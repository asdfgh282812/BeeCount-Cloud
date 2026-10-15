"""End-to-end farm routes use isolated SQLite and deterministic Taipei instants."""

from datetime import timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.database import Base, get_db
from src.main import app
from src.models import Ledger, LedgerMember, ReadTxProjection, User
from src.services import business_time
from src.services.game import engine, presence, rules, social, time
from src.services.game.models import (
    GameAction,
    GameEvent,
    GameFarm,
    GameInventory,
    GameSettlement,
    GameSlot,
    GameWallet,
)

API = "/api/v1/game"


@pytest.fixture
def env(monkeypatch, tmp_path):
    # Independent pooled connections mirror production; StaticPool would let
    # parallel auth reads corrupt a single shared sqlite cursor before the game lock.
    db_engine = create_engine(
        "sqlite:///" + str(tmp_path / "farm.sqlite"), connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(db_engine)
    sessions = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)

    def override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override
    clock = [time.utc("2026-10-15T02:00:00Z")]
    monkeypatch.setattr(engine, "now_utc", lambda: clock[0])
    monkeypatch.setattr(business_time, "business_tz", lambda: ZoneInfo("Asia/Taipei"))
    monkeypatch.setattr(presence, "monotonic", lambda: clock[0].timestamp())
    presence._home.clear()
    client = TestClient(app)

    def register(email, client_type="app", device=None):
        data = dict(
            email=email,
            password="Pa$$word1!",
            device_id=device or str(uuid4()),
            client_type=client_type,
            device_name="pytest",
            platform="test",
        )
        client.post("/api/v1/auth/register", json=data)
        response = client.post("/api/v1/auth/login", json=data)
        assert response.status_code == 200, response.text
        with sessions() as db:
            uid = db.scalar(select(User.id).where(User.email == email))
        return uid, {"Authorization": "Bearer " + response.json()["access_token"]}

    a, ah = register("farm-a@test.local")
    b, bh = register("farm-b@test.local")
    yield dict(
        client=client, sessions=sessions, clock=clock, a=a, b=b, ah=ah, bh=bh, register=register
    )
    client.close()
    app.dependency_overrides.clear()
    presence._home.clear()
    db_engine.dispose()


def error_code(response):
    payload = response.json()
    if isinstance(payload.get("detail"), dict):
        return payload["detail"]["code"]
    return payload.get("code") or payload["error"]["code"]


def create(env, header="ah", **data):
    r = env["client"].post(API + "/farm", headers=env[header], json=data)
    assert r.status_code == 200, r.text
    return r.json()


def action(env, name, header="ah", request_id=None, **params):
    return env["client"].post(
        API + "/actions",
        headers=env[header],
        json=dict(requestId=request_id or str(uuid4()), action=name, **params),
    )


def visit_action(env, name, header="ah", owner=None, **params):
    return env["client"].post(
        API + "/farms/" + (owner or env["b"]) + "/actions",
        headers=env[header],
        json=dict(requestId=str(uuid4()), action=name, **params),
    )


def current(env, header="ah"):
    r = env["client"].get(API + "/farm", headers=env[header])
    assert r.status_code == 200, r.text
    return r.json()


def ready_plot(env, uid, quantity=None, index=0):
    now = env["clock"][0]
    config = rules.load_config()
    for minute in range(100):
        at = now - timedelta(hours=12, minutes=minute)
        p = rules.plant(rules.empty_plot(index, at), "radish", at, uid, config)
        p.update(growth=1, moisture=100, lastAdvancedAt=time.iso(now))
        harvest = rules.harvest(p, now, config)
        if quantity is None or harvest["quantity"] == quantity:
            break
    assert quantity is None or harvest["quantity"] == quantity
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (uid, index))
        slot.plot = p
        slot.stolen_qty = 0
        farm = db.get(GameFarm, uid)
        farm.newbie_until = now - timedelta(days=1)
        db.commit()
    return harvest


def remove_newbie(env, uid):
    with env["sessions"]() as db:
        farm = db.get(GameFarm, uid)
        farm.newbie_until = env["clock"][0] - timedelta(days=1)
        db.commit()


def test_auth_and_web_only_rank_scope(env):
    assert env["client"].get(API + "/config").status_code == 401
    uid, web = env["register"]("farm-a@test.local", "web")
    assert env["client"].get(API + "/leaderboard", headers=web).status_code == 200
    for route in ("/farm", "/market", "/turnips", "/farms"):
        assert env["client"].get(API + route, headers=web).status_code == 403
    assert env["client"].post(API + "/farm", headers=web, json={}).status_code == 403
    assert (
        env["client"]
        .post(
            API + "/actions",
            headers=web,
            json={"requestId": str(uuid4()), "action": "expandStorage"},
        )
        .status_code
        == 403
    )


def test_create_idempotent_and_wallet_cannot_be_client_set(env):
    assert current(env) is None
    first = create(env, balance=999999, state={"xp": 999999})
    assert first["balance"] == 500 and first["state"]["xp"] == 0
    assert len(first["plots"]) == 2 and first["state"]["playerId"] == env["a"]
    again = create(env)
    assert again["balance"] == 500
    with env["sessions"]() as db:
        assert len(list(db.scalars(select(GameWallet).where(GameWallet.user_id == env["a"])))) == 1
    assert current(env, "bh") is None
    create(env, "bh")
    assert current(env, "bh")["state"]["playerId"] == env["b"]
    assert (
        env["client"].post(API + "/farm", headers=env["ah"], json={"importSave": {}}).status_code
        == 409
    )


def test_buy_plant_care_replay_and_failed_action_rollback(env):
    create(env)
    request_id = str(uuid4())
    bought = action(env, "buySeeds", request_id=request_id, cropId="radish", quantity=2)
    assert bought.status_code == 200, bought.text
    assert bought.json()["balance"] == 468
    repeated = action(env, "buySeeds", request_id=request_id, cropId="radish", quantity=2)
    assert repeated.json() == bought.json()
    reused = action(env, "buySeeds", request_id=request_id, cropId="radish", quantity=3)
    assert reused.status_code == 409 and error_code(reused) == "REQUEST_ID_REUSED"
    planted = action(env, "plant", index=0, cropId="radish")
    assert planted.status_code == 200, planted.text
    assert planted.json()["plots"][0]["moisture"] == 60
    watered = action(env, "water", index=0)
    assert watered.status_code == 200
    assert (
        watered.json()["plots"][0]["moisture"] == 100 and watered.json()["water"]["remaining"] == 7
    )
    before = current(env)
    failed = action(env, "buySeeds", cropId="radish", quantity=100)
    assert failed.status_code == 409 and error_code(failed) == "INSUFFICIENT_COINS"
    assert current(env) == before
    failed_id = str(uuid4())
    failed = action(env, "water", index=1, request_id=failed_id)
    assert failed.status_code == 400
    assert current(env)["water"]["remaining"] == 7
    with env["sessions"]() as db:
        assert db.get(GameAction, (env["a"], failed_id)) is None


@pytest.mark.parametrize(
    "params",
    [
        {"action": "buySeeds", "cropId": "radish", "quantity": True},
        {"action": "plant", "index": True, "cropId": "radish"},
        {"action": "buySeeds", "cropId": "radish", "quantity": -1},
        {"action": "wat"},
        {"action": "saveLayout", "layout": {}},
        {"action": "buySeeds", "quantity": 1, "cropId": ["radish"]},
    ],
)
def test_invalid_arguments_are_400_and_atomic(env, params):
    create(env)
    response = env["client"].post(
        API + "/actions", headers=env["ah"], json={"requestId": str(uuid4()), **params}
    )
    assert response.status_code == 400, response.text
    assert current(env)["balance"] == 500


