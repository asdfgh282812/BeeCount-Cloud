from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

settings = get_settings()


# 默认 QueuePool(pool_size=5, max_overflow=10)总共只有 15 个连接 —— 正常读写
# 请求都很快、够用,但一旦有 endpoint 在持有 `Depends(get_db)` 连接的同时去
# await 慢/挂起的外部 I/O(LLM provider、SwipeSmart、汇率上游等),几个并发
# 请求就能把 15 个连接全部占满,导致其它无关的 `/api/v1/read/...`、WebSocket
# 等请求排队 30s 后超时(2026-08-13 稳定性问题排查:根因是 ai/ask.py 等几个
# endpoint 在慢调用期间没有提前 db.close() 归还连接,已在对应文件修复;这里
# 同时加大池子上限作为纵深防御,避免同类疏漏再次把全站拖垮)。
engine_kwargs: dict = {
    "pool_pre_ping": True,
    "pool_size": 20,
    "max_overflow": 40,
    # 连接超过 30 分钟就回收重建,避免连接在数据库端/网络中间设备被悄悄断开
    # 后仍留在池里,下次借出才报错(对 Postgres 部署尤其重要;sqlite 连接
    # 本地进程内建立,回收开销可忽略)。
    "pool_recycle": 1800,
}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(settings.database_url, **engine_kwargs)


# 生产 SQLite 必配:WAL + busy_timeout。详见 .docs/sqlite-concurrency-fix.md
#
# 默认 journal_mode=DELETE + busy_timeout=0 在多 worker FastAPI 下会随机
# 报 "database is locked" — 任何 writer 进入 PENDING 状态(等其它 reader
# 退出),新 reader 都拿不到 SHARED 锁,立即报错。WAL 模式下 reader/writer
# 走 MVCC 不互相阻塞。
#
# 这是生产部署基础设施配置,不是业务 bug。
if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        # WAL: reader 不阻塞 writer,writer 不阻塞 reader(MVCC)。
        # 一次设置后持久化到 db 文件,后续 connection 自动是 WAL。
        cur.execute("PRAGMA journal_mode=WAL")
        # 写写互斥时等 5 秒再报错(默认 0 立即报)。
        cur.execute("PRAGMA busy_timeout=5000")
        # WAL 下推荐 NORMAL,数据不丢且写性能更好(DELETE 默认 FULL,fsync 太频繁)。
        cur.execute("PRAGMA synchronous=NORMAL")
        # SQLite 默认 FK 检查 OFF,显式开启让 ondelete=CASCADE 真生效。
        cur.execute("PRAGMA foreign_keys=ON")
        # 64MB page cache,大库读取性能大幅提升。
        cur.execute("PRAGMA cache_size=-64000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
