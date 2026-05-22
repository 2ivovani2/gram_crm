import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker
from sqlalchemy.exc import IntegrityError

# Configurable via DATABASE_URL env var.
# Defaults to SQLite for local dev / backwards compat.
# Production (unified compose): postgresql://postgres:<pw>@postgres:5432/autosending
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/database.db")

_is_sqlite = DATABASE_URL.startswith("sqlite")
_engine_kwargs = {"connect_args": {"check_same_thread": False}} if _is_sqlite else {}
engine       = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ── Association tables ─────────────────────────────────────────────────────────

campaign_accounts = Table(
    "campaign_accounts",
    Base.metadata,
    Column("campaign_id", Integer, ForeignKey("campaigns.id",  ondelete="CASCADE")),
    Column("account_id",  Integer, ForeignKey("accounts.id",   ondelete="CASCADE")),
)

campaign_channels = Table(
    "campaign_channels",
    Base.metadata,
    Column("campaign_id", Integer, ForeignKey("campaigns.id",  ondelete="CASCADE")),
    Column("channel_id",  Integer, ForeignKey("channels.id",   ondelete="CASCADE")),
)

campaign_messages = Table(
    "campaign_messages",
    Base.metadata,
    Column("campaign_id", Integer, ForeignKey("campaigns.id",         ondelete="CASCADE")),
    Column("message_id",  Integer, ForeignKey("message_templates.id", ondelete="CASCADE")),
)


# ── Models ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id          = Column(Integer,    primary_key=True, index=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username    = Column(String(64), nullable=True)   # Telegram @username (may be absent)
    first_name  = Column(String(64), nullable=True)
    last_name   = Column(String(64), nullable=True)
    created_at  = Column(DateTime,   default=datetime.utcnow)
    last_login  = Column(DateTime,   default=datetime.utcnow)

    accounts  = relationship("Account",         back_populates="user", cascade="all, delete-orphan")
    channels  = relationship("Channel",         back_populates="user", cascade="all, delete-orphan")
    messages  = relationship("MessageTemplate", back_populates="user", cascade="all, delete-orphan")
    campaigns = relationship("Campaign",        back_populates="user", cascade="all, delete-orphan")


