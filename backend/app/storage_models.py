from sqlalchemy import JSON, CheckConstraint, ForeignKey, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ConversationRow(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40), index=True)
    updated_at: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(default=0)
    message_sequence: Mapped[int] = mapped_column(default=0)
    preferences: Mapped[dict] = mapped_column(JSON, default=dict)
    stable_instructions: Mapped[list] = mapped_column(JSON, default=list)
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SubmissionRow(Base):
    __tablename__ = "submissions"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    operation: Mapped[str] = mapped_column(String(20))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    http_status: Mapped[int | None]
    started_at: Mapped[str] = mapped_column(String(40))
    expires_at: Mapped[float] = mapped_column(index=True)
    __table_args__ = (CheckConstraint("status IN ('processing', 'completed')"), CheckConstraint("operation IN ('message', 'import')"))


class MessageRow(Base):
    __tablename__ = "chat_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    submission_id: Mapped[str] = mapped_column(String(36))
    sequence: Mapped[int]
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    __table_args__ = (UniqueConstraint("conversation_id", "sequence"), CheckConstraint("role IN ('user', 'assistant')"), ForeignKeyConstraint(["conversation_id", "submission_id"], ["submissions.conversation_id", "submissions.id"]))


class ReferenceRow(Base):
    __tablename__ = "reference_material"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    position: Mapped[int]
    import_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (CheckConstraint("kind IN ('examples', 'headlines', 'urls')"),)


class EntryRow(Base):
    __tablename__ = "generated_entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    position: Mapped[int]
    current_version: Mapped[int]
    status: Mapped[str] = mapped_column(String(10), default="draft")
    __table_args__ = (UniqueConstraint("conversation_id", "position"), CheckConstraint("current_version >= 1"), CheckConstraint("status IN ('draft', 'accepted', 'rejected')"))


class EntryVersionRow(Base):
    __tablename__ = "entry_versions"
    entry_id: Mapped[str] = mapped_column(ForeignKey("generated_entries.id"), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    __table_args__ = (CheckConstraint("version >= 1"),)


class ReviewRow(Base):
    __tablename__ = "entry_reviews"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[str] = mapped_column(String(40))
    __table_args__ = (ForeignKeyConstraint(["entry_id", "version"], ["entry_versions.entry_id", "entry_versions.version"]), CheckConstraint("status IN ('accepted', 'rejected')"))
