"""Catch up closed weeks independent of whether a player opens the App."""

from sqlalchemy import select

from ...models import User
from .engine import FarmService, transaction
from .models import GameFarm
from .social import build_boards


def run(db):
    ids = list(
        db.scalars(
            select(GameFarm.user_id)
            .join(User, User.id == GameFarm.user_id)
            .where(GameFarm.enabled.is_(True), User.is_enabled.is_(True))
        )
    )
    if not ids:
        return {"farms": 0}
    # Ordered all-player lock prevents ranking snapshots racing actions/settlements.
    with transaction(db, ids):
        svc = FarmService(db)
        for uid in ids:
            farm = db.get(GameFarm, uid)
            user = db.get(User, uid)
            if farm and farm.enabled and user and user.is_enabled:
                svc.advance(farm)
        svc.market_prices()
        build_boards(svc)
    return {"farms": len(ids)}