def test_harvest_storage_rollback_xp_and_sale(env):
    create(env)
    h = ready_plot(env, env["a"], 6)
    with env["sessions"]() as db:
        farm = db.get(GameFarm, env["a"])
        farm.state = {**farm.state, "capacity": 1}
        db.commit()
    failed = action(env, "harvest", index=0)
    assert failed.status_code == 409 and error_code(failed) == "STORAGE_FULL"
    assert current(env)["plots"][0]["growth"] == 1 and current(env)["state"]["xp"] == 0
    expanded = action(env, "expandStorage")
    assert expanded.status_code == 200, expanded.text
    harvested = action(env, "harvest", index=0)
    assert harvested.status_code == 200, harvested.text
    assert harvested.json()["state"]["xp"] == h["xp"]
    assert harvested.json()["plots"][0]["cropId"] is None
    assert sum(r["quantity"] for r in harvested.json()["inventory"] if r["itemId"] == "radish") == 6
    sold = action(env, "sell", cropId="radish", quantity=6)
    assert sold.status_code == 200, sold.text
    assert sold.json()["balance"] == 400 + 6 * rules.market_price(
        "radish", env["clock"][0], rules.load_config()
    )


def test_stall_early_collect_and_receipt_no_double_pay(env):
    create(env)
    ready_plot(env, env["a"], 6)
    assert action(env, "harvest", index=0).status_code == 200
    listed = action(env, "listForSale", cropId="radish", quantity=6, slot=0)
    assert listed.status_code == 200, listed.text
    listing = listed.json()["listings"][0]
    assert action(env, "collectListing", slot=0).status_code == 409
    env["clock"][0] = time.utc(listing["sellsAt"]) + timedelta(seconds=1)
    collected = action(env, "collectListing", slot=0)
    assert collected.status_code == 200, collected.text
    balance = collected.json()["balance"]
    assert balance == 500 + 6 * listing["unitPrice"]
    assert action(env, "collectListing", slot=0).status_code == 409
    assert current(env)["balance"] == balance


def test_market_history_is_past_and_turnips_hide_future(env):
    create(env)
    market = env["client"].get(API + "/market", headers=env["ah"])
    assert market.status_code == 200
    assert len(market.json()["prices"]) == 19
    history = env["client"].get(
        API + "/market/history", headers=env["ah"], params={"cropId": "radish"}
    )
    assert history.status_code == 200 and len(history.json()) == 14
    assert all(time.utc(r["at"]) <= env["clock"][0] for r in history.json())
    assert (
        env["client"]
        .get(API + "/market/history", headers=env["ah"], params={"cropId": "bad"})
        .status_code
        == 400
    )
    turnips = env["client"].get(API + "/turnips", headers=env["ah"])
    assert turnips.status_code == 200
    assert set(turnips.json()) == {"buyPrice", "sellPrice", "quantity", "bought", "expiresAt"}
    assert not {"pattern", "prices"} & set(turnips.json())


def test_presence_multi_device_ttl_and_private_visit(env):
    create(env)
    create(env, "bh")
    for device in ("one", "two"):
        r = env["client"].post(
            API + "/presence", headers=env["bh"], json={"state": "home", "deviceId": device}
        )
        assert r.status_code == 200, r.text
    r = env["client"].post(
        API + "/presence", headers=env["bh"], json={"state": "away", "deviceId": "one"}
    )
    assert r.json()["isHome"]
    visit = env["client"].get(API + "/farms/" + env["b"], headers=env["ah"])
    assert visit.status_code == 200, visit.text
    data = visit.json()
    assert data["isHome"] and "plots" in data
    assert not {"balance", "inventory", "settlements", "state", "scoringLedgerId"} & data.keys()
    assert "farm-b@test.local" not in visit.text
    env["clock"][0] += timedelta(seconds=45)
    assert not env["client"].get(API + "/farms/" + env["b"], headers=env["ah"]).json()["isHome"]
    assert visit_action(env, "help", owner=env["a"], index=0, kind="water").status_code == 400


def test_steal_newbie_home_cooldown_and_owner_reserve(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    protected = visit_action(env, "steal", index=0)
    assert protected.status_code == 409 and error_code(protected) == "NEWBIE_PROTECTION"
    remove_newbie(env, env["a"])
    env["client"].post(
        API + "/presence", headers=env["bh"], json={"state": "home", "deviceId": "one"}
    )
    home = visit_action(env, "steal", index=0)
    assert home.status_code == 409 and error_code(home) == "OWNER_HOME"
    env["client"].post(
        API + "/presence", headers=env["bh"], json={"state": "away", "deviceId": "one"}
    )
    stolen = visit_action(env, "steal", index=0)
    assert stolen.status_code == 200, stolen.text
    assert stolen.json()["quantity"] == 1
    again = visit_action(env, "steal", index=0)
    assert again.status_code == 409 and error_code(again) == "COOLDOWN"
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (env["b"], 0))
        slot.stolen_qty = 2
        db.commit()
    env["clock"][0] += timedelta(hours=4)
    reserved = visit_action(env, "steal", index=0)
    assert reserved.status_code == 409 and error_code(reserved) == "OWNER_RESERVE"
    harvested = action(env, "harvest", "bh", index=0)
    assert harvested.status_code == 200, harvested.text
    assert sum(r["quantity"] for r in harvested.json()["inventory"] if r["itemId"] == "radish") >= 4


def seed_events(env, kind, count, at=None):
    with env["sessions"]() as db:
        for _i in range(count):
            db.add(
                GameEvent(
                    id=str(uuid4()),
                    actor_id=env["a"],
                    owner_id=env["b"],
                    kind=kind,
                    data={"quantity": 1, "caught": False},
                    created_at=at or env["clock"][0] - timedelta(hours=1),
                )
            )
        db.commit()


def test_help_rewards_both_and_daily_limit(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (env["b"], 0))
        slot.plot = {**slot.plot, "moisture": 60}
        db.commit()
    helped = visit_action(env, "help", index=0, kind="water")
    assert helped.status_code == 200, helped.text
    assert helped.json()["snapshot"]["balance"] == 505
    assert current(env, "bh")["balance"] == 505
    assert error_code(visit_action(env, "help", index=0, kind="water")) == "NO_HELP_NEEDED"
    seed_events(env, "help", 9)
    limited = visit_action(env, "help", index=0, kind="weed")
    assert limited.status_code == 409 and error_code(limited) == "DAILY_LIMIT"


def test_daily_and_weekly_steal_limits(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])
    seed_events(env, "steal", 10)
    limited = visit_action(env, "steal", index=0)
    assert limited.status_code == 409 and error_code(limited) == "DAILY_LIMIT"
    with env["sessions"]() as db:
        for e in db.scalars(select(GameEvent)):
            db.delete(e)
        db.commit()
    seed_events(env, "steal", 5, env["clock"][0] - timedelta(days=1))
    limited = visit_action(env, "steal", index=0)
    assert limited.status_code == 409 and error_code(limited) == "WEEKLY_LIMIT"


def seed_settlement(env, uid, score=80, flags=None):
    week = "2026-10-05"
    with env["sessions"]() as db:
        db.add(
            GameSettlement(
                user_id=uid,
                week=week,
                data=dict(
                    week=week,
                    score=score,
                    coins=882,
                    streak=1,
                    detailsJson='{"habit": 1}',
                    createdAt=time.iso(env["clock"][0]),
                ),
                anomaly_flags=flags or [],
            )
        )
        db.commit()
    return week


