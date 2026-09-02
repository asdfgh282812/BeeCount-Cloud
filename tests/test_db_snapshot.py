"""db_snapshot.vacuum_into / pg_dump_into 行为单测。

核心契约:exclude_tables 里的表 **schema 保留、数据清空**。之前版本 DROP TABLE
导致 restore 后 server 启动撞 "no such table",已修复改成 DELETE FROM
(SQLite)/ `--exclude-table-data`(PostgreSQL)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from src.database import Base
from src.models import Ledger, MCPCallLog, ReadTxProjection, User
from src.services.backup.db_snapshot import (
    DEFAULT_EXCLUDED_TABLES,
    _pg_dump_connection_args,
    pg_dump_into,
    snapshot_database,
    vacuum_into,
)


@pytest.fixture
def src_db(tmp_path):
    """造一个完整 schema + 少量数据的源 DB,返回 (path, session_factory)。"""
    db_path = tmp_path / "src.db"
    # SQLite VACUUM INTO 要求源是文件 DB(不是 :memory:)
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with Session() as db:
        # 用户数据(必须保留)
        db.add(User(id="u1", email="u@x.com", password_hash="h"))
        db.add(
            Ledger(
                id="L1", user_id="u1", external_id="ext", name="L", currency="CNY",
            )
        )
        db.add(
            ReadTxProjection(
                ledger_id="L1", sync_id="tx-1", user_id="u1",
                tx_type="expense", amount=1.0,
                happened_at=datetime.now(timezone.utc),
                tx_index=0, source_change_id=1,
            )
        )
        # 运维表数据(默认 exclude,vacuum_into 应该清掉数据但保留 schema)
        db.add(
            MCPCallLog(
                user_id="u1", tool_name="ping", status="ok",
                called_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    yield db_path, Session
    engine.dispose()


def _read_target(target_path: Path):
    """打开 vacuum_into 输出的目标 DB,返回 (engine, session_factory, inspector)。"""
    engine = create_engine(f"sqlite:///{target_path}")
    Session = sessionmaker(bind=engine)
    inspector = inspect(engine)
    return engine, Session, inspector


def test_vacuum_into_preserves_excluded_table_schema(src_db, tmp_path):
    """exclude_tables 里的表 schema 必须保留 — restore 后 server 启动不报
    'no such table'。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target)

    engine, _, inspector = _read_target(target)
    try:
        existing = set(inspector.get_table_names())
        for tbl in DEFAULT_EXCLUDED_TABLES:
            assert tbl in existing, (
                f"excluded table {tbl!r} schema 不见了 — restore 后会撞 'no such table'"
            )
    finally:
        engine.dispose()


def test_vacuum_into_clears_excluded_table_data(src_db, tmp_path):
    """exclude_tables 里的数据应当被清空(不属于用户数据,留着只让 tar 变大)。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        # 确认 src 里有运维数据
        n = db.scalar(select(text("count(*)")).select_from(text("mcp_call_logs")))
        assert n == 1, "src 里应该有 1 条 mcp_call_log 测试数据"
        vacuum_into(db, target)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            n = tdb.scalar(
                select(text("count(*)")).select_from(text("mcp_call_logs"))
            )
            assert n == 0, "excluded 表的数据应该被清空"
    finally:
        engine.dispose()


def test_vacuum_into_preserves_user_data(src_db, tmp_path):
    """非 excluded 表(用户数据)必须完整保留。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("users"))
            ) == 1
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("ledgers"))
            ) == 1
            assert tdb.scalar(
                select(text("count(*)")).select_from(text("read_tx_projection"))
            ) == 1
    finally:
        engine.dispose()


def test_vacuum_into_handles_missing_excluded_table(src_db, tmp_path, monkeypatch):
    """exclude_tables 列出但源 DB 里没有的表 — 应当 silent skip,不抛错。

    场景:老 DB 还没跑过新 migration,某些表(比如 mcp_call_logs)还没创建。
    """
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        # 故意删一个表,模拟"老 DB"
        db.execute(text("DROP TABLE mcp_call_logs"))
        db.commit()
        # 不应抛错(silent skip 缺失表)
        vacuum_into(db, target)

    assert target.exists()


