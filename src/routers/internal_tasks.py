"""内部排程任务的手动触发端点(MOZE_FEATURE_GAP_SD.md §2.2/§2.3 Phase 1)。

`services.recurring_materializer` 平时由 main.py 的周期性 asyncio loop 调用,
这里额外暴露一个 admin-only endpoint,给:
  - 外部 cron(不想在本进程里维护 asyncio loop 时,改成外部定时 curl 这个
    endpoint,文档 §2.2 提到的"或外部 cron 打一个 /internal/tasks/
    materialize-recurring 端点"就是这个)
  - 手动 / 测试环境立即触发一次物化,不用等下一个 loop tick

要求 admin scope,不对普通用户开放 —— 这是运维操作,不是业务 API。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin_user
from ..models import User
from ..services import recurring_materializer

router = APIRouter()


@router.post("/tasks/materialize-recurring")
def materialize_recurring(
    _admin: User = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> dict:
    return recurring_materializer.materialize_all_due(db)
