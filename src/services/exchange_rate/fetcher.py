"""汇率上游抓取 + 服务端缓存(惰性,无定时任务)。

方向约定:返回/缓存均为 1 base = x quote(与上游一致,不取倒数)。
上游链与条款依据:BeeCount 仓 .docs/multi-currency/04-exchange-rate-sources.md §④B。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session, sessionmaker

from ...config import get_settings
from ...models import ExchangeRateCache

logger = logging.getLogger(__name__)

# 顺序依据 BeeCount 仓 .docs/multi-currency/04-exchange-rate-sources.md §③:
# cdn 主域国内不可靠,放末位兜底海外;国内自托管建议直接配 EXCHANGE_RATE_UPSTREAM
_FAWAZ_HOSTS = [
    "https://fastly.jsdelivr.net",
    "https://testingcf.jsdelivr.net",
    "https://cdn.jsdelivr.net",
]
_FAWAZ_PATH = "/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.min.json"
_FRANKFURTER = "https://api.frankfurter.dev/v1/latest?base={BASE}"
_TIMEOUT = httpx.Timeout(8.0)

# 并发防击穿:同 base 同时只放一个上游请求
_locks: dict[str, asyncio.Lock] = {}


def _normalize(rates: dict) -> dict[str, str]:
    return {str(k).upper(): str(v) for k, v in rates.items()}


async def fetch_upstream(base: str) -> tuple[str, str, dict[str, str]]:
    """按上游链取 (rate_date, source, {QUOTE: '1 base = x quote'});全挂抛异常。"""
    settings = get_settings()
    errors: list[str] = []
    # 注意:httpx<0.27 的 raise_for_status() 返回 None,不能链式调用
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        if settings.exchange_rate_upstream:
            url = settings.exchange_rate_upstream.rstrip("/") + f"/v1/latest?base={base.upper()}"
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                return str(data["date"]), "custom", _normalize(data["rates"])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"custom: {exc}")
        else:
            for host in _FAWAZ_HOSTS:
                url = host + _FAWAZ_PATH.format(base=base.lower())
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                    return str(data["date"]), "fawazahmed0", _normalize(data[base.lower()])
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{host}: {exc}")
            try:
                resp = await client.get(_FRANKFURTER.format(BASE=base.upper()))
                resp.raise_for_status()
                data = resp.json()
                return str(data["date"]), "frankfurter", _normalize(data["rates"])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"frankfurter: {exc}")
    raise RuntimeError("exchange rate upstreams all failed: " + "; ".join(errors))


async def get_rates(db: Session, base: str) -> tuple[ExchangeRateCache, bool]:
    """缓存优先取汇率。返回 (缓存行, stale)。无缓存且上游全挂 → 抛 RuntimeError。

    缓存 miss/过期要打上游(`fetch_upstream` 链式重试到 ~24s),这段绝不能占着
    调用方的连接池连接空等——那样几个并发的冷 base 请求就能把默认
    pool_size=5+overflow=10 的连接池吃满,拖累全站其它请求排队 30s 后 500
    (2026-08-13 稳定性问题排查,跟 AI 端点同一类 bug)。

    修法:锁内的双检读 + 落盘写改用绑定同一个 engine(`db.get_bind()`,尊重
    `dependency_overrides[get_db]` 测试换库)的**独立** Session,不去动调用方
    传进来的 `db`。踩过两个坑:
    1) 改用模块级 `SessionLocal()` —— 绕开了调用方注入的 session/引擎,测试
       换库时读写到错的库上(被 test_exchange_rate_proxy.py 抓到)。
    2) 直接 `db.close()` 后复用调用方的 `db` —— 部分调用方(如
       `workspace.py` 的账户 rollup)在调用前后还要继续用它已经 load 好的其它
       ORM 对象,`close()` 会把那些对象一并 detach,读起来直接坏掉(被
       test_workspace_account_group_fx.py 抓到)。
    """
    settings = get_settings()
    base = base.upper()
    row = db.get(ExchangeRateCache, base)
    ttl = timedelta(hours=settings.exchange_rate_cache_ttl_hours)
    now = datetime.now(timezone.utc)
    if row is not None and _aware(row.fetched_at) + ttl > now:
        return row, False

    ScopedSession = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    lock = _locks.setdefault(base, asyncio.Lock())
    async with lock:
        # 双检:等锁期间别的请求可能已刷新
        stale_fallback: ExchangeRateCache | None
        with ScopedSession() as session:
            cached = session.get(ExchangeRateCache, base)
            if cached is not None and _aware(cached.fetched_at) + ttl > now:
                session.expunge(cached)
                return cached, False
            stale_fallback = cached
            if stale_fallback is not None:
                session.expunge(stale_fallback)

        try:
            rate_date, source, rates = await fetch_upstream(base)
        except Exception as exc:  # noqa: BLE001
            if stale_fallback is not None:
                logger.warning("exchange_rate: upstream down, serve stale base=%s err=%s", base, exc)
                # 无负缓存:上游连续失败时每个请求都会重打一次上游(每跳 8s 上限);
                # 自托管小流量可接受,如需削峰在此加 backoff/短 TTL 标记
                return stale_fallback, True
            raise RuntimeError(str(exc)) from exc

        with ScopedSession() as session:
            row = session.get(ExchangeRateCache, base)
            if row is None:
                row = ExchangeRateCache(
                    base_currency=base, rate_date=rate_date, source=source,
                    payload_json=rates, fetched_at=now,
                )
                session.add(row)
            else:
                row.rate_date = rate_date
                row.source = source
                row.payload_json = rates
                row.fetched_at = now
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row, False


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