def test_claim_receipts_and_small_leaderboard_anonymous_privacy(env):
    create(env)
    create(env, "bh")
    week = seed_settlement(env, env["a"])
    seed_settlement(env, env["b"], 70)
    claimed = action(env, "claimSettlement", week=week)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["balance"] == 1382 and week in claimed.json()["claimedSettlementWeeks"]
    assert action(env, "claimSettlement", week=week).json()["balance"] == 1382
    assert (
        env["client"]
        .patch(API + "/farm", headers=env["bh"], json={"anonymousOnBoard": True})
        .status_code
        == 200
    )
    with env["sessions"]() as db:
        with engine.transaction(db, [env["a"], env["b"]]):
            social.build_boards(engine.FarmService(db))
    board = env["client"].get(API + "/leaderboard", headers=env["ah"], params={"week": week})
    assert board.status_code == 200, board.text
    data = board.json()
    assert data["playerCount"] == 2 and data["averageScore"] == 75
    assert data["myValue"] == 80
    assert [r["rewardCoins"] for r in data["items"]] == [200, 200]
    anon = data["items"][1]
    assert (
        anon["userId"] is None and anon["avatarUrl"] is None and anon["displayName"] == "匿名農夫"
    )
    assert not {"email", "income", "expense", "budget", "balance"} & set(anon)
    balance = current(env)["balance"]
    with env["sessions"]() as db:
        with engine.transaction(db, [env["a"], env["b"]]):
            social.build_boards(engine.FarmService(db))
    assert current(env)["balance"] == balance
    assert (
        env["client"]
        .get(API + "/leaderboard", headers=env["ah"], params={"board": "unknown"})
        .status_code
        == 400
    )
    assert (
        env["client"]
        .get(API + "/leaderboard", headers=env["ah"], params={"week": "not-date"})
        .status_code
        == 400
    )


def test_disable_hides_social_and_boards(env):
    create(env)
    create(env, "bh")
    seed_settlement(env, env["b"])
    with env["sessions"]() as db:
        with engine.transaction(db, [env["b"]]):
            social.build_boards(engine.FarmService(db))
    disabled = env["client"].patch(API + "/farm", headers=env["bh"], json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    assert env["client"].get(API + "/farms/" + env["b"], headers=env["ah"]).status_code == 404
    assert env["client"].get(API + "/farms", headers=env["ah"]).json() == []
    assert env["client"].get(API + "/leaderboard", headers=env["ah"]).json()["items"] == []


def test_ledger_isolation_shared_reject_and_created_time_midnight(env):
    with env["sessions"]() as db:
        own = Ledger(user_id=env["a"], external_id="own", name="private", currency="TWD")
        other = Ledger(user_id=env["b"], external_id="foreign", name="foreign", currency="TWD")
        shared = Ledger(user_id=env["a"], external_id="shared", name="shared", currency="TWD")
        db.add_all([own, other, shared])
        db.flush()
        db.add(LedgerMember(ledger_id=shared.id, user_id=env["b"], role="editor"))
        lid = own.id
        db.commit()
    for external in ("foreign", "shared"):
        r = env["client"].post(API + "/farm", headers=env["ah"], json={"scoringLedgerId": external})
        assert r.status_code == 400 and error_code(r) == "INELIGIBLE_LEDGER"
    create(env, scoringLedgerId="own")
    with env["sessions"]() as db:
        farm = db.get(GameFarm, env["a"])
        farm.state = {**farm.state, "enabledAt": "2026-10-04T16:00:00.000Z"}
        for i in range(7):
            at = time.utc("2026-10-04T16:00:00Z") + timedelta(days=i, minutes=1)
            db.add(
                ReadTxProjection(
                    ledger_id=lid,
                    sync_id=f"tx-{i}",
                    user_id=env["a"],
                    tx_type="income",
                    amount=1000,
                    happened_at=at,
                    client_created_at=at,
                    first_seen_at=at,
                )
            )
        db.commit()
    settled = current(env)
    row = next(r for r in settled["settlements"] if r["week"] == "2026-10-05")
    assert row["score"] == 100 and row["coins"] == 1050
    assert settled["balance"] == 500
    assert action(env, "claimSettlement", week="2026-10-05").json()["balance"] == 1550


def local_save(env):
    import json

    now = env["clock"][0]
    return dict(
        saveVersion=1,
        state=dict(
            mode="local",
            configVersion=1,
            playerId=str(uuid4()),
            xp=20,
            capacity=50,
            cityCode="taipei",
        ),
        slots=[dict(index=i, plotJson=json.dumps(rules.empty_plot(i, now))) for i in range(2)],
        inventory=[],
        wallet=[dict(delta=9000)],
        settlements=[dict(week="2026-10-05", score=100, coins=9999)],
        listings=[],
        turnips=[],
    )


def test_local_migration_caps_coins_no_past_rewards_preserves_listing(env):
    now = env["clock"][0]
    save = local_save(env)
    save["inventory"] = [
        dict(itemId="seed:radish", quantity=2, expiresAt=None),
        dict(
            itemId="radish",
            quantity=3,
            expiresAt=time.milliseconds_since_epoch(now + timedelta(days=14)),
        ),
    ]
    save["listings"] = [
        dict(
            slot=0,
            listingId=str(uuid4()),
            cropId="radish",
            quantity=4,
            unitPrice=9,
            listedAt=time.milliseconds_since_epoch(now - timedelta(minutes=3)),
            sellsAt=time.milliseconds_since_epoch(now - timedelta(minutes=1)),
            expiresAt=time.milliseconds_since_epoch(now + timedelta(days=14)),
        )
    ]
    snapshot = create(env, importSave=save)
    assert snapshot["balance"] == 5000 and snapshot["settlements"] == []
    assert snapshot["state"]["xp"] == 20
    assert snapshot["social"]["title"] == "開墾先驅"
    assert len(snapshot["listings"]) == 1
    collected = action(env, "collectListing", slot=0)
    assert collected.status_code == 200, collected.text
    assert collected.json()["balance"] == 5036
    assert sum(r["quantity"] for r in collected.json()["inventory"] if r["itemId"] == "radish") == 3
    assert create(env)["balance"] == 5036


def test_import_turnips_uses_server_prices_but_keeps_current_quantity(env):
    import json

    now = env["clock"][0]
    save = local_save(env)
    generated = rules.turnip_generate(save["state"]["playerId"], now, rules.load_config())
    expiry = time.utc(generated["weekStart"]) + timedelta(days=7)
    save["inventory"] = [
        dict(itemId="turnip", quantity=3, expiresAt=time.milliseconds_since_epoch(expiry))
    ]
    save["turnips"] = [
        dict(
            week=time.day_key(generated["weekStart"]),
            startsAt=time.milliseconds_since_epoch(generated["weekStart"]),
            pattern="large_spike",
            buyPrice=99999,
            pricesJson=json.dumps([99999] * 12),
            bought=3,
            quantity=3,
        )
    ]
    create(env, importSave=save)
    quote = env["client"].get(API + "/turnips", headers=env["ah"])
    assert quote.status_code == 200, quote.text
    own = current(env)
    assert own["ownerId"] == env["a"] and own["state"]["playerId"] == save["state"]["playerId"]
    assert quote.json()["quantity"] == 3 and quote.json()["bought"] == 3
    assert quote.json()["sellPrice"] == rules.turnip_quote(generated, now, rules.load_config())
    assert quote.json()["sellPrice"] != 99999
    sold = action(env, "sellTurnips", quantity=3)
    assert sold.status_code == 200, sold.text
    assert sold.json()["balance"] == 5000 + quote.json()["sellPrice"] * 3


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_plot_field",
        "missing_empty_flag",
        "bad_plot_boolean",
        "duplicate_index",
        "future_time",
        "bad_inventory",
        "negative_wallet",
    ],
)
def test_invalid_import_rolls_back_entire_farm(env, mutation):
    import json

    save = local_save(env)
    if mutation in (
        "missing_plot_field",
        "missing_empty_flag",
        "bad_plot_boolean",
        "duplicate_index",
        "future_time",
    ):
        p = json.loads(save["slots"][1]["plotJson"])
        if mutation == "missing_plot_field":
            del p["lastAdvancedAt"]
        elif mutation == "missing_empty_flag":
            del p["weeds"]
        elif mutation == "bad_plot_boolean":
            p["pests"] = "false"
        elif mutation == "duplicate_index":
            p["index"] = 0
        else:
            p["lastAdvancedAt"] = time.iso(env["clock"][0] + timedelta(days=1))
        save["slots"][1]["plotJson"] = json.dumps(p)
    elif mutation == "bad_inventory":
        save["inventory"] = [dict(itemId="secret", quantity=100)]
    else:
        save["wallet"] = [dict(delta=-1)]
    r = env["client"].post(API + "/farm", headers=env["ah"], json={"importSave": save})
    assert r.status_code == 400, r.text
    assert current(env) is None
    with env["sessions"]() as db:
        assert db.get(GameFarm, env["a"]) is None
        assert not list(db.scalars(select(GameWallet).where(GameWallet.user_id == env["a"])))
        assert not list(db.scalars(select(GameInventory).where(GameInventory.user_id == env["a"])))


