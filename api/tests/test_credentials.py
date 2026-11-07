import hashlib
import json
import logging
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import manage_keys
from app.config import Settings
from app.credential_contracts import CredentialCreate
from app.main import create_app
from app.persistence.credential_repository import CredentialRepository
from app.persistence.errors import RepositoryError
from app.persistence.storage_models import CredentialRow
from test_project_generation import fake_provider, prompt_parts
from test_projects import BASE, project_database, sample


CHAT = "/api/v1/conversations"
INSTRUCTION = {"instruction": "An imaginary neighborhood experiment"}


def authorization(issued: dict) -> dict[str, str]:
    return {"Authorization": "Bearer " + issued["api_key"]}


@pytest.fixture
def secured_api(project_database):
    repository = CredentialRepository(project_database)
    admin = repository.issue_admin(CredentialCreate(name="Test administrator")).model_dump(mode="json")
    provider = fake_provider()
    importer = Mock(side_effect=AssertionError("Security tests must not import external content."))

    @contextmanager
    def open_client(local_demo=False, peer="127.0.0.1"):
        location = {"database_path": project_database.path} if project_database.path is not None else {"database_url": project_database.url.render_as_string(hide_password=False)}
        application = create_app(Settings(**location, local_demo=local_demo), provider=provider, importer_factory=importer)
        with TestClient(application, base_url="http://127.0.0.1", client=(peer, 50000)) as client:
            yield client

    yield open_client, admin, provider, repository
    importer.assert_not_called()


def provision(client: TestClient, admin: dict, project_id: str = "wowiki", **fields: object) -> tuple[dict, dict]:
    response = client.post(BASE, headers=authorization(admin), json={"project_id": project_id, "display_name": project_id, **fields})
    assert response.status_code == 201
    key = client.post(BASE + "/" + project_id + "/keys", headers=authorization(admin), json={"name": project_id + " integration"})
    assert key.status_code == 201
    assert key.headers["cache-control"] == "no-store"
    return response.json(), key.json()


def assert_denied(response, status: int, expected: dict | None = None) -> dict:
    assert response.status_code == status
    payload = response.json()
    assert payload["error"]["code"] == ("unauthorized" if status == 401 else "access_denied")
    assert payload["error"]["details"] == []
    assert payload["conversation_id"] is None and payload["message_id"] is None
    assert response.headers["cache-control"] == "no-store"
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    if expected is not None:
        assert payload == expected
    return payload


def test_missing_malformed_forged_and_revoked_keys_fail_closed(secured_api) -> None:
    open_client, admin, provider, repository = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
        repository.revoke(UUID(key["credential_id"]))
        unknown = repository.issue_admin(CredentialCreate(name="Unknown after revocation")).model_dump(mode="json")
        repository.revoke(UUID(unknown["credential_id"]))
        forged = key["api_key"][:-1] + ("A" if key["api_key"][-1] != "A" else "B")
        expected = assert_denied(client.get(BASE), 401)
        for headers in [{}, {"Authorization": "Bearer invalid"}, {"Authorization": "Basic " + admin["api_key"]}, authorization(key), authorization(unknown), {"Authorization": f"Bearer fableai_{uuid4().hex}_{secrets.token_urlsafe(32)}"}, {"Authorization": "Bearer " + forged}, [("Authorization", "Bearer " + admin["api_key"]), ("Authorization", "Bearer " + admin["api_key"])], {"Authorization": "Bearer " + "x" * 8192}]:
            for method, url, body in [("GET", BASE + "/wowiki", None), ("GET", BASE + "/absent", None), ("POST", BASE + "/wowiki/generations", INSTRUCTION), ("POST", CHAT, None)]:
                assert_denied(client.request(method, url, headers=headers, json=body), 401, expected)
        assert client.get(BASE + "/wowiki", params={"api_key": admin["api_key"]}).status_code == 401
        assert client.get(BASE + "/wowiki", headers={"Authorization": "bEaReR " + admin["api_key"]}).status_code == 200
        assert client.post(BASE, content="{", headers={"Content-Type": "application/json"}).status_code == 401
        provider.generate.assert_not_called()


