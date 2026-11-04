"""
SQLAlchemy database models and engine setup.

Uses SQLite for local development and PostgreSQL on Render (via DATABASE_URL env var).
"""

import json
import os
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ── Engine ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    # Render provides postgres:// but SQLAlchemy 2.x needs postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    # Explicitly use psycopg2 — SQLAlchemy 2.x otherwise tries psycopg3 first
    if DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    # Local dev: SQLite in the project root
    _db_path = os.path.join(os.path.dirname(__file__), "..", "doccheck.db")
    engine = create_engine(
        f"sqlite:///{os.path.abspath(_db_path)}",
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Models ────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True, index=True)
    email         = Column(String(255), unique=True, index=True, nullable=False)
    name          = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)


class CheckSession(Base):
    __tablename__ = "check_sessions"

    id              = Column(String(32), primary_key=True)
    user_id         = Column(Integer, index=True, nullable=False)
    date            = Column(String(32), nullable=True)
    summary         = Column(Text, default="{}")     # JSON
    results         = Column(Text, default="[]")     # JSON
    rules_text      = Column(Text, default="")
    rules_name      = Column(String(255), default="")
    applied_sources = Column(Text, default="[]")     # JSON


# ── Helpers ───────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables (safe to call repeatedly — only creates if missing)."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_to_dict(sess: CheckSession, email: str) -> dict:
    """Serialise a CheckSession ORM row to the shape the frontend expects."""
    return {
        "id":              sess.id,
        "email":           email,
        "date":            sess.date or "",
        "summary":         json.loads(sess.summary or "{}"),
        "results":         json.loads(sess.results or "[]"),
        "rules_text":      sess.rules_text or "",
        "rules_name":      sess.rules_name or "",
        "applied_sources": json.loads(sess.applied_sources or "[]"),
    }