def test_concurrent_request_replay_grants_once(env):
    from concurrent.futures import ThreadPoolExecutor

    create(env)
    receipt = str(uuid4())

    def buy(_):
        return action(env, "buySeeds", request_id=receipt, cropId="radish", quantity=2)

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(buy, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert current(env)["balance"] == 468
    assert (
        sum(r["quantity"] for r in current(env)["inventory"] if r["itemId"] == "seed:radish") == 2
    )


def test_guardian_catches_fine_atomic_and_low_conscience(env, monkeypatch):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])

    class AlwaysCatch:
        def __init__(self, seed):
            pass

        def next_double(self):
            return 0

    monkeypatch.setattr(social, "FarmRandom", AlwaysCatch)
    with env["sessions"]() as db:
        owner = db.get(GameFarm, env["b"])
        owner.state = {
            **owner.state,
            "guardDogUntil": time.iso(env["clock"][0] + timedelta(days=1)),
        }
        db.commit()
    caught = visit_action(env, "steal", index=0)
    assert caught.status_code == 200, caught.text
    fine = 3 * rules.market_price("radish", env["clock"][0], rules.load_config())
    assert caught.json()["caught"] and caught.json()["quantity"] == 0
    assert (
        caught.json()["snapshot"]["balance"] == 500 - fine
        and current(env, "bh")["balance"] == 500 + fine
    )
    with env["sessions"]() as db:
        actor = db.get(GameFarm, env["a"])
        actor.conscience = 19
        db.commit()
    denied = visit_action(env, "steal", index=0)
    assert denied.status_code == 409 and error_code(denied) == "LOW_CONSCIENCE"


def test_disabled_cloud_account_not_public(env):
    create(env)
    create(env, "bh")
    with env["sessions"]() as db:
        user = db.get(User, env["b"])
        user.is_enabled = False
        db.commit()
    assert env["client"].get(API + "/farms/" + env["b"], headers=env["ah"]).status_code == 404
    assert env["client"].get(API + "/farms", headers=env["ah"]).json() == []


def test_rank_rewards_at_five_players_and_invalid_calendar_week(env):
    create(env)
    create(env, "bh")
    players = [env["a"], env["b"]]
    for i in range(3):
        uid, header = env["register"](f"board-{i}@test.local")
        r = env["client"].post(API + "/farm", headers=header, json={})
        assert r.status_code == 200
        players.append(uid)
    for i, uid in enumerate(players):
        seed_settlement(env, uid, 100 - i)
    with env["sessions"]() as db:
        with engine.transaction(db, players):
            social.build_boards(engine.FarmService(db))
    board = (
        env["client"]
        .get(API + "/leaderboard", headers=env["ah"], params={"week": "2026-10-05"})
        .json()
    )
    assert board["playerCount"] == 5
    assert [r["rewardCoins"] for r in board["items"]] == [1500, 1000, 1000, 200, 200]
    assert board["items"][0]["title"] == "本週帳王"
    champion = current(env)
    assert champion["balance"] == 2000
    assert champion["social"]["decorCredits"] == {"tree_orange": 1}
    with env["sessions"]() as db:
        with engine.transaction(db, players):
            social.build_boards(engine.FarmService(db))
    assert current(env)["social"]["decorCredits"] == {"tree_orange": 1}
    layout = rules.default_layout(range(2))
    position = rules.first_free(layout, 48, 48)
    layout["decor"].append(dict(id="champion-tree", type="tree_orange", position=position))
    redeemed = action(env, "saveLayout", layout=layout)
    assert redeemed.status_code == 200, redeemed.text
    assert redeemed.json()["balance"] == 2000
    assert redeemed.json()["social"]["decorCredits"] == {"tree_orange": 0}
    assert action(env, "saveLayout", layout=layout).json()["balance"] == 2000
    layout["decor"] = [d for d in layout["decor"] if d["id"] != "champion-tree"]
    assert action(env, "saveLayout", layout=layout).status_code == 200
    layout["decor"].append(dict(id="another-tree", type="tree_orange", position=position))
    paid = action(env, "saveLayout", layout=layout)
    assert paid.status_code == 200, paid.text
    assert paid.json()["balance"] == 1850
    assert paid.json()["social"]["decorCredits"] == {"tree_orange": 0}
    for bad in ("2026-99-99", "2026-10-06"):
        r = env["client"].get(API + "/leaderboard", headers=env["ah"], params={"week": bad})
        assert r.status_code == 400, r.text


def test_area_item_consumed_once_and_applies_neighbour_plots(env):
    create(env)
    assert action(env, "buySeeds", cropId="radish", quantity=2).status_code == 200
    assert action(env, "plant", cropId="radish", index=0).status_code == 200
    assert action(env, "plant", cropId="radish", index=1).status_code == 200
    assert action(env, "buyItem", itemId="sun_doll", quantity=1).status_code == 200
    used = action(env, "useItem", itemId="sun_doll", index=0)
    assert used.status_code == 200, used.text
    for p in used.json()["plots"]:
        assert [e["id"] for e in p["effects"]] == ["sun_doll"]
        assert time.utc(p["effects"][0]["expiresAt"]) == env["clock"][0] + timedelta(hours=6)
    assert not any(r["itemId"] == "item:sun_doll" for r in used.json()["inventory"])
    assert action(env, "useItem", itemId="sun_doll", index=0).status_code == 409


def test_weather_cache_is_authoritative_growth_input(env):
    from src.services.game.models import GameWeather

    create(env)
    assert action(env, "buySeeds", cropId="radish", quantity=1).status_code == 200
    assert action(env, "plant", cropId="radish", index=0).status_code == 200
    now = env["clock"][0]
    with env["sessions"]() as db:
        db.add(
            GameWeather(
                city_code="taipei",
                hour=now,
                fetched_at=now,
                data=dict(
                    at=time.iso(now),
                    temperature=25,
                    precipitation=1,
                    cloud=50,
                    gust=0,
                    humidity=50,
                    isDay=True,
                ),
            )
        )
        db.commit()
    env["clock"][0] += timedelta(hours=1)
    p = current(env)["plots"][0]
    assert p["moisture"] == 100 and p["growth"] == pytest.approx(0.8 / 12)


def test_jobs_ignore_disabled_cloud_account(env):
    from src.services.game import jobs

    create(env)
    create(env, "bh")
    with env["sessions"]() as db:
        user = db.get(User, env["b"])
        user.is_enabled = False
        db.commit()
    with env["sessions"]() as db:
        result = jobs.run(db)
    assert result["farms"] == 1


def incoming_theft(env, at=None, returned=False, caught=False):
    event_id = str(uuid4())
    with env["sessions"]() as db:
        db.add(
            GameEvent(
                id=event_id,
                actor_id=env["b"],
                owner_id=env["a"],
                kind="steal",
                data={"quantity": 1, "caught": caught, "returned": returned},
                created_at=at or env["clock"][0] - timedelta(hours=1),
            )
        )
        db.commit()
    return event_id


