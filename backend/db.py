from sqlmodel import SQLModel, Field, create_engine, Session
from datetime import datetime
from typing import Optional


class Task(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    est_minutes: int = 45
    priority: int = 2
    status: str = "todo"
    created_at: datetime = Field(default_factory=datetime.utcnow)


engine = create_engine("sqlite:///data.db",
                       connect_args={"check_same_thread": False})


def init_db():
    SQLModel.metadata.create_all(engine)


def get_session():
    return Session(engine)
