"""The app's database, read only. The experiments read the tables they need of it, as they need them, with queries of
their own: they never import the app, nor keep a copy of its schema, nor write to it."""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import make_url


class NotReadOnly(Exception):
    """The connection to the app's database could write to it."""


def connect(url: str) -> Engine:
    """an engine whose every connection can only read the app's database"""
    parsed = make_url(url)
    if parsed.get_backend_name() == "postgresql":
        engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    elif parsed.get_backend_name() == "sqlite":
        # the file opened read only by SQLite itself
        engine = create_engine(f"sqlite:///file:{parsed.database}?mode=ro&uri=true")

        @event.listens_for(engine, "connect")
        def _query_only(connection, _):
            connection.execute("PRAGMA query_only = ON")
    else:
        raise NotReadOnly(f"the experiments can't read {parsed.get_backend_name()} read only")
    return engine


def read_only(engine: Engine) -> bool:
    """whether the database refuses to be written to on the engine's connections"""
    with engine.connect() as connection:
        if engine.dialect.name == "postgresql":
            return connection.execute(text("SHOW transaction_read_only")).scalar() == "on"
        return connection.execute(text("PRAGMA query_only")).scalar() == 1


def fingerprint(engine: Engine, tables: list[str]) -> dict[str, int]:
    """how many rows each table has, to tell the database didn't change"""
    with engine.connect() as connection:
        return {table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar() for table in tables}


@dataclass(frozen=True)
class QuizRequest:
    """a quiz a student asked for in the app: what it was written from, how many questions, and the prompt's version"""

    id: str
    student: str
    course_id: str
    count: int
    # the sections it was written from: document id, filename, section (its position) and title
    sources: list[dict]
    # e.g. quiz@1
    prompt: str | None


def quiz_requests(engine: Engine, student: str) -> list[QuizRequest]:
    """the student's quizzes, oldest first"""
    query = text(
        "SELECT id, student, course_id, count, sources, prompt FROM quizzes"
        " WHERE student = :student ORDER BY created_at"
    )
    with engine.connect() as connection:
        rows = connection.execute(query, {"student": student}).all()
    return [
        QuizRequest(
            id=row.id,
            student=row.student,
            course_id=row.course_id,
            count=row.count,
            sources=_json(row.sources),
            prompt=row.prompt,
        )
        for row in rows
    ]


def rated_down_quizzes(engine: Engine, student: str) -> set[str]:
    """the quizzes the assistant wrote in a conversation that the student rated down"""
    query = text(
        "SELECT t.quiz_id FROM answer_ratings r JOIN conversation_turns t ON t.id = r.turn_id"
        " WHERE r.student = :student AND r.rating = 'down' AND t.quiz_id IS NOT NULL"
    )
    with engine.connect() as connection:
        return {row.quiz_id for row in connection.execute(query, {"student": student})}


def section_texts(engine: Engine, document_id: str) -> dict[int, str]:
    """the text of each section of a material, by its position, as the app has it now"""
    query = text("SELECT position, content FROM platform_sections WHERE document_id = :document_id")
    with engine.connect() as connection:
        return {row.position: row.content for row in connection.execute(query, {"document_id": document_id})}


def readers(engine: Engine, document_id: str) -> set[str]:
    """who may read a material: the owner of the course it is in, and the members it is shared with"""
    query = text(
        "SELECT i.owner AS student FROM platform_documents d JOIN platform_indexes i ON i.name = d.index_name"
        " WHERE d.id = :document_id"
        " UNION SELECT m.member FROM platform_documents d JOIN platform_index_members m ON m.index_name = d.index_name"
        " WHERE d.id = :document_id"
    )
    with engine.connect() as connection:
        return {row.student for row in connection.execute(query, {"document_id": document_id})}


def _json(value):
    import json

    return json.loads(value) if isinstance(value, str) else value