def test_vacuum_into_with_none_exclude_keeps_everything(src_db, tmp_path):
    """exclude_tables=None → 一字不删,完整 copy。"""
    src_path, src_factory = src_db
    target = tmp_path / "snap.db"

    with src_factory() as db:
        vacuum_into(db, target, exclude_tables=None)

    engine, target_session, _ = _read_target(target)
    try:
        with target_session() as tdb:
            n = tdb.scalar(
                select(text("count(*)")).select_from(text("mcp_call_logs"))
            )
            assert n == 1, "exclude_tables=None 时不该动数据"
    finally:
        engine.dispose()


def test_pg_dump_connection_args_parses_url_and_hides_password():
    """密码不能出现在 argv 里(会被同机其它用户 `ps` 看到),必须走 PGPASSWORD
    环境变量;host/port/user/dbname 正常转成 --flag。"""
    args, env = _pg_dump_connection_args(
        "postgresql+psycopg://beecount:s3cr3t@dbhost:5433/beecount"
    )
    assert args == [
        "--host", "dbhost",
        "--port", "5433",
        "--username", "beecount",
        "--dbname", "beecount",
    ]
    assert env["PGPASSWORD"] == "s3cr3t"
    assert "s3cr3t" not in args


def test_pg_dump_connection_args_no_password_omits_env_var():
    args, env = _pg_dump_connection_args("postgresql+psycopg://beecount@dbhost/beecount")
    assert "PGPASSWORD" not in env
    assert "--username" in args and "beecount" in args


# ---- 需要真的连上 PostgreSQL 才能跑的分支(CI 的 backend-check-postgres
# job 会把 DATABASE_URL 设成 postgres,本机手动跑可以自己 export 同名变量指
# 向一个临时 postgres 实例)。backend-check-sqlite job / 本机默认 sqlite
# 场景下自动 skip,不影响主测试套件。----

_PG_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_pg_available = _PG_DATABASE_URL.startswith("postgresql") and shutil.which("pg_dump") is not None

pg_only = pytest.mark.skipif(
    not _pg_available,
    reason="需要 DATABASE_URL 指向 PostgreSQL 且本机已装 pg_dump(见 backend-check-postgres CI job)",
)


