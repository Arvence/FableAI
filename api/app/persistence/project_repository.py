from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.database import Database
from app.persistence.errors import RepositoryError
from app.persistence.storage_models import ProjectReferenceRow, ProjectRow
from app.project_contracts import ProjectConfiguration, ProjectCreate, ProjectDetail, ProjectPage, ProjectSummary, ProjectUpdate


class ProjectRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.database = database
        self._clock = clock

    @contextmanager
    def _write(self) -> Iterator[Session]:
        with self.database.engine.connect().execution_options(write_transaction=True) as connection, Session(bind=connection) as session, session.begin():
            yield session

    @staticmethod
    def _project(session: Session, project_id: str, *, write: bool = False) -> ProjectRow:
        row = session.get(ProjectRow, project_id, with_for_update=True if write else {"read": True})
        if row is None:
            raise RepositoryError("project_not_found", "The project does not exist.")
        return row

    @staticmethod
    def _summary(row: ProjectRow) -> ProjectSummary:
        return ProjectSummary(project_id=row.project_id, display_name=row.display_name, created_at=datetime.fromisoformat(row.created_at), updated_at=datetime.fromisoformat(row.updated_at))

    @classmethod
    def _detail(cls, session: Session, row: ProjectRow) -> ProjectDetail:
        references = session.scalars(select(ProjectReferenceRow).where(ProjectReferenceRow.project_id == row.project_id).order_by(ProjectReferenceRow.position)).all()
        return ProjectDetail(**cls._summary(row).model_dump(), generation_settings=row.generation_settings, instructions=row.instructions, references=[reference.content for reference in references])

    @staticmethod
    def _save_references(session: Session, project_id: str, configuration: ProjectConfiguration) -> None:
        session.execute(delete(ProjectReferenceRow).where(ProjectReferenceRow.project_id == project_id))
        session.add_all([ProjectReferenceRow(project_id=project_id, id=str(reference.id), kind="examples", position=position, content=reference.model_dump(mode="json")) for position, reference in enumerate(configuration.references)])
        session.flush()

    def create(self, request: ProjectCreate) -> ProjectDetail:
        try:
            with self._write() as session:
                if session.get(ProjectRow, request.project_id) is not None:
                    raise RepositoryError("project_conflict", "A project with this ID already exists.")
                timestamp = self._clock().isoformat()
                row = ProjectRow(project_id=request.project_id, display_name=request.display_name, created_at=timestamp, updated_at=timestamp, generation_settings=request.generation_settings.model_dump(mode="json"), instructions=request.instructions)
                session.add(row)
                session.flush()
                self._save_references(session, row.project_id, request)
                return self._detail(session, row)
        except IntegrityError:
            with Session(self.database.engine) as session:
                if session.get(ProjectRow, request.project_id) is not None:
                    raise RepositoryError("project_conflict", "A project with this ID already exists.") from None
            raise

    def get(self, project_id: str) -> ProjectDetail:
        with Session(self.database.engine) as session:
            return self._detail(session, self._project(session, project_id))

    def list_projects(self, limit: int, offset: int) -> ProjectPage:
        with Session(self.database.engine) as session:
            rows = session.scalars(select(ProjectRow).order_by(ProjectRow.created_at.desc(), ProjectRow.project_id).offset(offset).limit(limit + 1)).all()
            return ProjectPage(items=[self._summary(row) for row in rows[:limit]], next_offset=offset + limit if len(rows) > limit else None)

    def update(self, project_id: str, request: ProjectUpdate) -> ProjectDetail:
        with self._write() as session:
            row = self._project(session, project_id, write=True)
            current = self._detail(session, row)
            changes = request.model_dump(mode="json", exclude_unset=True)
            if "generation_settings" in changes:
                changes["generation_settings"] = {**current.generation_settings.model_dump(mode="json"), **changes["generation_settings"]}
            try:
                updated = ProjectConfiguration.model_validate({**current.model_dump(include=set(ProjectConfiguration.model_fields)), **changes})
            except ValidationError:
                raise RepositoryError("invalid_request", "The updated project settings, instructions, or references exceed supported limits.") from None
            row.display_name = updated.display_name
            row.generation_settings = updated.generation_settings.model_dump(mode="json")
            row.instructions = updated.instructions
            row.updated_at = self._clock().isoformat()
            if "references" in changes:
                self._save_references(session, project_id, updated)
            session.flush()
            return self._detail(session, row)
