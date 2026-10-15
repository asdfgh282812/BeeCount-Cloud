"""App gameplay writes; App/Web may read only the privacy-safe weekly leaderboard."""

from contextlib import contextmanager

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_any_scopes, require_scopes
from ..models import User
from ..security import SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE
from ..services.game import presence, rules
from ..services.game.engine import FarmService, fail, transaction
from ..services.game.models import GameFarm
from ..services.game.social import POLICY, farm_list, leaderboard, public_farm, social_action

router = APIRouter()
app_only = Depends(require_scopes(SCOPE_APP_WRITE))
read_scope = Depends(require_any_scopes(SCOPE_APP_WRITE, SCOPE_WEB_READ, SCOPE_WEB_WRITE))


@contextmanager
def game_tx(db, ids):
    try:
        with transaction(db, ids):
            yield FarmService(db)
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, OverflowError, AttributeError, IndexError) as exc:
        raise HTTPException(
            400, detail={"code": "INVALID_ARGUMENT", "message": "無效的農場資料或操作"}
        ) from exc


@router.get("/config", dependencies=[read_scope])
def config(user: User = Depends(get_current_user)):
    return {**rules.load_config(), "social": POLICY}


@router.get("/farm", dependencies=[app_only])
def own_farm(
    refreshWeather: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    uid = user.id
    if refreshWeather:
        from ..services.game.weather import refresh_weather

        refresh_weather(db, only_user=uid)
    with game_tx(db, [uid]) as svc:
        farm = db.get(GameFarm, uid)
        if farm is None:
            return None
        svc.advance(farm)
        return svc.snapshot(farm)


@router.post("/farm", dependencies=[app_only])
def create_farm(
    data: dict = Body(default={}),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        return svc.create(uid, data)


@router.patch("/farm", dependencies=[app_only])
def patch_farm(
    data: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        return svc.patch(svc.farm(uid, False), data)


@router.post("/actions", dependencies=[app_only])
def act(
    data: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        farm = svc.farm(uid)
        return svc.replay(uid, data.get("requestId"), data, lambda: svc.action(farm, data))


@router.get("/market", dependencies=[app_only])
def market(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        return {"prices": svc.market_prices()}


@router.get("/market/history", dependencies=[app_only])
def market_history(
    cropId: str = Query(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        return svc.market_history(cropId)


@router.get("/turnips", dependencies=[app_only])
def turnips(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    uid = user.id
    with game_tx(db, [uid]) as svc:
        farm = svc.farm(uid)
        svc.advance(farm)
        return svc.turnip_snapshot(farm)


@router.get("/leaderboard", dependencies=[read_scope])
def boards(
    board: str = "bookkeeping",
    week: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Read route never accepts/derives scores from a browser. Scheduler owns freezing.
    return leaderboard(FarmService(db), user.id, board, week)


@router.get("/farms", dependencies=[app_only])
def farms(
    filter: str = "active", db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    if filter not in ("active", "ready"):
        fail("INVALID_FILTER", "無效農場篩選", 400)
    svc = FarmService(db)
    svc.farm(user.id)
    return farm_list(svc, user.id, filter)


@router.get("/farms/{owner_id}", dependencies=[app_only])
def visit(owner_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    uid = user.id
    with game_tx(db, [uid, owner_id]) as svc:
        svc.farm(uid)
        farm = svc.farm(owner_id)
        svc.advance(farm)
        result = public_farm(svc, farm, True)
        result["turnipSellPrice"] = svc.turnip_snapshot(farm)["sellPrice"]
        return result


@router.post("/farms/{owner_id}/actions", dependencies=[app_only])
def visit_action(
    owner_id: str,
    data: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    uid = user.id
    with game_tx(db, [uid, owner_id]) as svc:
        actor = svc.farm(uid)
        owner = svc.farm(owner_id)
        payload = {**data, "ownerId": owner_id}
        return svc.replay(
            uid, data.get("requestId"), payload, lambda: social_action(svc, actor, owner, data)
        )


@router.post("/presence", dependencies=[app_only])
def heartbeat(
    data: dict = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    uid = user.id
    FarmService(db).farm(uid)
    state = data.get("state")
    device = data.get("deviceId")
    if state not in ("home", "away") or not isinstance(device, str) or not 1 <= len(device) <= 128:
        fail("INVALID_PRESENCE", "無效農場心跳", 400)
    presence.update(uid, "http:" + device, state)
    return {"isHome": presence.is_home(uid)}


@router.get("/events", dependencies=[app_only])
def events(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from datetime import timedelta

    from sqlalchemy import or_

    from ..services.game.engine import instant, iso
    from ..services.game.models import GameEvent
    from ..services.game.social import identity

    svc = FarmService(db)
    uid = user.id
    svc.farm(uid)
    rows = db.scalars(
        select(GameEvent)
        .where(
            or_(GameEvent.actor_id == uid, GameEvent.owner_id == uid),
            GameEvent.created_at >= svc.now - timedelta(days=7),
        )
        .order_by(GameEvent.created_at.desc())
        .limit(100)
    )
    result = []
    for row in rows:
        actor = db.get(GameFarm, row.actor_id)
        expires = instant(row.created_at) + timedelta(hours=12)
        result.append(
            {
                "id": row.id,
                "actorId": row.actor_id,
                "ownerId": row.owner_id,
                "displayName": identity(db, actor)["displayName"],
                "action": row.kind,
                "createdAt": iso(row.created_at),
                "returned": bool(row.data.get("returned")),
                "caught": bool(row.data.get("caught")),
                "emoji": row.data.get("emoji"),
                "returnGiftUntil": iso(expires),
                "returnGiftAllowed": row.kind == "steal"
                and row.owner_id == uid
                and row.actor_id != uid
                and not row.data.get("caught")
                and not row.data.get("returned")
                and expires > svc.now,
            }
        )
    return result