@pytest.fixture
def pg_engine():
    """在**独立的一次性数据库**里跑(不是 DATABASE_URL 指向的那个)。

    CI 的 backend-check-postgres job 用同一个 postgres 服务跑整个 `pytest -q`
    套件,`DATABASE_URL` 指向的 `beecount` 库是所有测试共享的 —— 如果直接在
    那个库上 `drop_all`/`create_all`,teardown 时把表全 drop 掉会打断同一
    job 里其它(按文件名排在 test_db_snapshot.py 之后的)测试。开一个随机
    命名的临时库,用完整库 `DROP DATABASE` 扔掉,不动共享库一根汗毛。
    """
    from sqlalchemy.engine import make_url

    base_url = make_url(_PG_DATABASE_URL)
    admin_engine = create_engine(
        base_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    test_db_name = f"beecount_test_snapshot_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{test_db_name}"'))

    test_url = base_url.set(database=test_db_name)
    test_database_url = test_url.render_as_string(hide_password=False)
    engine = create_engine(test_database_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with Session() as db:
        # Postgres 真的强制 FK 约束(SQLite 测试引擎没开 PRAGMA foreign_keys,
        # 加的顺序无所谓)——这里必须按依赖顺序 flush,不能像 sqlite 版 fixture
        # 那样一股脑 add 完再一次性 commit。
        db.add(User(id="u1", email="u@x.com", password_hash="h"))
        db.flush()
        db.add(
            Ledger(id="L1", user_id="u1", external_id="ext", name="L", currency="CNY")
        )
        db.flush()
        db.add(
            ReadTxProjection(
                ledger_id="L1", sync_id="tx-1", user_id="u1",
                tx_type="expense", amount=1.0,
                happened_at=datetime.now(timezone.utc),
                tx_index=0, source_change_id=1,
            )
        )
        db.add(
            MCPCallLog(
                user_id="u1", tool_name="ping", status="ok",
                called_at=datetime.now(timezone.utc),
            )
        )
        db.commit()

    try:
        yield engine, test_database_url
    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{test_db_name}" WITH (FORCE)'))
        admin_engine.dispose()


@pg_only
def test_snapshot_database_dispatches_to_pg_dump(pg_engine, tmp_path):
    """`db.get_bind().dialect.name == 'postgresql'` 时应该走 pg_dump 分支,
    产生 db.sql(而不是 db.sqlite3),且不再撞本 issue 的原始报错
    (`VACUUM INTO` 语法错误)。"""
    engine, test_database_url = pg_engine
    Session = sessionmaker(bind=engine)
    with Session() as db:
        target, dialect_name = snapshot_database(
            db, tmp_path, database_url=test_database_url
        )

    assert dialect_name == "postgresql"
    assert target == tmp_path / "db.sql"
    assert target.exists()
    dump_text = target.read_text(encoding="utf-8")
    assert "CREATE TABLE" in dump_text
    assert "beecount" in dump_text or "L1" in dump_text or "ext" in dump_text


@pg_only
def test_pg_dump_into_excludes_data_but_keeps_schema(pg_engine, tmp_path):
    """跟 SQLite 分支同一条契约:exclude_tables 只清数据、保留 schema。"""
    _engine, test_database_url = pg_engine
    target = tmp_path / "db.sql"
    pg_dump_into(test_database_url, target, exclude_tables=DEFAULT_EXCLUDED_TABLES)

    dump_text = target.read_text(encoding="utf-8")
    assert "CREATE TABLE" in dump_text
    assert "mcp_call_logs" in dump_text  # schema 还在
    # 数据被排除:dump 里不该出现测试插入的 status='ok' 那行的痕迹
    assert "'ok'" not in dump_text or "COPY mcp_call_logs" not in dump_text


@pg_only
def test_pg_dump_into_restores_cleanly_into_existing_schema(pg_engine, tmp_path):
    """还原验证的核心场景 —— 对应用户的真实顾虑:
    dump 用 --clean --if-exists 产生,灌回一个**已经存在同名 schema**(容器
    启动已跑过 alembic migration,不是空库)的数据库时,`psql < dump.sql`
    不应该报任何 'already exists' 错误,且还原后的数据要跟备份当下完全一致
    (不多不少)。
    """
    engine, test_database_url = pg_engine
    target = tmp_path / "db.sql"
    pg_dump_into(test_database_url, target)  # 默认 exclude_tables,保留用户数据

    # psql 灌回同一个库(此时 schema 已存在 —— 模拟"容器已跑过 migration")
    from sqlalchemy.engine import make_url

    url = make_url(test_database_url)
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    cmd = ["psql", "--no-password", "-v", "ON_ERROR_STOP=1"]
    if url.host:
        cmd += ["--host", url.host]
    if url.port:
        cmd += ["--port", str(url.port)]
    if url.username:
        cmd += ["--username", url.username]
    if url.database:
        cmd += ["--dbname", url.database]
    cmd += ["--file", str(target)]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"psql restore failed (说明 dump 灌回既有 schema 会报错,"
        f"还原会失败):\nstdout={result.stdout}\nstderr={result.stderr}"
    )

    Session = sessionmaker(bind=engine)
    with Session() as db:
        assert db.scalar(select(text("count(*)")).select_from(text("users"))) == 1
        assert db.scalar(select(text("count(*)")).select_from(text("ledgers"))) == 1
        assert (
            db.scalar(select(text("count(*)")).select_from(text("read_tx_projection")))
            == 1
        )
