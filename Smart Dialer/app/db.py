"""
Database configuration and setup.

Uses SQLite for local development with SQLAlchemy ORM.
Easy to migrate to PostgreSQL for production.
"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool
import os

# Database URL
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:///./smart_dialer.db"
)

# SQLAlchemy engine
# SQLite: use check_same_thread=False and StaticPool for simplicity
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    # Enable foreign keys for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # PostgreSQL or other
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base class for all models
Base = declarative_base()


def init_db():
    """Create all tables from scratch to keep test runs isolated."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_db():
    """Get database session (for dependency injection)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
