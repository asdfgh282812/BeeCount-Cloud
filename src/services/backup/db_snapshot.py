"""数据库快照 —— dialect-aware,SQLite 用 `VACUUM INTO`,PostgreSQL 用 `pg_dump`。

SQLite 分支(`vacuum_into`):
WAL 模式下直接 `cp beecount.db` 不安全:
  - WAL 段还没 checkpoint,目标文件少事务
  - 备份过程中读到的内容半截

`VACUUM INTO 'path'` 是原子的 + 已 checkpoint + 输出永远是单文件,无 -shm /
-wal 噪音。需要短暂 read 锁(~ms~s 级),不阻塞写。

PostgreSQL 分支(`pg_dump_into`):
用 `pg_dump --format=plain` 产生一份 `psql` 可直接灌回的纯文本 SQL 快照,
`--clean --if-exists` 让还原时不管目标库是空的还是已跑过 migration 都能
无错覆盖(DROP ... IF EXISTS 再 CREATE),跟 SQLite 分支"整份原子快照"的语义
对齐 —— 还原后的状态就是备份当下的状态,而不是"叠加"在现有 schema 上。

两个分支产生的文件名不同(`db.sqlite3` / `db.sql`),由 `snapshot_database()`
按 dialect 分发,调用方(runner.py)不需要关心具体用哪个函数。
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


logger = logging.getLogger(__name__)


# 备份默认排除的"运维类"表 — 不属于用户数据,留着只让 tar 变大 + 暴露
# 内部细节:
#   - backup_runs / backup_run_targets:备份运行历史(restore 后没意义)
#   - sync_push_idempotency:24 小时滚动 idempotency 缓存
#   - audit_logs:管理员操作日志,运维痕迹,不属于"账本数据"
#   - refresh_tokens:登录 session,restore 后所有人都得重登,留着没用
#   - mcp_call_logs:MCP tool 调用审计,30 天滚动遥测,跟账本数据无关
# **PAT 表 (personal_access_tokens) 要保留** — 用户的 LLM 客户端配置依赖
# 这些 token,restore 后 LLM 仍然能连上,不用重新发 token。
# 用户数据相关(必须保留):users / user_profiles / devices / ledgers /
# sync_changes / sync_cursors / read_*_projection / attachment_files /
# personal_access_tokens / backup_remotes / backup_schedules /
# backup_schedule_remotes(配置要保留)
DEFAULT_EXCLUDED_TABLES = (
    "backup_runs",
    "backup_run_targets",
    "sync_push_idempotency",
    "audit_logs",
    "refresh_tokens",
    "mcp_call_logs",
)


def vacuum_into(
    db: Session,
    target_path: str | Path,
    *,
    exclude_tables: tuple[str, ...] | None = DEFAULT_EXCLUDED_TABLES,
) -> None:
    """跑 VACUUM INTO,把当前数据库一致快照写到 target_path。

    exclude_tables 提供时,VACUUM 完后开 copy 文件,**DELETE 这些表的数据**
    (保留 schema)+ 再 VACUUM 一次释放空间。default 排除运维类表
    (backup_runs / audit_logs 等),用户数据全部保留。

    **注意**:之前版本是 `DROP TABLE` 整张表,restore 后 server 启动会撞
    "no such table" 因为代码里有引用(典型:`mcp_call_logs`)。
    改成 `DELETE FROM` 仅清数据,schema 保留 → restore 后即插即用。

    target_path 父目录必须已存在 + 文件不能已存在(SQLite 要求)。
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    safe = str(target).replace("'", "''")
    db.execute(text(f"VACUUM INTO '{safe}'"))
    db.commit()
    if not target.exists():
        raise RuntimeError(f"VACUUM INTO did not produce file: {target}")

    if exclude_tables:
        from sqlalchemy import create_engine, inspect

        copy_engine = create_engine(f"sqlite:///{target}")
        try:
            # **保留 schema,只 DELETE 数据**(原来用 DROP TABLE 会让 restore
            # 出来的 DB 缺表,server 启动后查到这些表就 500 —— 历史 issue:
            # 0008+ 之后 mcp_call_logs 不在表里,导致 GET /profile/pats 报
            # "no such table"。运维类表本身体积也不大,留 schema 不影响
            # 备份大小。)
            inspector = inspect(copy_engine)
            existing_tables = set(inspector.get_table_names())
            with copy_engine.begin() as conn:
                for tbl in exclude_tables:
                    if tbl not in existing_tables:
                        # 表本来就不存在(老 DB 还没跑过这个 migration)— 跳过
                        continue
                    # 表名是常量白名单,无注入风险
                    conn.execute(text(f"DELETE FROM {tbl}"))
            # VACUUM 释放数据占用的空间(SQLite 不会自动收回)
            with copy_engine.connect() as conn:
                conn.execute(text("VACUUM"))
                conn.commit()
            logger.info(
                "vacuum_into: cleared data in %d tables (schema preserved): %s",
                len(exclude_tables), ", ".join(exclude_tables),
            )
        finally:
            copy_engine.dispose()

    size = target.stat().st_size
    logger.info("VACUUM INTO done: %s (%d bytes)", target, size)


