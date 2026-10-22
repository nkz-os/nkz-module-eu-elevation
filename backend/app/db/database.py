"""
Database connection and session management.
Uses SQLAlchemy with GeoAlchemy2 for PostGIS support.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool
from app.config import settings

_engine = None
SessionLocal = None
Base = declarative_base()


def get_engine():
    """Lazy-init engine to avoid failures in CI without DATABASE_URL."""
    global _engine, SessionLocal
    if _engine is None:
        url = settings.DATABASE_URL
        if not url:
            # CI fallback — engine won't be used in smoke test
            return None
        _engine = create_engine(
            url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_db():
    """Dependency for getting database sessions."""
    engine = get_engine()
    if engine is None:
        raise RuntimeError("DATABASE_URL not configured")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    engine = get_engine()
    if engine is None:
        return
    from app.models import elevation_models  # noqa: F401
    Base.metadata.create_all(bind=engine)
