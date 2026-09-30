"""机位分配接口：状态看板、机位明细，以及分配/释放/检修等动作。

落库成功后列表、详情、看板读的都是服务内同一份分配明细账本重算出来的结果。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.flightstand import (
    ACTION_ALLOCATE,
    ACTION_MAINTAIN,
    ACTION_REOPEN,
    ACTION_RELEASE,
    VALID_STATUSES,
    FlightstandService,
)

router = APIRouter(prefix="/api/flightstand", tags=["机位分配"])

service = FlightstandService()

LIST_FIELDS = ["机位编号", "机位类型", "可停机型", "廊桥配置", "引导线状态", "占用状态", "分配航段", "机位状态"]


@router.get("/board")
def board(
    keyword: str | None = Query(default=None, description="按机位编号/航段/航班检索"),
    status: str | None = Query(default=None, description="空闲、已分配、检修中"),
) -> dict[str, Any]:
    """状态看板：机位卡片、生效中的分配明细与占用张数（由分配明细重算）。"""
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(VALID_STATUSES)}")
    return service.board(keyword=keyword, status=status)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按机位编号检索"),
    status: str | None = Query(default=None, description="空闲、已分配、检修中"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """列表与看板共用一份数据；没有数据时返回空页，不报错。"""
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"状态仅支持：{'、'.join(VALID_STATUSES)}")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条机位，缺字段或编号重复时说明原因而不是静默丢弃。"""
    entry, problems = service.create_entry(payload.values)
    if problems:
        return ActionResult(ok=False, message="；".join(problems))
    return ActionResult(ok=True, message="机位已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条机位执行动作。

    分配机位需要「分配航段」；占用冲突 / 航段已在别位生效时 ok=false、conflict=true，
    同一航段对同一机位重复提交只生效第一次（duplicated=true）。
    """
    values = payload.values
    action = str(values.get("action") or "").strip()

    if action == ACTION_ALLOCATE:
        entry, message, conflict, duplicated = service.allocate(
            entry_id,
            str(values.get("分配航段") or ""),
            flight=str(values.get("对应航班") or ""),
            operator=str(values.get("操作员") or ""),
        )
        ok = not conflict
        return ActionResult(
            ok=ok, message=message, entry=entry, conflict=conflict, duplicated=duplicated
        )

    if action == ACTION_RELEASE:
        entry, message, conflict, duplicated = service.release(
            entry_id, operator=str(values.get("操作员") or "")
        )
        return ActionResult(
            ok=not conflict, message=message, entry=entry, conflict=conflict, duplicated=duplicated
        )

    if action == ACTION_MAINTAIN:
        entry, message = service.maintain(entry_id)
        if entry is None:
            return ActionResult(ok=False, message=message)
        return ActionResult(ok=True, message=message, entry=entry)

    if action == ACTION_REOPEN:
        entry, message = service.reopen(entry_id)
        if entry is None:
            return ActionResult(ok=False, message=message)
        return ActionResult(ok=True, message=message, entry=entry)

    return ActionResult(ok=False, message=f"动作「{action}」不属于机位分配可执行范围")


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出机位分配清单：当前全量机位与生效中的分配明细。"""
    board_data = service.board()
    return {
        "module": "flightstand",
        "total": len(board_data["stands"]),
        "items": board_data["stands"],
        "allocations": board_data["allocations"],
        "stats": board_data["stats"],
    }


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict[str, Any]:
    """读取单条机位详情（含该位分配明细）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"机位 {entry_id} 不存在或已归档")
    return entry