def _pg_dump_connection_args(database_url: str) -> tuple[list[str], dict[str, str]]:
    """把 SQLAlchemy 风格的 database_url(`postgresql+psycopg://...`)转成
    `pg_dump` 认得的连接参数 + 环境变量。

    密码不经 argv 传递(会被同机其它用户 `ps` 看到),走 `PGPASSWORD` 环境
    变量;`--no-password` 保证没有密码时不会卡在交互式 prompt 上。
    """
    url = make_url(database_url)
    args: list[str] = []
    if url.host:
        args += ["--host", url.host]
    if url.port:
        args += ["--port", str(url.port)]
    if url.username:
        args += ["--username", url.username]
    if url.database:
        args += ["--dbname", url.database]

    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    return args, env


def pg_dump_into(
    database_url: str,
    target_path: str | Path,
    *,
    exclude_tables: tuple[str, ...] | None = DEFAULT_EXCLUDED_TABLES,
    pg_dump_binary: str = "pg_dump",
) -> None:
    """跑 `pg_dump`,把当前 PostgreSQL 数据库一致快照写成纯文本 SQL 文件。

    `--clean --if-exists`:每条语句前带 `DROP ... IF EXISTS`,还原时不用
    管目标库是全新的(容器启动已跑过 alembic,表已存在)还是空的,`psql <
    dump.sql` 都不会因为"表已存在"报错 —— 还原结果精确对应备份当下的完整
    schema + 数据,而不是叠加在现状之上。
    `--no-owner --no-privileges`:还原环境的 DB role 名字可能跟备份时不同
    (比如换了台机器),这两个 flag 去掉 OWNER TO / GRANT 语句,避免因为
    role 不存在而报错;数据本身不受影响。

    exclude_tables:跟 `vacuum_into` 语义一致 —— schema 保留、数据不 dump
    (用 `--exclude-table-data`),运维类表(backup_runs / audit_logs 等)
    不占备份体积。
    """
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    conn_args, env = _pg_dump_connection_args(database_url)
    cmd = [
        pg_dump_binary,
        *conn_args,
        "--no-password",
        "--format=plain",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        f"--file={target}",
    ]
    for tbl in exclude_tables or ():
        cmd.append(f"--exclude-table-data={tbl}")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed (exit {result.returncode}): {result.stderr[-2000:]}"
        )
    if not target.exists():
        raise RuntimeError(f"pg_dump did not produce file: {target}")

    size = target.stat().st_size
    logger.info("pg_dump done: %s (%d bytes)", target, size)


def snapshot_database(
    db: Session,
    work_dir: str | Path,
    *,
    database_url: str | None = None,
    exclude_tables: tuple[str, ...] | None = DEFAULT_EXCLUDED_TABLES,
) -> tuple[Path, str]:
    """按当前 DB dialect 分发到 `vacuum_into` / `pg_dump_into`。

    返回 (快照文件路径, dialect 名),供调用方(runner.py)写进 meta.json,
    restore 端凭这个字段判断该用 `cp` 还是 `psql` 还原。
    """
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "sqlite"
    work_dir = Path(work_dir)

    if dialect_name == "postgresql":
        if database_url is None:
            from ...config import get_settings

            database_url = get_settings().database_url
        target = work_dir / "db.sql"
        pg_dump_into(database_url, target, exclude_tables=exclude_tables)
    elif dialect_name == "sqlite":
        target = work_dir / "db.sqlite3"
        vacuum_into(db, target, exclude_tables=exclude_tables)
    else:
        raise RuntimeError(f"unsupported database dialect for backup: {dialect_name!r}")

    return target, dialect_name
