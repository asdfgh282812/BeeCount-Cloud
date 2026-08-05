"""账本数据导入 router(/api/v1/import/*)。

设计:.docs/web-ledger-import.md
"""
from fastapi import APIRouter

from . import endpoints, simple_endpoints

router = APIRouter()
router.include_router(endpoints.router)
router.include_router(simple_endpoints.router)

__all__ = ["router"]
