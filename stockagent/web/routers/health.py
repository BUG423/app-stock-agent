"""系统健康 API。"""

from fastapi import APIRouter

from stockagent.core.health import collect_health

router = APIRouter()


@router.get("")
async def get_health():
    return await collect_health()