def test_project_keys_use_only_their_own_settings_references_and_replay_results(secured_api) -> None:
    open_client, admin, provider, _ = secured_api
    with open_client() as client:
        first, first_key = provision(client, admin, generation_settings={"category": "news", "count": 2}, references=[sample("Original paper harbor sample.")])
        other, other_key = provision(client, admin, "club", generation_settings={"category": "blog", "count": 1}, references=[sample("An independent original sample for a fictional club.")])
        assert client.get(BASE + "/wowiki", headers=authorization(first_key)).json() == first
        assert client.get(BASE + "/wowiki/output-contract", headers=authorization(first_key)).status_code == 200
        updated = client.patch(BASE + "/wowiki", headers=authorization(first_key), json={"instructions": ["Keep paragraphs compact."], "references": [sample("A replacement sample about original paper shapes.")]}).json()
        assert updated["instructions"] == ["Keep paragraphs compact."]
        expected = None
        replay_id = str(uuid4())
        for foreign in ("club", "absent"):
            for method, path, body in [("GET", "", None), ("PATCH", "", {"references": [], "generation_settings": {"count": 20}}), ("GET", "/output-contract", None), ("POST", "/generations", INSTRUCTION)]:
                response = client.request(method, BASE + "/" + foreign + path, headers={**authorization(first_key), "Idempotency-Key": replay_id}, json=body)
                expected = assert_denied(response, 403, expected)
        assert client.get(BASE + "/club", headers=authorization(other_key)).json() == other
        results = []
        for project_id, key in [("wowiki", first_key), ("club", other_key)]:
            response = client.post(BASE + "/" + project_id + "/generations", headers={**authorization(key), "Idempotency-Key": replay_id}, json=INSTRUCTION)
            assert response.status_code == 200
            assert response.headers["idempotency-replayed"] == "false"
            results.append(response.json())
        assert [len(result["entries"]) for result in results] == [2, 1]
        assert [result["entries"][0]["category"] for result in results] == ["news", "blog"]
        first_prompt, second_prompt = [prompt_parts(call.args[0]) for call in provider.generate.await_args_list]
        assert first_prompt[1]["stable_instructions"] == updated["instructions"]
        assert second_prompt[1]["stable_instructions"] == []
        assert first_prompt[0]["reference_patterns"] != second_prompt[0]["reference_patterns"]
        assert_denied(client.post(BASE + "/club/generations", headers={**authorization(first_key), "Idempotency-Key": replay_id}, json=INSTRUCTION), 403, expected)
        assert client.post(BASE + "/wowiki/generations", headers={**authorization(first_key), "Idempotency-Key": replay_id}, json=INSTRUCTION).json() == results[0]
        assert provider.generate.await_count == 2


def test_project_keys_cannot_administer_keys_or_read_modify_legacy_history(secured_api) -> None:
    open_client, admin, provider, _ = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
        _, foreign_key = provision(client, admin, "club")
        conversation = client.post(CHAT, headers=authorization(admin)).json()
        path = CHAT + "/" + conversation["conversation_id"]
        entry = path + "/entries/" + str(uuid4())
        requests = [("GET", BASE, None), ("POST", BASE, {"project_id": "new", "display_name": "New"}), ("GET", CHAT, None), ("POST", CHAT, None), ("GET", path, None), ("DELETE", path, None), ("GET", path + "/messages", None), ("POST", path + "/messages", INSTRUCTION), ("POST", path + "/references/import", {}), ("PATCH", entry, {}), ("GET", entry + "/versions", None), ("GET", entry + "/reviews", None)]
        for project_id, issued in [("wowiki", key), ("club", foreign_key)]:
            keys = BASE + "/" + project_id + "/keys"
            requests.extend([("GET", keys, None), ("POST", keys, {"name": "Unauthorized"}), ("DELETE", keys + "/" + issued["credential_id"], None), ("POST", keys + "/" + issued["credential_id"] + "/rotate", None)])
        expected = None
        for method, url, body in requests:
            expected = assert_denied(client.request(method, url, headers=authorization(key), json=body), 403, expected)
        assert client.get(path, headers=authorization(admin)).json() == conversation
        assert len(client.get(BASE, headers=authorization(admin)).json()["items"]) == 2
        assert client.get(BASE + "/wowiki", headers=authorization(key)).status_code == 200
        provider.generate.assert_not_called()


