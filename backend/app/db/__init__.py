"""Database layer (owned by the DB engineer). Portable across PostgreSQL 16 and SQL Server 2019+.

Public interface:
    app.db.types    JSONType, UTCDateTime, UtcNow
    app.db.base     Base, IdMixin, WorkspaceMixin, TimestampMixin
    app.db.session  engine, SessionLocal, get_db, make_engine, session_factory, bind_session_factory
    app.db.models   every ORM model
    app.db.seed     seed_if_empty, seed
"""