def test_return_gift_once_exempts_home_daily_limit_and_cooldown(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])
    gift = incoming_theft(env)
    seed_events(env, "steal", 10)
    # Reach today's cap across several farms, without exceeding the target's
    # independent weekly anti-harassment limit (which return gifts do not bypass).
    other_farms = []
    for n in range(2):
        uid, headers = env["register"](f"return-other-{n}@test.local")
        response = env["client"].post(API + "/farm", headers=headers, json={})
        assert response.status_code == 200
        other_farms.append(uid)
    with env["sessions"]() as db:
        outgoing = list(db.scalars(select(GameEvent).where(GameEvent.actor_id == env["a"])))
        for i, event in enumerate(outgoing):
            if i >= 4:
                event.owner_id = other_farms[(i - 4) // 3]
        db.commit()
    env["client"].post(
        API + "/presence", headers=env["bh"], json={"state": "home", "deviceId": "host"}
    )
    records = env["client"].get(API + "/events", headers=env["ah"])
    assert records.status_code == 200, records.text
    receipt = next(r for r in records.json() if r["id"] == gift)
    assert receipt["returnGiftAllowed"]
    returned = visit_action(env, "returnGift", index=0, eventId=gift)
    assert returned.status_code == 200, returned.text
    assert returned.json()["quantity"] == 1
    again = visit_action(env, "returnGift", index=0, eventId=gift)
    assert again.status_code == 409 and error_code(again) == "NO_RETURN_GIFT"
    records = env["client"].get(API + "/events", headers=env["ah"]).json()
    receipt = next(r for r in records if r["id"] == gift)
    assert receipt["returned"] and not receipt["returnGiftAllowed"]


@pytest.mark.parametrize("age_hours", [12, 12.001])
def test_return_gift_expires_at_exact_twelve_hours(env, age_hours):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])
    gift = incoming_theft(env, env["clock"][0] - timedelta(hours=age_hours))
    receipt = next(
        r for r in env["client"].get(API + "/events", headers=env["ah"]).json() if r["id"] == gift
    )
    assert not receipt["returnGiftAllowed"]
    returned = visit_action(env, "returnGift", index=0, eventId=gift)
    assert returned.status_code == 409 and error_code(returned) == "NO_RETURN_GIFT"


def test_events_only_include_involved_recent_players_and_web_is_denied(env):
    create(env)
    create(env, "bh")
    recent = incoming_theft(env)
    old = incoming_theft(env, env["clock"][0] - timedelta(days=8))
    third_uid, third_header = env["register"]("events-third@test.local")
    env["client"].post(API + "/farm", headers=third_header, json={})
    unrelated = str(uuid4())
    with env["sessions"]() as db:
        db.add(
            GameEvent(
                id=unrelated,
                actor_id=env["b"],
                owner_id=third_uid,
                kind="react",
                data={"emoji": "🌱"},
                created_at=env["clock"][0],
            )
        )
        db.commit()
    records = env["client"].get(API + "/events", headers=env["ah"])
    assert records.status_code == 200, records.text
    assert {r["id"] for r in records.json()} == {recent}
    assert old not in records.text and unrelated not in records.text
    _, web = env["register"]("farm-a@test.local", "web")
    assert env["client"].get(API + "/events", headers=web).status_code == 403


def test_notification_preferences_suppress_stolen_but_retain_return_gift(env):
    from src.models import Notification

    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])
    patched = env["client"].patch(
        API + "/farm", headers=env["bh"], json={"notificationSettings": {"stolen": False}}
    )
    assert patched.status_code == 200, patched.text
    stolen = visit_action(env, "steal", index=0)
    assert stolen.status_code == 200, stolen.text
    with env["sessions"]() as db:
        assert not list(db.scalars(select(Notification).where(Notification.user_id == env["b"])))
    events = env["client"].get(API + "/events", headers=env["bh"]).json()
    assert any(r["returnGiftAllowed"] for r in events)
    invalid = env["client"].patch(
        API + "/farm", headers=env["bh"], json={"notificationSettings": {"stolen": "false"}}
    )
    assert invalid.status_code == 400, invalid.text
    assert current(env, "bh")["social"]["notificationSettings"]["stolen"] is False


def test_market_snapshot_captures_previous_24h_supply_and_stays_fixed(env):
    from src.services.game.models import GameMarketSnapshot
    from src.services.game.time import market_slot, market_slot_start

    create(env)
    at = market_slot_start(market_slot(env["clock"][0]))
    with env["sessions"]() as db:
        for quantity, ago in [
            (1000, timedelta(hours=1)),
            (4000, timedelta(hours=25)),
            (3000, timedelta(0)),
        ]:
            db.add(
                GameEvent(
                    id=str(uuid4()),
                    actor_id=env["a"],
                    owner_id=env["a"],
                    kind="market_sale",
                    data={"cropId": "radish", "quantity": quantity},
                    created_at=at - ago,
                )
            )
        db.commit()
    quote = env["client"].get(API + "/market", headers=env["ah"])
    assert quote.status_code == 200, quote.text
    price = quote.json()["prices"]["radish"]
    assert price == rules.market_price("radish", at, rules.load_config(), supply_quantity=1000)
    with env["sessions"]() as db:
        assert db.get(GameMarketSnapshot, (market_slot(at), "radish")).supply_quantity == 1000
        db.add(
            GameEvent(
                id=str(uuid4()),
                actor_id=env["a"],
                owner_id=env["a"],
                kind="market_sale",
                data={"cropId": "radish", "quantity": 9000},
                created_at=at - timedelta(minutes=1),
            )
        )
        db.commit()
    assert env["client"].get(API + "/market", headers=env["ah"]).json()["prices"]["radish"] == price
    history = env["client"].get(
        API + "/market/history", headers=env["ah"], params={"cropId": "radish"}
    )
    assert history.status_code == 200, history.text
    assert history.json()[-1] == {"at": time.iso(at), "price": price}


def test_daily_help_resets_at_taipei_midnight_and_water_not_utc_midnight(env):
    env["clock"][0] = time.utc("2026-10-15T15:59:00Z")
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (env["b"], 0))
        slot.plot = {**slot.plot, "moisture": 60}
        db.commit()
    seed_events(env, "help", 10, env["clock"][0])
    assert error_code(visit_action(env, "help", index=0, kind="water")) == "DAILY_LIMIT"
    env["clock"][0] = time.utc("2026-10-15T16:01:00Z")
    helped = visit_action(env, "help", index=0, kind="water")
    assert helped.status_code == 200, helped.text
    assert helped.json()["snapshot"]["state"]["waterDay"] == "2026-10-16"
    assert helped.json()["snapshot"]["water"]["remaining"] == 8


def test_harvest_notification_opt_in_and_one_per_crop(env):
    from src.models import Notification

    create(env)
    create(env, "bh")
    patched = env["client"].patch(
        API + "/farm", headers=env["ah"], json={"notificationSettings": {"harvest": True}}
    )
    assert patched.status_code == 200, patched.text
    ready_plot(env, env["a"], 6)
    ready_plot(env, env["b"], 6)
    current(env)
    current(env)
    current(env, "bh")
    with env["sessions"]() as db:
        rows = list(db.scalars(select(Notification).where(Notification.category == "farm")))
        assert [r.user_id for r in rows] == [env["a"]]
        assert rows[0].payload_json["type"] == "farm_harvest"
    assert action(env, "harvest", index=0).status_code == 200
    ready_plot(env, env["a"], 6)
    # A newly planted generation, even in the same slot, may notify again.
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (env["a"], 0))
        slot.plot = {**slot.plot, "plantedAt": time.iso(env["clock"][0] - timedelta(hours=11))}
        db.commit()
    current(env)
    with env["sessions"]() as db:
        assert (
            len(list(db.scalars(select(Notification).where(Notification.user_id == env["a"])))) == 2
        )


def test_season_reminder_waits_until_taipei_20_once_and_respects_pref(env):
    from src.models import Notification

    env["clock"][0] = time.utc("2026-11-28T11:59:00Z")
    create(env)
    create(env, "bh")
    for header in ("ah", "bh"):
        assert action(env, "buySeeds", header, cropId="radish", quantity=1).status_code == 200
        assert action(env, "plant", header, cropId="radish", index=0).status_code == 200
    env["client"].patch(
        API + "/farm", headers=env["bh"], json={"notificationSettings": {"season": False}}
    )
    with env["sessions"]() as db:
        assert not list(db.scalars(select(Notification).where(Notification.category == "farm")))
    env["clock"][0] += timedelta(minutes=1)
    current(env)
    current(env)
    current(env, "bh")
    with env["sessions"]() as db:
        rows = list(db.scalars(select(Notification).where(Notification.category == "farm")))
        assert [r.user_id for r in rows] == [env["a"]]
        assert rows[0].payload_json == {"type": "farm_season", "days": 3}


