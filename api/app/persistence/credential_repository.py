import hashlib
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.credential_contracts import CredentialCreate, CredentialIssued, CredentialPage, CredentialSummary, Principal
from app.persistence.database import Database
from app.persistence.errors import RepositoryError
from app.persistence.storage_models import CredentialRow, ProjectRow


TOKEN_PATTERN = re.compile(r"fableai_([0-9a-f]{32})_[A-Za-z0-9_-]{43}")


class CredentialRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.database = database
        self._clock = clock

    @contextmanager
    def _write(self) -> Iterator[Session]:
        with self.database.engine.connect().execution_options(write_transaction=True) as connection, Session(bind=connection) as session, session.begin():
            yield session

    @staticmethod
    def _metadata(row: CredentialRow) -> CredentialSummary:
        return CredentialSummary(credential_id=row.id, name=row.name, kind="administrator" if row.is_admin else "project", project_id=row.project_id, created_at=datetime.fromisoformat(row.created_at), revoked_at=datetime.fromisoformat(row.revoked_at) if row.revoked_at else None)

    @staticmethod
    def _project(session: Session, project_id: str) -> None:
        if session.get(ProjectRow, project_id) is None:
            raise RepositoryError("project_not_found", "The project does not exist.")

    @staticmethod
    def _credential(session: Session, credential_id: UUID, project_id: str | None) -> CredentialRow:
        row = session.get(CredentialRow, str(credential_id), with_for_update=True)
        if row is None or (project_id is not None and row.project_id != project_id):
            raise RepositoryError("credential_not_found", "The credential does not exist.")
        return row

    def _issue(self, session: Session, name: str, project_id: str | None) -> CredentialIssued:
        credential_id = uuid4()
        token = f"fableai_{credential_id.hex}_{secrets.token_urlsafe(32)}"
        row = CredentialRow(id=str(credential_id), name=name, project_id=project_id, is_admin=project_id is None, key_hash=hashlib.sha256(token.encode()).hexdigest(), created_at=self._clock().isoformat())
        session.add(row)
        session.flush()
        return CredentialIssued(**self._metadata(row).model_dump(), api_key=token)

    def issue_admin(self, request: CredentialCreate) -> CredentialIssued:
        with self._write() as session:
            return self._issue(session, request.name, None)

    def issue_project(self, project_id: str, request: CredentialCreate) -> CredentialIssued:
        with self._write() as session:
            self._project(session, project_id)
            return self._issue(session, request.name, project_id)

    def list_project(self, project_id: str, limit: int, offset: int) -> CredentialPage:
        with Session(self.database.engine) as session:
            self._project(session, project_id)
            rows = session.scalars(select(CredentialRow).where(CredentialRow.project_id == project_id).order_by(CredentialRow.created_at.desc(), CredentialRow.id).offset(offset).limit(limit + 1)).all()
            return CredentialPage(items=[self._metadata(row) for row in rows[:limit]], next_offset=offset + limit if len(rows) > limit else None)

    def revoke(self, credential_id: UUID, project_id: str | None = None) -> None:
        with self._write() as session:
            row = self._credential(session, credential_id, project_id)
            if row.revoked_at is None:
                row.revoked_at = self._clock().isoformat()

    def rotate(self, credential_id: UUID, project_id: str | None = None) -> CredentialIssued:
        with self._write() as session:
            row = self._credential(session, credential_id, project_id)
            if row.revoked_at is not None:
                raise RepositoryError("credential_revoked", "The credential has already been revoked.")
            row.revoked_at = self._clock().isoformat()
            return self._issue(session, row.name, row.project_id)

    def authenticate(self, token: str) -> Principal | None:
        match = TOKEN_PATTERN.fullmatch(token)
        if match is None:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with Session(self.database.engine) as session:
            row = session.get(CredentialRow, str(UUID(hex=match[1])))
            valid = secrets.compare_digest(digest, row.key_hash if row is not None else "0" * 64)
            if not valid or row is None or row.revoked_at is not None:
                return None
            return Principal(credential_id=UUID(row.id), project_id=row.project_id, is_admin=row.is_admin)