def test_key_hashes_and_raw_secrets_are_never_returned_by_read_endpoints_or_logs(secured_api, project_database, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    open_client, admin, _, repository = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
        token = key["api_key"]
        expected_hash = hashlib.sha256(token.encode()).hexdigest()
        with Session(project_database.engine) as session:
            row = session.get(CredentialRow, key["credential_id"])
            assert row.key_hash == expected_hash
            assert token not in json.dumps({column.name: getattr(row, column.name) for column in row.__table__.columns})
        summary = repository.list_project("wowiki", 20, 0).model_dump_json()
        for url in (BASE, BASE + "/wowiki", BASE + "/wowiki/keys", "/openapi.json", "/health"):
            response = client.get(url, headers=authorization(admin))
            assert response.status_code == 200
            assert all(secret not in response.text for secret in [token, admin["api_key"], expected_hash])
        assert "api_key" not in summary and "key_hash" not in summary
        issued = repository.issue_project("wowiki", CredentialCreate(name="Second key"))
        assert issued.api_key.get_secret_value() not in repr(issued)
        assert issued.api_key.get_secret_value() not in str(issued.model_dump())
        assert token not in caplog.text and admin["api_key"] not in caplog.text and expected_hash not in caplog.text


def test_rotation_and_revocation_apply_across_instances_restarts_and_cached_results(secured_api) -> None:
    open_client, admin, provider, _ = secured_api
    with open_client() as first, open_client() as second:
        _, key = provision(first, admin)
        _, peer = provision(first, admin, "club")
        key_path = BASE + "/wowiki/keys/" + key["credential_id"]
        generation_headers = {**authorization(key), "Idempotency-Key": str(uuid4())}
        result = second.post(BASE + "/wowiki/generations", headers=generation_headers, json=INSTRUCTION)
        assert result.status_code == 200
        rotated = first.post(key_path + "/rotate", headers=authorization(admin))
        assert rotated.status_code == 201
        replacement = rotated.json()
        assert replacement["api_key"] != key["api_key"] and replacement["credential_id"] != key["credential_id"]
        assert_denied(second.get(BASE + "/wowiki", headers=authorization(key)), 401)
        assert_denied(second.post(BASE + "/wowiki/generations", headers=generation_headers, json=INSTRUCTION), 401)
        replay = second.post(BASE + "/wowiki/generations", headers={**generation_headers, **authorization(replacement)}, json=INSTRUCTION)
        assert replay.json() == result.json() and replay.headers["idempotency-replayed"] == "true"
        assert provider.generate.await_count == 1
        assert first.post(key_path + "/rotate", headers=authorization(admin)).status_code == 409
        assert first.delete(key_path, headers=authorization(admin)).status_code == 204
        items = first.get(BASE + "/wowiki/keys", headers=authorization(admin)).json()["items"]
        assert len(items) == 2 and sum(item["revoked_at"] is not None for item in items) == 1
    with open_client() as restarted:
        assert_denied(restarted.get(BASE + "/wowiki", headers=authorization(key)), 401)
        assert restarted.get(BASE + "/wowiki", headers=authorization(replacement)).status_code == 200
        for _ in range(2):
            assert restarted.delete(BASE + "/wowiki/keys/" + replacement["credential_id"], headers=authorization(admin)).status_code == 204
        assert_denied(restarted.get(BASE + "/wowiki", headers=authorization(replacement)), 401)
        assert restarted.get(BASE + "/club", headers=authorization(peer)).status_code == 200


def test_key_management_validation_pagination_and_foreign_ids(secured_api) -> None:
    open_client, admin, _, _ = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
        _, other = provision(client, admin, "club")
        path = BASE + "/wowiki/keys"
        for body in [{"name": ""}, {"name": " "}, {"name": "x" * 101}, {"name": "Escalate", "is_admin": True}, {"name": "Move", "project_id": "club"}, {"name": "Replace", "api_key": "supplied-value"}]:
            assert client.post(path, headers=authorization(admin), json=body).status_code == 422
        assert client.post(BASE + "/absent/keys", headers=authorization(admin), json={"name": "Missing"}).status_code == 404
        for foreign_id in (other["credential_id"], admin["credential_id"], str(uuid4())):
            assert client.delete(path + "/" + foreign_id, headers=authorization(admin)).status_code == 404
            assert client.post(path + "/" + foreign_id + "/rotate", headers=authorization(admin)).status_code == 404
        second = client.post(path, headers=authorization(admin), json={"name": "Another integration"})
        assert second.status_code == 201
        page = client.get(path, params={"limit": 1}, headers=authorization(admin)).json()
        next_page = client.get(path, params={"limit": 1, "offset": page["next_offset"]}, headers=authorization(admin)).json()
        assert {item["credential_id"] for item in page["items"] + next_page["items"]} == {key["credential_id"], second.json()["credential_id"]}
        assert next_page["next_offset"] is None
        for query in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
            assert client.get(path, params=query, headers=authorization(admin)).status_code == 422
        assert client.get(BASE + "/club", headers=authorization(other)).status_code == 200


def test_concurrent_rotation_issues_exactly_one_replacement(secured_api, project_database) -> None:
    open_client, admin, _, repository = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
    barrier = Barrier(2)

    def rotate():
        barrier.wait(timeout=5)
        try:
            return repository.rotate(UUID(key["credential_id"]), "wowiki")
        except RepositoryError as error:
            return error.error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate(), range(2)))
    assert results.count("credential_revoked") == 1
    replacement = next(result for result in results if result != "credential_revoked")
    assert repository.authenticate(key["api_key"]) is None
    assert repository.authenticate(replacement.api_key.get_secret_value()).project_id == "wowiki"
    with Session(project_database.engine) as session:
        assert len(session.scalars(select(CredentialRow).where(CredentialRow.project_id == "wowiki", CredentialRow.revoked_at.is_(None))).all()) == 1


