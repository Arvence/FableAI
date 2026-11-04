from app.credential_contracts import CredentialCreate
from app.persistence.credential_repository import CredentialRepository
from app.persistence.database import Database


def administrator_headers(database: Database) -> dict[str, str]:
    issued = CredentialRepository(database).issue_admin(CredentialCreate(name="Isolated test administrator"))
    return {"Authorization": "Bearer " + issued.api_key.get_secret_value()}
