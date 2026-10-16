"""engine/session 工厂与数据库 URL 处理；Alembic 自动升级入口。"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql import text as sa_text

SQLITE_PREFIX = "sqlite:///"
MYSQL_PREFIX = "mysql+pymysql://"
DEFAULT_DATABASE_URL = "sqlite:///./shop.db"


def resolve_database_url() -> str:
    """读取运行时数据库 URL；未配置时回退默认 SQLite。"""
    return os.getenv("SHOP_DATABASE_URL", DEFAULT_DATABASE_URL)


def validate_database_url(database_url: str) -> str:
    if database_url.startswith(SQLITE_PREFIX) or database_url.startswith(MYSQL_PREFIX):
        return database_url
    raise ValueError(
        "SHOP_DATABASE_URL 必须为 sqlite:/// 或 mysql+pymysql:// 连接串（memory:// 已移除）"
    )


def create_engine_factory(database_url: str) -> tuple[Engine, sessionmaker]:
    """按 URL 方言创建 engine 与 session 工厂。"""
    database_url = validate_database_url(database_url)
    if database_url.startswith(SQLITE_PREFIX):
        path = database_url.removeprefix(SQLITE_PREFIX)
        if path in ("", ":memory:"):
            # 内存库：单连接共享，供轻量工具场景使用
            engine = create_engine(
                "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(
                database_url, connect_args={"timeout": 30, "check_same_thread": False}
            )

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_connection, _record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    else:
        engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=10,
            max_overflow=10,
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def ensure_schema(database_url: str | None = None) -> None:
    """应用启动时执行 Alembic 升级；SHOP_DB_AUTO_UPGRADE=0 可关闭。

    兼容三种库状态：全新库执行 upgrade；已纳管库由 Alembic 处理（已到 head 为空操作）；
    仅由 create_all 建出的库（测试/历史脚本）补 stamp。
    """
    if os.getenv("SHOP_DB_AUTO_UPGRADE", "1") == "0":
        return
    database_url = validate_database_url(database_url or resolve_database_url())

    from alembic import command
    from alembic.config import Config

    migrations_dir = Path(__file__).resolve().parents[3] / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", database_url)

    engine, _ = create_engine_factory(database_url)
    try:
        inspector = inspect(engine)
        has_users = inspector.has_table("users")
        has_version_table = inspector.has_table("alembic_version")
        current_version = None
        if has_version_table:
            with engine.connect() as conn:
                rows = conn.execute(sa_text("SELECT version_num FROM alembic_version")).fetchall()
                current_version = rows[0][0] if rows else None
        if has_users and current_version is None:
            # 业务表已存在但从未纳管（create_all/历史脚本建库）：登记版本即可
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        engine.dispose()