def test_failed_rotation_rolls_back_revocation_and_partial_issue(secured_api, monkeypatch) -> None:
    open_client, admin, _, repository = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
    original = repository._issue

    def fail_after_insert(*args):
        original(*args)
        raise SQLAlchemyError("Injected transaction failure")

    monkeypatch.setattr(repository, "_issue", fail_after_insert)
    with pytest.raises(SQLAlchemyError):
        repository.rotate(UUID(key["credential_id"]), "wowiki")
    assert repository.authenticate(key["api_key"]).project_id == "wowiki"
    assert len(repository.list_project("wowiki", 20, 0).items) == 1


def test_authentication_storage_failure_is_safe_and_never_starts_generation(secured_api, monkeypatch, caplog) -> None:
    open_client, admin, provider, _ = secured_api
    with open_client() as client:
        _, key = provision(client, admin)
        failure = Mock(side_effect=SQLAlchemyError("Private backend failure " + key["api_key"]))
        monkeypatch.setattr(client.app.state.credentials, "authenticate", failure)
        response = client.post(BASE + "/wowiki/generations", headers=authorization(key), json=INSTRUCTION)
        assert response.status_code == 503 and response.json()["error"]["code"] == "storage_failed"
        assert key["api_key"] not in response.text + caplog.text
        provider.generate.assert_not_called()


