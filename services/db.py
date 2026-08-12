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


# ── Users (authentication) ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Text, primary_key=True, default=_uid)
    username      = Column(Text, unique=True, nullable=False)
    full_name     = Column(Text)
    email         = Column(Text)
    organisation  = Column(Text)
    password_hash = Column(Text, nullable=False)   # pbkdf2_sha256$<iters>$<salt>$<hash>
    role          = Column(Text, default="Analyst")  # see auth.ALL_ROLES
    active        = Column(Integer, default=1)     # 0 = disabled, cannot sign in
    created_at    = Column(Text, default=_now)
    last_login_at = Column(Text)


# ── IRM project worklist ──────────────────────────────────────────────────────

class IRMProject(Base):
    """
    A National Treasury IRM project record awaiting location verification.

    irm_latitude / irm_longitude hold the ORIGINAL coordinates as supplied by IRM
    (often blank or a town centroid) — they are never overwritten, so the
    "IRM said X, we corrected it to Y" trail stays provable.
    """
    __tablename__ = "irm_projects"

    id             = Column(Text, primary_key=True, default=_uid)
    project_code   = Column(Text, unique=True, nullable=False)
    project_name   = Column(Text, nullable=False)
    department     = Column(Text)
    municipality   = Column(Text)
    province       = Column(Text, default="Gauteng")
    irm_latitude   = Column(Float)     # nullable — blank is the normal case
    irm_longitude  = Column(Float)
    irm_address    = Column(Text)      # free-text location string from IRM
    budget_rands   = Column(Float)
    financial_year = Column(Text)
    source_file    = Column(Text)
    imported_by    = Column(Text)
    imported_at    = Column(Text, default=_now)


# ── Location captures ─────────────────────────────────────────────────────────

class LocationCapture(Base):
    """
    One digitised geometry for an IRM project. A project may hold many captures;
    exactly one carries is_primary=1 and supplies the lat/lon exported to STAM.
    """
    __tablename__ = "location_captures"

    id                  = Column(Text, primary_key=True, default=_uid)
    project_code        = Column(Text, nullable=False)
    project_name        = Column(Text)
    irm_project_id      = Column(Text)    # IRMProject.id; NULL = ad-hoc entry
    geometry_type       = Column(Text)    # Point | LineString | Polygon
    geometry_geojson    = Column(Text)    # JSON geometry object
    centroid_lat        = Column(Float)
    centroid_lon        = Column(Float)
    length_m            = Column(Float)   # LineString only
    area_m2             = Column(Float)   # Polygon only
    is_primary          = Column(Integer, default=0)
    verification_status = Column(Text)
    lifecycle_status    = Column(Text)
    comments            = Column(Text)
    search_query        = Column(Text)    # what the user searched to find the place
    captured_by         = Column(Text)
    deleted             = Column(Integer, default=0)   # soft delete
    created_at          = Column(Text, default=_now)
    updated_at          = Column(Text, default=_now, onupdate=_now)


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

# One engine (and therefore one connection pool) per database file. Pages call
# get_session() many times per render; building a fresh engine each time meant a
# new pool and a fresh connection every call.
_ENGINES: dict[str, object] = {}


def get_engine(db_path: str | None = None):
    path = os.path.abspath(db_path or DB_PATH)
    engine = _ENGINES.get(path)
    if engine is not None:
        return engine

    os.makedirs(os.path.dirname(path), exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    _ENGINES[path] = engine
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