def test_social_replay_cannot_duplicate_both_wallet_rewards(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    with env["sessions"]() as db:
        slot = db.get(GameSlot, (env["b"], 0))
        slot.plot = {**slot.plot, "moisture": 60}
        db.commit()
    payload = dict(requestId=str(uuid4()), action="help", index=0, kind="water")
    path = API + "/farms/" + env["b"] + "/actions"
    first = env["client"].post(path, headers=env["ah"], json=payload)
    again = env["client"].post(path, headers=env["ah"], json=payload)
    assert first.status_code == 200 and first.json() == again.json()
    assert current(env)["balance"] == 505 and current(env, "bh")["balance"] == 505
    with env["sessions"]() as db:
        assert len(list(db.scalars(select(GameEvent).where(GameEvent.kind == "help")))) == 1


def test_turnips_old_batch_expires_at_sunday05_new_midnight_purchase_survives(env):
    env["clock"][0] = time.utc("2026-10-17T16:00:00Z")
    create(env)
    old_expiry = env["clock"][0] + timedelta(hours=5)
    with env["sessions"]() as db:
        farm = db.get(GameFarm, env["a"])
        old = rules.turnip_generate(
            farm.state["playerId"], env["clock"][0] - timedelta(days=1), rules.load_config()
        )
        farm.turnips = {**old, "quantity": 2, "bought": 2}
        db.add(GameInventory(user_id=env["a"], item_id="turnip", quantity=2, expires_at=old_expiry))
        db.commit()
    bought = action(env, "buyTurnips", quantity=1)
    assert bought.status_code == 200, bought.text
    assert sum(r["quantity"] for r in bought.json()["inventory"] if r["itemId"] == "turnip") == 3
    env["clock"][0] = old_expiry - timedelta(seconds=1)
    assert sum(r["quantity"] for r in current(env)["inventory"] if r["itemId"] == "turnip") == 3
    env["clock"][0] = old_expiry
    remaining = current(env)
    assert sum(r["quantity"] for r in remaining["inventory"] if r["itemId"] == "turnip") == 1
    quote = env["client"].get(API + "/turnips", headers=env["ah"])
    assert quote.status_code == 200 and quote.json()["quantity"] == 1
    assert quote.json()["sellPrice"] is None


def test_new_bookkeeping_of_ancient_date_still_counts_habit_and_water_bonus(env):
    now = env["clock"][0]
    with env["sessions"]() as db:
        ledger = Ledger(user_id=env["a"], external_id="retroactive", name="private", currency="TWD")
        db.add(ledger)
        db.flush()
        lid = ledger.id
        db.commit()
    create(env, scoringLedgerId="retroactive")
    with env["sessions"]() as db:
        db.add(
            ReadTxProjection(
                ledger_id=lid,
                sync_id="ancient-date",
                user_id=env["a"],
                tx_type="income",
                amount=1000,
                happened_at=now - timedelta(days=200),
                client_created_at=now,
                first_seen_at=now,
            )
        )
        db.commit()
    snapshot = current(env)
    assert snapshot["water"]["bonus"] and snapshot["water"]["remaining"] == 16
    with env["sessions"]() as db:
        svc = engine.FarmService(db)
        entries = svc.entries(db.get(GameFarm, env["a"]))
    assert len(entries) == 1
    assert rules.score(time.week_start(now), entries, rules.load_config())[
        "habit"
    ] == pytest.approx(1 / 7)


def test_refund_outside_read_window_excludes_original_from_scoring(env):
    now = env["clock"][0]
    with env["sessions"]() as db:
        ledger = Ledger(user_id=env["a"], external_id="refunds", name="private", currency="TWD")
        db.add(ledger)
        db.flush()
        lid = ledger.id
        db.commit()
    create(env, scoringLedgerId="refunds")
    with env["sessions"]() as db:
        db.add_all(
            [
                ReadTxProjection(
                    ledger_id=lid,
                    sync_id="expense",
                    user_id=env["a"],
                    tx_type="expense",
                    amount=1000,
                    happened_at=now,
                    client_created_at=now,
                    first_seen_at=now,
                ),
                ReadTxProjection(
                    ledger_id=lid,
                    sync_id="refund",
                    user_id=env["a"],
                    tx_type="income",
                    amount=1000,
                    happened_at=now - timedelta(days=200),
                    client_created_at=now - timedelta(days=200),
                    first_seen_at=now - timedelta(days=200),
                    refund_of_sync_id="expense",
                ),
            ]
        )
        db.commit()
        svc = engine.FarmService(db)
        entries = svc.entries(db.get(GameFarm, env["a"]))
    assert len(entries) == 1 and entries[0]["isRefund"]
    assert rules.score(time.week_start(now), entries, rules.load_config())["habit"] == 0
    assert not current(env)["water"]["bonus"]


@pytest.mark.parametrize("caught", [True, False])
def test_dog_fine_or_insurance_uses_authoritative_market_snapshot(env, monkeypatch, caught):
    from src.services.game.models import GameMarketSnapshot

    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])

    class DeterministicDog:
        def __init__(self, seed):
            pass

        def next_double(self):
            return 0 if caught else 1

    monkeypatch.setattr(social, "FarmRandom", DeterministicDog)
    price = 13
    with env["sessions"]() as db:
        owner = db.get(GameFarm, env["b"])
        owner.state = {
            **owner.state,
            "guardDogUntil": time.iso(env["clock"][0] + timedelta(hours=1)),
        }
        db.add(
            GameMarketSnapshot(
                tick=time.market_slot(env["clock"][0]),
                crop_id="radish",
                price=price,
                supply_quantity=1000,
            )
        )
        db.commit()
    stolen = visit_action(env, "steal", index=0)
    assert stolen.status_code == 200, stolen.text
    assert stolen.json()["caught"] is caught
    assert current(env)["balance"] == 500 - (price * 3 if caught else 0)
    assert current(env, "bh")["balance"] == 500 + (
        price * 3 if caught else rules.dart_round(price * 0.3)
    )


def test_guard_dog_requires_fine_coverage_before_random_draw(env, monkeypatch):
    from src.services.game.models import GameMarketSnapshot

    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])

    class MustNotDraw:
        def __init__(self, seed):
            raise AssertionError("Insufficient fine coverage must fail before randomness")

    monkeypatch.setattr(social, "FarmRandom", MustNotDraw)
    with env["sessions"]() as db:
        owner = db.get(GameFarm, env["b"])
        owner.state = {
            **owner.state,
            "guardDogUntil": time.iso(env["clock"][0] + timedelta(hours=1)),
        }
        db.add(
            GameMarketSnapshot(
                tick=time.market_slot(env["clock"][0]),
                crop_id="radish",
                price=200,
                supply_quantity=0,
            )
        )
        db.commit()
    stolen = visit_action(env, "steal", index=0)
    assert stolen.status_code == 409 and error_code(stolen) == "INSUFFICIENT_COINS"
    assert current(env)["balance"] == 500 and current(env, "bh")["balance"] == 500
    with env["sessions"]() as db:
        assert db.get(GameSlot, (env["b"], 0)).stolen_qty == 0
        assert not list(db.scalars(select(GameEvent).where(GameEvent.kind == "steal")))