def test_demo_requires_explicit_opt_in_and_never_bypasses_project_security(secured_api) -> None:
    open_client, admin, _, _ = secured_api
    with open_client() as production:
        _, key = provision(production, admin)
        assert_denied(production.get(CHAT), 401)
        assert production.get("/health").status_code == 200
    with open_client(local_demo=True) as demo:
        assert demo.post(CHAT).status_code == 201
        assert_denied(demo.get(BASE), 401)
        assert_denied(demo.get(BASE + "/wowiki"), 401)
        assert_denied(demo.get(CHAT, headers=authorization(key)), 403)
        assert_denied(demo.get(CHAT, headers={"Authorization": "Bearer invalid"}), 401)
    with open_client(local_demo=True, peer="192.0.2.1") as remote:
        assert_denied(remote.get(CHAT), 403)
        assert_denied(remote.get(BASE, headers=authorization(admin)), 403)


def test_local_development_key_authenticates_only_conversations(secured_api, project_database) -> None:
    _, _, provider, _ = secured_api
    key = "test-local-development-key"
    location = {"database_path": project_database.path} if project_database.path is not None else {"database_url": project_database.url.render_as_string(hide_password=False)}
    with TestClient(create_app(Settings(**location, local_development_key=key), provider=provider), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert_denied(client.get(CHAT), 401)
        assert client.get(CHAT, headers={"Authorization": "Bearer " + key}).status_code == 200
        assert client.post(CHAT, headers={"Authorization": "Bearer " + key}).status_code == 201
        assert_denied(client.get(BASE, headers={"Authorization": "Bearer " + key}), 401)
        assert_denied(client.get(CHAT, headers={"Authorization": "Bearer " + key + "x"}), 401)
        assert provider.generate.await_count == 0
    with TestClient(create_app(Settings(**location, local_development_key=key), provider=provider), base_url="http://127.0.0.1", client=("192.0.2.1", 50000)) as remote:
        assert remote.get(CHAT, headers={"Authorization": "Bearer " + key}).status_code == 403


def test_openapi_requires_bearer_authentication_on_all_production_api_routes(secured_api) -> None:
    open_client, _, _, _ = secured_api
    with open_client() as client:
        schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["FableAIKey"] == {"type": "http", "scheme": "bearer"}
    for path, methods in schema["paths"].items():
        if path.startswith("/api/"):
            for operation in methods.values():
                assert operation["security"] == [{"FableAIKey": []}]
                assert "401" in operation["responses"] and "403" in operation["responses"]


def test_local_cli_bootstraps_rotates_and_revokes_admin_without_provider_use(secured_api, project_database, monkeypatch, capsys) -> None:
    _, _, provider, repository = secured_api
    location = {"database_path": project_database.path} if project_database.path is not None else {"database_url": project_database.url.render_as_string(hide_password=False)}
    monkeypatch.setattr(manage_keys, "load_settings", lambda: Settings(**location))
    assert manage_keys.main(["issue-admin", "--name", "Local operator"]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    issued = json.loads(output.out)
    assert repository.authenticate(issued["api_key"]).is_admin is True
    assert manage_keys.main(["rotate", issued["credential_id"]]) == 0
    replacement = json.loads(capsys.readouterr().out)
    assert repository.authenticate(issued["api_key"]) is None
    assert repository.authenticate(replacement["api_key"]).is_admin is True
    assert manage_keys.main(["revoke", replacement["credential_id"]]) == 0
    assert "api_key" not in capsys.readouterr().out
    assert repository.authenticate(replacement["api_key"]) is None
    assert manage_keys.main(["rotate", replacement["credential_id"]]) == 1
    failure = capsys.readouterr()
    assert failure.out == "" and replacement["api_key"] not in failure.err
    provider.generate.assert_not_called()



def test_cli_failures_never_echo_a_secret_supplied_instead_of_a_credential_id(secured_api, project_database, monkeypatch, capsys) -> None:
    _, admin, _, _ = secured_api
    location = {"database_path": project_database.path} if project_database.path is not None else {"database_url": project_database.url.render_as_string(hide_password=False)}
    monkeypatch.setattr(manage_keys, "load_settings", lambda: Settings(**location))
    assert manage_keys.main(["revoke", admin["api_key"]]) == 1
    failure = capsys.readouterr()
    assert failure.out == "" and admin["api_key"] not in failure.err
    assert "Credential management failed" in failure.err
