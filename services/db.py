"""
SQLAlchemy models and database session management for STAM.
"""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Text, Float, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(_HERE, "..", "data", "stam.db")


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


# ── Projects ──────────────────────────────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id                  = Column(Text, primary_key=True, default=_uid)
    project_id          = Column(Text, unique=True, nullable=False)   # e.g. P001
    name                = Column(Text, nullable=False)
    department          = Column(Text)
    project_type        = Column(Text)   # clinic|school|library|community|housing|road|industrial|commercial
    latitude            = Column(Float)
    longitude           = Column(Float)
    budget_rands        = Column(Float)
    budget_year         = Column(Text)   # e.g. "2026/27"
    readiness_status    = Column(Text)   # Ready|Design|Planning|Concept
    municipality        = Column(Text)
    ward                = Column(Text)
    gsdf_classification = Column(Text)   # Priority|Accommodate|Discourage|Outside
    total_score         = Column(Integer)
    score_breakdown     = Column(Text, default="{}")   # JSON
    classification      = Column(Text)   # Priority Now|Priority Next Cycle|Conditional|Not Recommended
    source_file         = Column(Text)   # which import file this came from
    created_at          = Column(Text, default=_now)
    updated_at          = Column(Text, default=_now, onupdate=_now)


# ── Facilities ────────────────────────────────────────────────────────────────

class Facility(Base):
    __tablename__ = "facilities"

    id               = Column(Text, primary_key=True, default=_uid)
    name             = Column(Text, nullable=False)
    facility_type    = Column(Text)   # clinic|school|library|community_hall|road
    latitude         = Column(Float)
    longitude        = Column(Float)
    capacity         = Column(Integer)
    current_occupancy = Column(Integer)
    municipality     = Column(Text)
    ward             = Column(Text)
    created_at       = Column(Text, default=_now)


# ── Score templates ───────────────────────────────────────────────────────────

class ScoreTemplate(Base):
    __tablename__ = "score_templates"

    id            = Column(Text, primary_key=True, default=_uid)
    template_name = Column(Text, nullable=False)
    weights       = Column(Text, default="{}")   # JSON: {gsdf_overlap: 20, ...}
    active        = Column(Integer, default=0)   # 1 = current default
    created_at    = Column(Text, default=_now)


# ── Saved queries ─────────────────────────────────────────────────────────────

class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id            = Column(Text, primary_key=True, default=_uid)
    query_name    = Column(Text, nullable=False)
    criteria      = Column(Text, default="{}")   # JSON
    results_count = Column(Integer)
    created_by    = Column(Text, default="Analyst")
    created_at    = Column(Text, default=_now)


# ── Audit log ─────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_log"

    id          = Column(Text, primary_key=True, default=_uid)
    user_role   = Column(Text, default="Analyst")
    action      = Column(Text, nullable=False)
    entity_type = Column(Text)
    entity_id   = Column(Text)
    detail      = Column(Text, default="{}")   # JSON
    timestamp   = Column(Text, default=_now)


# ── Engine / Session ──────────────────────────────────────────────────────────

def get_engine(db_path: str | None = None):
    path = db_path or os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(db_path: str | None = None):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine


def get_session(engine=None) -> Session:
    if engine is None:
        engine = get_engine()
    factory = sessionmaker(bind=engine)
    return factory()


def log_action(action: str, entity_type: str = "", entity_id: str = "",
               detail: dict | None = None, user_role: str = "Analyst"):
    """Write a row to audit_log. Swallows errors to avoid breaking UI flows."""
    import json
    try:
        session = get_session()
        entry = AuditLog(
            user_role=user_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=json.dumps(detail or {}),
        )
        session.add(entry)
        session.commit()
        session.close()
    except Exception:
        pass