@pytest.mark.parametrize("offset,expected_habit", [(-1, 1 / 7), (0, 1 / 7), (1, 0), (None, 0)])
def test_habit_arrival_grace_at_48h_and_missing_first_seen(env, offset, expected_habit):
    import json

    with env["sessions"]() as db:
        ledger = Ledger(user_id=env["a"], external_id="grace", name="private", currency="TWD")
        db.add(ledger)
        db.flush()
        lid = ledger.id
        db.commit()
    create(env, scoringLedgerId="grace")
    created = time.utc("2026-10-10T02:00:00Z")
    deadline = time.utc("2026-10-13T16:00:00Z")
    with env["sessions"]() as db:
        farm = db.get(GameFarm, env["a"])
        farm.state = {**farm.state, "enabledAt": "2026-10-04T16:00:00.000Z"}
        db.add(
            ReadTxProjection(
                ledger_id=lid,
                sync_id="arrival",
                user_id=env["a"],
                tx_type="income",
                amount=1000,
                happened_at=created,
                client_created_at=created,
                first_seen_at=deadline + timedelta(seconds=offset) if offset is not None else None,
            )
        )
        db.commit()
    settlement = next(r for r in current(env)["settlements"] if r["week"] == "2026-10-05")
    assert json.loads(settlement["detailsJson"])["habit"] == pytest.approx(expected_habit)
    # A frozen zero-habit week stays frozen if arrival metadata is corrected later.
    with env["sessions"]() as db:
        tx = db.get(ReadTxProjection, (lid, "arrival"))
        tx.first_seen_at = deadline - timedelta(seconds=1)
        db.commit()
    repeated = next(r for r in current(env)["settlements"] if r["week"] == "2026-10-05")
    assert repeated == settlement


def test_old_import_creation_time_does_not_become_today_habit(env):
    now = env["clock"][0]
    with env["sessions"]() as db:
        ledger = Ledger(user_id=env["a"], external_id="oldimport", name="private", currency="TWD")
        db.add(ledger)
        db.flush()
        lid = ledger.id
        db.commit()
    create(env, scoringLedgerId="oldimport")
    with env["sessions"]() as db:
        db.add(
            ReadTxProjection(
                ledger_id=lid,
                sync_id="oldimport",
                user_id=env["a"],
                tx_type="income",
                amount=1000,
                happened_at=now,
                client_created_at=now - timedelta(days=200),
                first_seen_at=now,
            )
        )
        db.commit()
        svc = engine.FarmService(db)
        entries = svc.entries(db.get(GameFarm, env["a"]))
    assert time.utc(entries[0]["createdAt"]) == now - timedelta(days=200)
    assert rules.score(time.week_start(now), entries, rules.load_config())["habit"] == 0
    assert not current(env)["water"]["bonus"]


def test_return_gift_keeps_weekly_target_protection(env):
    create(env)
    create(env, "bh")
    ready_plot(env, env["b"], 6)
    remove_newbie(env, env["a"])
    gift = incoming_theft(env)
    seed_events(env, "steal", 5, env["clock"][0] - timedelta(days=1))
    result = visit_action(env, "returnGift", index=0, eventId=gift)
    assert result.status_code == 409 and error_code(result) == "WEEKLY_LIMIT"
    rows = env["client"].get(API + "/events", headers=env["ah"]).json()
    assert next(r for r in rows if r["id"] == gift)["returned"] is False


def test_bookkeeping_tie_uses_achievement_not_settlement_access_time(env):
    import json

    create(env)
    create(env, "bh")
    seed_settlement(env, env["a"], 80)
    seed_settlement(env, env["b"], 80)
    with env["sessions"]() as db:
        for uid, achieved in [
            (env["a"], "2026-10-11T10:00:00Z"),
            (env["b"], "2026-10-11T01:00:00Z"),
        ]:
            row = db.get(GameSettlement, (uid, "2026-10-05"))
            row.data = {**row.data, "detailsJson": json.dumps({"habit": 1, "achievedAt": achieved})}
        social.build_boards(engine.FarmService(db, env["clock"][0]))
        db.commit()
    rows = env["client"].get(API + "/leaderboard", headers=env["ah"]).json()["items"]
    assert [r["userId"] for r in rows] == [env["b"], env["a"]]


def test_city_change_advances_elapsed_time_under_old_weather(env):
    from src.services.game.models import GameWeather

    create(env)
    action(env, "buySeeds", cropId="radish", quantity=1)
    action(env, "plant", index=0, cropId="radish")
    at = env["clock"][0]
    with env["sessions"]() as db:
        for city, rain in [("taipei", 0), ("tokyo", 1)]:
            db.add(
                GameWeather(
                    city_code=city,
                    hour=at.replace(minute=0, second=0, microsecond=0),
                    fetched_at=at,
                    data={
                        "at": time.iso(at),
                        "temperature": 25,
                        "precipitation": rain,
                        "cloud": 50,
                        "gust": 0,
                        "humidity": 50,
                        "isDay": True,
                    },
                )
            )
        db.commit()
    env["clock"][0] += timedelta(hours=1)
    result = env["client"].patch(API + "/farm", headers=env["ah"], json={"cityCode": "tokyo"})
    assert result.status_code == 200, result.text
    assert result.json()["state"]["cityCode"] == "tokyo"
    assert result.json()["plots"][0]["moisture"] == 57


