from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = sessionmaker(expire_on_commit=False)


def init_engine(url: str | None = None):
    """建立資料庫連線；測試時可傳入其他 URL。"""
    global engine
    url = url or settings.db_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _pragma(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    SessionLocal.configure(bind=engine)
    from . import models  # noqa: F401  確保模型已註冊

    Base.metadata.create_all(engine)
    return engine


def get_engine():
    if engine is None:
        init_engine()
    return engine


@contextmanager
def session_scope():
    get_engine()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