class Account(Base):
    __tablename__ = "accounts"

    id            = Column(Integer,     primary_key=True, index=True)
    user_id       = Column(Integer,     ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    api_id        = Column(Integer,     nullable=False)
    api_hash      = Column(String(64),  nullable=False)
    phone         = Column(String(20),  nullable=False)
    username      = Column(String(64),  nullable=True)
    first_name    = Column(String(64),  nullable=True)
    last_name     = Column(String(64),  nullable=True)
    bio           = Column(Text,        nullable=True)
    session_file  = Column(String(256), nullable=True)
    status        = Column(String(16),  default="pending")
    is_active     = Column(Boolean,     default=True)
    created_at    = Column(DateTime,    default=datetime.utcnow)
    last_used     = Column(DateTime,    nullable=True)
    messages_sent = Column(Integer,     default=0)
    errors_count  = Column(Integer,     default=0)
    tg_user_id    = Column(BigInteger,  nullable=True)

    user = relationship("User", back_populates="accounts")
    logs = relationship("ActivityLog", back_populates="account", cascade="all, delete")


class Channel(Base):
    __tablename__ = "channels"

    id            = Column(Integer,     primary_key=True, index=True)
    user_id       = Column(Integer,     ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    url           = Column(String(256), nullable=False)
    title         = Column(String(128), nullable=True)
    channel_tg_id = Column(String(32),  nullable=True)
    is_active     = Column(Boolean,     default=True)
    created_at    = Column(DateTime,    default=datetime.utcnow)
    success_count = Column(Integer,     default=0, server_default="0")
    fail_streak   = Column(Integer,     default=0, server_default="0")

    user = relationship("User", back_populates="channels")


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id         = Column(Integer,    primary_key=True, index=True)
    user_id    = Column(Integer,    ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    content    = Column(Text,       nullable=False)
    category   = Column(String(64), nullable=True)
    use_count  = Column(Integer,    default=0)
    is_active  = Column(Boolean,    default=True)
    created_at = Column(DateTime,   default=datetime.utcnow)

    user = relationship("User", back_populates="messages")


class Campaign(Base):
    __tablename__ = "campaigns"

    id               = Column(Integer,     primary_key=True, index=True)
    user_id          = Column(Integer,     ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    name             = Column(String(128), nullable=False)
    min_delay        = Column(Float,       default=30.0)
    max_delay        = Column(Float,       default=120.0)
    comment_on_posts = Column(Boolean,     default=False)
    status           = Column(String(16),  default="idle")
    created_at       = Column(DateTime,    default=datetime.utcnow)
    started_at       = Column(DateTime,    nullable=True)
    stopped_at       = Column(DateTime,    nullable=True)
    total_sent       = Column(Integer,     default=0)

    user     = relationship("User",            back_populates="campaigns")
    accounts = relationship("Account",         secondary=campaign_accounts)
    channels = relationship("Channel",         secondary=campaign_channels)
    messages = relationship("MessageTemplate", secondary=campaign_messages)


class AccountMembership(Base):
    """Persisted channel memberships — survives backend restarts."""
    __tablename__ = "account_memberships"

    id          = Column(Integer,     primary_key=True)
    account_id  = Column(Integer,     ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    channel_url = Column(String(256), nullable=False)
    joined_at   = Column(DateTime,    default=datetime.utcnow)

    __table_args__ = (
        Index("ix_account_memberships_acc_url", "account_id", "channel_url", unique=True),
    )


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id            = Column(Integer,     primary_key=True, index=True)
    user_id       = Column(Integer,     ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    account_id    = Column(Integer,     ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    campaign_id   = Column(Integer,     nullable=True)
    channel_url   = Column(String(256), nullable=True)
    message       = Column(Text,        nullable=True)
    action_type   = Column(String(32),  nullable=False)
    status        = Column(String(16),  nullable=False)
    error_message = Column(Text,        nullable=True)
    timestamp     = Column(DateTime,    default=datetime.utcnow)

    account = relationship("Account", back_populates="logs")

    __table_args__ = (
        Index("ix_activity_logs_user_ts", "user_id", "timestamp"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Path("data/sessions").mkdir(parents=True, exist_ok=True)
    Path("data/logs").mkdir(parents=True, exist_ok=True)
    if _is_sqlite:
        _run_migrations()
    else:
        _run_pg_migrations()
    Base.metadata.create_all(bind=engine)


def _pg_add_column(conn, table: str, column: str, col_type: str):
    from sqlalchemy import text
    try:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"))
        conn.commit()
    except Exception as e:
        logger.warning(f"PG migration {table}.{column}: {e}")


def _run_pg_migrations():
    from sqlalchemy import inspect, text
    with engine.connect() as conn:
        inspector = inspect(engine)
        if "users" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("users")}
            if "telegram_id" not in cols:
                # Old username/password schema → migrate to Telegram auth schema
                conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
                conn.commit()

        # Incremental column additions — safe to run repeatedly
        _pg_add_column(conn, "accounts", "tg_user_id",    "BIGINT")
        _pg_add_column(conn, "channels", "success_count", "INTEGER DEFAULT 0")
        _pg_add_column(conn, "channels", "fail_streak",   "INTEGER DEFAULT 0")


def _run_migrations():
    """
    Safe incremental migrations for SQLite.
    Handles upgrading from the old Telegram-based schema to the new
    username/password schema without losing campaign, account, or channel data.
    """
    import sqlite3

    db_path = Path("data/database.db")
    if not db_path.exists():
        return

    conn = sqlite3.connect(str(db_path))
    try:
        _migrate_users_table(conn)
        _add_columns_if_missing(conn)
    finally:
        conn.close()


def _migrate_users_table(conn):
    """
    If the users table has the old Telegram schema (telegram_id column),
    recreate it as the new password-based schema while preserving all rows.
    """
    import sqlite3

    cursor = conn.execute("PRAGMA table_info(users)")
    cols   = {row[1] for row in cursor.fetchall()}

    if not cols:
        return  # table doesn't exist yet — create_all will handle it

    if "telegram_id" not in cols:
        # Already on new schema — just ensure password_hash exists
        if "password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT DEFAULT ''")
            conn.commit()
        return

    # Old schema detected — migrate
    has_pw = "password_hash" in cols

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users_new (
            id            INTEGER PRIMARY KEY,
            username      VARCHAR(64) UNIQUE,
            password_hash VARCHAR(128) NOT NULL DEFAULT '',
            first_name    VARCHAR(64),
            last_name     VARCHAR(64),
            created_at    DATETIME,
            last_login    DATETIME
        )
    """)

    if has_pw:
        conn.execute("""
            INSERT OR IGNORE INTO users_new
                (id, username, password_hash, first_name, last_name, created_at, last_login)
            SELECT id, username, password_hash, first_name, last_name, created_at, last_login
            FROM users
        """)
    else:
        conn.execute("""
            INSERT OR IGNORE INTO users_new
                (id, username, password_hash, first_name, last_name, created_at, last_login)
            SELECT id, username, '', first_name, last_name, created_at, last_login
            FROM users
        """)

    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")
    conn.commit()


def _add_columns_if_missing(conn):
    """Add user_id FK columns to resource tables if they don't exist yet."""
    migrations = [
        ("accounts",          "user_id INTEGER REFERENCES users(id)"),
        ("channels",          "user_id INTEGER REFERENCES users(id)"),
        ("message_templates", "user_id INTEGER REFERENCES users(id)"),
        ("campaigns",         "user_id INTEGER REFERENCES users(id)"),
        ("activity_logs",     "user_id INTEGER REFERENCES users(id)"),
        ("accounts",          "tg_user_id BIGINT"),
        ("channels",          "success_count INTEGER DEFAULT 0"),
        ("channels",          "fail_streak INTEGER DEFAULT 0"),
    ]
    for table, col_def in migrations:
        col_name = col_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
            conn.commit()
        except Exception:
            pass  # column already exists
