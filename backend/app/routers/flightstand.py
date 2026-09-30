"""机位分配接口：维护机位，提供状态看板，并覆盖分配机位、释放机位、登记检修等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.flightstand import STATUSES, FlightstandService

router = APIRouter(prefix="/api/flightstand", tags=["机位分配"])

service = FlightstandService()

LIST_FIELDS = ["机位编号", "机位类型", "可停机型", "廊桥配置", "引导线状态", "占用状态", "分配航段", "机位状态"]


def _parse_filters(keyword: str | None, status: str | None) -> tuple[str | None, str | None]:
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"状态「{status}」不合法，可选：{'、'.join(STATUSES)}")
    return keyword, status


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按机位编号检索"),
    status: str | None = Query(default=None, description="空闲、已分配、占用中、检修中"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按机位编号与状态过滤机位列表；列表与看板、详情共用同一份服务端推导结果。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    keyword, status = _parse_filters(keyword, status)
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/board")
def board(
    keyword: str | None = Query(default=None, description="按机位编号检索"),
    status: str | None = Query(default=None, description="空闲、已分配、占用中、检修中"),
) -> dict[str, Any]:
    """机位状态看板：卡片明细与占用统计都由服务端从分配明细现算。"""
    keyword, status = _parse_filters(keyword, status)
    return service.board(keyword=keyword, status=status)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出机位分配清单：返回当前全量数据，口径与看板一致。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "flightstand", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条机位明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"机位 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条机位，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="机位已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条机位执行分配/释放/检修；冲突（机位被占、航段已落库他人）时返回原因并保持原占用。"""
    entry, message, conflict = service.run_action(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message, conflict=conflict)
    return ActionResult(ok=True, message=message, entry=entry)