def test_real_sync_private_owner_membership_can_score_then_pauses_if_shared(env):
    from src.models import Device

    with env["sessions"]() as db:
        device = db.scalar(select(Device.id).where(Device.user_id == env["a"]))
    response = env["client"].post(
        "/api/v1/sync/push",
        headers=env["ah"],
        json={
            "device_id": device,
            "changes": [
                {
                    "ledger_id": "real-personal",
                    "entity_type": "ledger",
                    "entity_sync_id": "real-personal",
                    "action": "upsert",
                    "updated_at": time.iso(env["clock"][0]),
                    "payload": {"ledgerName": "Personal", "currency": "TWD"},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    with env["sessions"]() as db:
        ledger = db.scalar(
            select(Ledger).where(Ledger.user_id == env["a"], Ledger.external_id == "real-personal")
        )
        assert db.get(LedgerMember, (ledger.id, env["a"])).role == "owner"
        ledger_id = ledger.id
    farm = create(env, scoringLedgerId="real-personal")
    assert farm["scoringAvailable"] is True and farm["scoringLedgerSyncId"] == "real-personal"
    with env["sessions"]() as db:
        db.add(LedgerMember(ledger_id=ledger_id, user_id=env["b"], role="editor"))
        db.commit()
    assert current(env)["scoringAvailable"] is False


def test_soft_deleted_personal_ledger_stops_farm_scoring(env):
    from src.models import SyncChange

    with env["sessions"]() as db:
        ledger = Ledger(
            user_id=env["a"], external_id="deleted-personal", name="Personal", currency="TWD"
        )
        db.add(ledger)
        db.flush()
        ledger_id = ledger.id
        db.add(LedgerMember(ledger_id=ledger_id, user_id=env["a"], role="owner"))
        db.commit()
    assert create(env, scoringLedgerId="deleted-personal")["scoringAvailable"]
    with env["sessions"]() as db:
        db.add(
            SyncChange(
                user_id=env["a"],
                ledger_id=ledger_id,
                entity_type="ledger_snapshot",
                entity_sync_id="deleted-personal",
                action="delete",
                payload_json={},
                updated_at=env["clock"][0],
            )
        )
        db.commit()
    assert current(env)["scoringAvailable"] is False
    response = env["client"].patch(
        API + "/farm", headers=env["ah"], json={"scoringLedgerId": "deleted-personal"}
    )
    assert response.status_code == 400 and error_code(response) == "INELIGIBLE_LEDGER"


def test_shared_ledger_needs_reselection_notification_once(env):
    from src.models import Notification

    with env["sessions"]() as db:
        ledger = Ledger(
            user_id=env["a"], external_id="turns-shared", name="Personal", currency="TWD"
        )
        db.add(ledger)
        db.flush()
        lid = ledger.id
        db.add(LedgerMember(ledger_id=lid, user_id=env["a"], role="owner"))
        db.commit()
    create(env, scoringLedgerId="turns-shared")
    with env["sessions"]() as db:
        db.add(LedgerMember(ledger_id=lid, user_id=env["b"], role="editor"))
        db.commit()
    assert current(env)["scoringAvailable"] is False
    assert current(env)["scoringAvailable"] is False
    with env["sessions"]() as db:
        notices = [
            r
            for r in db.scalars(select(Notification).where(Notification.user_id == env["a"]))
            if r.payload_json.get("type") == "farm_scoring_ledger"
        ]
        assert len(notices) == 1 and notices[0].category == "farm"


def test_farm_nickname_is_separate_from_profile_and_has_no_id_fallback(env):
    result = create(env)
    assert result["social"]["nicknameRequired"]
    result = env["client"].patch(
        API + "/farm", headers=env["ah"], json={"nickname": "  小安的菜園  "}
    )
    assert result.status_code == 200
    assert result.json()["social"]["nickname"] == "小安的菜園"
    assert not result.json()["social"]["nicknameRequired"]
    create(env, "bh")
    farms = env["client"].get(API + "/farms", headers=env["bh"]).json()
    assert len(farms) == 1 and farms[0]["displayName"] == "小安的菜園"
    unnamed = env["client"].get(API + "/farms", headers=env["ah"]).json()[0]
    assert unnamed["displayName"] == "尚未取暱稱的農友"
    for invalid in ("", "   ", "a" * 21, "小安\n菜園", 12, None):
        rejected = env["client"].patch(API + "/farm", headers=env["ah"], json={"nickname": invalid})
        assert rejected.status_code == 400
        assert current(env)["social"]["nickname"] == "小安的菜園"


def test_events_show_counterparty_and_exclude_own_market_receipts(env):
    create(env)
    create(env, "bh")
    env["client"].patch(API + "/farm", headers=env["ah"], json={"nickname": "小安"})
    env["client"].patch(API + "/farm", headers=env["bh"], json={"nickname": "小白"})
    with env["sessions"]() as db:
        for actor, owner, kind in (
            (env["a"], env["b"], "react"),
            (env["a"], env["a"], "market_sale"),
        ):
            db.add(
                GameEvent(
                    id=str(uuid4()),
                    actor_id=actor,
                    owner_id=owner,
                    kind=kind,
                    data={},
                    created_at=env["clock"][0],
                )
            )
        db.commit()
    outgoing = env["client"].get(API + "/events", headers=env["ah"]).json()
    incoming = env["client"].get(API + "/events", headers=env["bh"]).json()
    assert len(outgoing) == len(incoming) == 1
    assert outgoing[0]["displayName"] == "小白" and outgoing[0]["outgoing"]
    assert outgoing[0]["counterpartyId"] == env["b"]
    assert incoming[0]["displayName"] == "小安" and not incoming[0]["outgoing"]


def test_cloud_cheats_require_admin_and_replay_only_mutates_own_farm(env):
    before = create(env)
    other = create(env, "bh")
    body = {"requestId": str(uuid4()), "command": "addCoins", "value": 1000}
    denied = env["client"].post(API + "/debug/actions", headers=env["ah"], json=body)
    assert denied.status_code == 403
    with env["sessions"]() as db:
        db.get(User, env["a"]).is_admin = True
        db.commit()
    first = env["client"].post(API + "/debug/actions", headers=env["ah"], json=body)
    assert first.status_code == 200, first.text
    assert first.json()["balance"] == before["balance"] + 1000
    assert first.json()["social"]["canCheat"]
    again = env["client"].post(API + "/debug/actions", headers=env["ah"], json=body)
    assert again.json() == first.json()
    assert current(env, "bh")["balance"] == other["balance"]
    _, web = env["register"]("farm-a@test.local", "web")
    assert env["client"].post(API + "/debug/actions", headers=web, json=body).status_code == 403


@pytest.mark.parametrize(
    "command,params",
    [
        ("setLevel", {"value": 8}),
        ("refillWater", {}),
        ("giveAllSeeds", {"value": 10}),
        ("giveAllItems", {"value": 5}),
        ("giveAllCrops", {"value": 10}),
        ("addCapacity", {"value": 50}),
        ("clearInventory", {}),
        ("unlockPlots", {"value": 24}),
        ("plantEmpty", {"cropId": "radish"}),
        ("ripenAll", {}),
        ("careAll", {}),
        ("clearAllPlots", {}),
        ("fastForward", {"value": 3600}),
        ("setWeather", {"kind": "rain"}),
        ("setDaylight", {"value": False}),
        ("resetFarm", {}),
    ],
)
def test_cloud_toolbox_commands_are_executable(env, command, params):
    create(env)
    create(env, "bh")
    with env["sessions"]() as db:
        db.get(User, env["a"]).is_admin = True
        db.commit()
    r = env["client"].post(
        API + "/debug/actions",
        headers=env["ah"],
        json={"requestId": str(uuid4()), "command": command, **params},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["ownerId"] == env["a"]
    if command == "setLevel":
        assert result["state"]["xp"] > 0
    if command == "unlockPlots":
        assert len(result["plots"]) == 24
    if command == "setWeather":
        assert result["weather"]["precipitation"] == 3
        assert current(env, "bh")["weather"] is None
    if command == "setDaylight":
        assert result["weather"]["isDay"] is False


def test_new_farm_nickname_validation_is_atomic(env):
    rejected = env["client"].post(API + "/farm", headers=env["ah"], json={"nickname": "  "})
    assert rejected.status_code == 400
    assert current(env) is None
    assert create(env, nickname="  小安  ")["social"]["nickname"] == "小安"


def test_cloud_cheats_populated_plots_reset_and_weather_refresh(env):
    initial = create(env, nickname="小安")
    with env["sessions"]() as db:
        db.get(User, env["a"]).is_admin = True
        db.commit()

    def cheat(command, **params):
        r = env["client"].post(
            API + "/debug/actions",
            headers=env["ah"],
            json={"requestId": str(uuid4()), "command": command, **params},
        )
        assert r.status_code == 200, r.text
        return r.json()

    cheat("giveAllCrops", value=10)
    result = cheat("plantEmpty", cropId="radish")
    assert all(p["cropId"] == "radish" for p in result["plots"])
    result = cheat("ripenAll")
    assert all(p["growth"] == 1 for p in result["plots"])
    result = cheat("careAll")
    assert all(p["moisture"] == 100 and not p["withered"] for p in result["plots"])
    cheat("setDaylight", value=False)
    result = cheat("setWeather", kind="storm")
    assert result["weather"]["isDay"] is False and result["weather"]["precipitation"] == 14
    # A manual refresh clears only this farm's test weather.
    # No upstream weather request is needed for this regression.
    from unittest.mock import patch

    from src.services.game import weather

    with patch.object(weather, "refresh_weather"):
        refreshed = env["client"].get(API + "/farm?refreshWeather=true", headers=env["ah"]).json()
    assert refreshed["weather"] is None
    result = cheat("resetFarm")
    assert result["social"]["nickname"] == "小安"
    assert result["balance"] == initial["balance"] and result["inventory"] == []
    assert all(p["cropId"] is None for p in result["plots"])


def test_visit_footprint_is_throttled_and_listed_for_the_owner(env):
    create(env)
    create(env, "bh")
    first = visit_action(env, "visit")
    assert first.status_code == 200, first.text
    assert first.json()["recorded"] is True
    # Walking in again inside the window leaves no second footprint.
    again = visit_action(env, "visit")
    assert again.status_code == 200 and again.json()["recorded"] is False
    incoming = env["client"].get(API + "/events", headers=env["bh"]).json()
    assert [e["action"] for e in incoming] == ["visit"]
    assert incoming[0]["actorId"] == env["a"] and not incoming[0]["outgoing"]
    env["clock"][0] += timedelta(minutes=social.VISIT_FOOTPRINT_MINUTES + 1)
    later = visit_action(env, "visit")
    assert later.json()["recorded"] is True
    assert len(env["client"].get(API + "/events", headers=env["bh"]).json()) == 2
    # No coins, quota or notification side effects, and you cannot visit yourself.
    assert visit_action(env, "visit", header="bh", owner=env["b"]).status_code == 400


def test_react_accepts_the_sunflower_and_rejects_unknown_emoji(env):
    create(env)
    create(env, "bh")
    bad = visit_action(env, "react", emoji="💩")
    assert bad.status_code == 400 and error_code(bad) == "INVALID_EMOJI"
    ok = visit_action(env, "react", emoji="🌻")
    assert ok.status_code == 200, ok.text
    events = env["client"].get(API + "/events", headers=env["bh"]).json()
    assert events[0]["emoji"] == "🌻"
