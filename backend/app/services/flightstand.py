"""机位分配业务规则。

事实来源只有一个：机位列里的「分配明细」。机位状态、占用状态、引导线状态、
分配航段都是生效中明细的推导结果，列表、详情与状态看板读到的永远是同一份口径。
分配/释放用一把锁把「校验 + 落库」收成原子操作，先落库者赢，后到者拿到冲突原因。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "flightstand"
REQUIRED_FIELDS = ["机位编号", "机位类型", "可停机型"]
OPTIONAL_FIELDS = ["廊桥配置"]
STATUS_FREE = "空闲"
STATUS_ASSIGNED = "已分配"
STATUS_OCCUPIED = "占用中"
STATUS_MAINTENANCE = "检修中"
STATUSES = [STATUS_FREE, STATUS_ASSIGNED, STATUS_OCCUPIED, STATUS_MAINTENANCE]
ACTIVE_STATUSES = {STATUS_ASSIGNED, STATUS_OCCUPIED}

DETAIL_ACTIVE = "生效"
DETAIL_RELEASED = "已释放"
GUIDE_FREE = "空闲"
GUIDE_BUSY = "占用"

ACTION_ALLOCATE = "分配机位"
ACTION_RELEASE = "释放机位"
ACTION_MAINTENANCE = "登记检修"
ACTION_RESUME = "解除检修"
ACTION_RULES = {
    ACTION_ALLOCATE: STATUS_ASSIGNED,
    ACTION_RELEASE: STATUS_FREE,
    ACTION_MAINTENANCE: STATUS_MAINTENANCE,
    ACTION_RESUME: STATUS_FREE,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class FlightstandService:
    def __init__(self) -> None:
        # 两个人同时分配同一机位时，靠这把锁保证先落库的请求生效
        self._lock = threading.RLock()

    # ---------- 读取：列表、详情、看板共用同一份推导口径 ----------

    def _active_details(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        return [d for d in row.get("allocations", []) if d.get("状态") == DETAIL_ACTIVE]

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        """把落库行推导成对外结构；任何接口都不许绕过它直接吐原始行。"""
        active = self._active_details(row)
        if active:
            status = row.get("status") if row.get("status") in ACTIVE_STATUSES else STATUS_ASSIGNED
        elif row.get("status") == STATUS_MAINTENANCE:
            status = STATUS_MAINTENANCE
        else:
            status = STATUS_FREE

        latest = active[0] if active else {}
        view: dict[str, Any] = {field: row.get(field) for field in REQUIRED_FIELDS + OPTIONAL_FIELDS}
        view.update({
            "id": row.get("id"),
            "status": status,
            "机位状态": status,
            "占用状态": GUIDE_BUSY if active else GUIDE_FREE,
            # 引导线随分配占用、随释放清空，不允许再出现残留
            "引导线状态": GUIDE_BUSY if active else GUIDE_FREE,
            "分配航段": "、".join(str(d.get("航段") or "") for d in active) or None,
            "航班号": latest.get("航班号"),
            "操作人": latest.get("操作人"),
            "分配时刻": latest.get("分配时刻"),
            "allocations": [dict(detail) for detail in row.get("allocations", [])],
            "pending": bool(active),
            "abnormal": False,
        })
        return view

    def _filtered_rows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("机位编号", ""))]
        if status:
            rows = [row for row in rows if self._view(row)["status"] == status]
        return rows

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filtered_rows(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._view(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return self._view(row) if row is not None else None

    def board(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """状态看板：占用张数每次都从分配明细现算，不做缓存计数。"""
        rows = self._filtered_rows(keyword=keyword, status=status)
        items = [self._view(row) for row in rows]
        active_detail_count = sum(len(self._active_details(row)) for row in rows)
        stats = {
            "空闲机位": sum(item["status"] == STATUS_FREE for item in items),
            "已分配机位": sum(item["status"] == STATUS_ASSIGNED for item in items),
            "占用中机位": sum(item["status"] == STATUS_OCCUPIED for item in items),
            "检修中机位": sum(item["status"] == STATUS_MAINTENANCE for item in items),
            "占用机位": sum(item["占用状态"] == GUIDE_BUSY for item in items),
            "生效分配明细": active_detail_count,
            "机位总数": len(items),
        }
        return {"stats": stats, "items": items}

    # ---------- 写入：校验与落库在同一把锁里完成 ----------

    def _find_active_by_leg(self, leg: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if any(d.get("状态") == DETAIL_ACTIVE and d.get("航段") == leg for d in row.get("allocations", [])):
                return row
        return None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with self._lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS + OPTIONAL_FIELDS})
            entry["status"] = STATUS_FREE
            entry["pending"] = False
            entry["abnormal"] = False
            entry["allocations"] = []
            rows.append(entry)
            return self._view(entry), []

    def allocate(
        self,
        entry_id: int,
        *,
        leg: str,
        flight_no: str = "",
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str, bool]:
        """返回（最新机位视图、提示语、是否冲突）。

        冲突时机位一行都不改，调用方把原因写到卡片上即可。
        """
        leg = leg.strip()
        if not leg:
            return None, "分配失败：请填写分配航段", False
        with self._lock:
            row = store.find(MODULE, entry_id)
            if row is None:
                return None, f"机位 {entry_id} 不存在或已归档", False

            # 同一航段重复提交：全表只认第一次落库的明细
            existing = self._find_active_by_leg(leg)
            if existing is not None:
                if int(existing.get("id", 0)) == entry_id:
                    return self._view(row), f"航段 {leg} 已在该机位生效，重复提交只生效第一次", False
                return (
                    None,
                    f"冲突：航段 {leg} 已分配给机位 {existing.get('机位编号')}，本次提交不生效",
                    True,
                )

            active = self._active_details(row)
            if active:
                holder = active[0].get("航段") or "未知航段"
                return None, f"冲突：机位 {row.get('机位编号')} 已被航段 {holder} 占用，不能重复分配", True
            if row.get("status") == STATUS_MAINTENANCE:
                return None, f"冲突：机位 {row.get('机位编号')} 检修中，暂不允许分配", True

            seq = len(row.get("allocations", [])) + 1
            detail = {
                "序号": seq,
                "航段": leg,
                "航班号": flight_no.strip() or None,
                "操作人": operator.strip() or None,
                "分配时刻": _now(),
                "状态": DETAIL_ACTIVE,
                "释放时刻": None,
            }
            row.setdefault("allocations", []).append(detail)
            row["status"] = STATUS_ASSIGNED
            row["pending"] = True
            return self._view(row), f"机位 {row.get('机位编号')} 已分配给航段 {leg}", False

    def release(self, entry_id: int, *, leg: str | None = None) -> tuple[dict[str, Any] | None, str, bool]:
        leg = (leg or "").strip() or None
        with self._lock:
            row = store.find(MODULE, entry_id)
            if row is None:
                return None, f"机位 {entry_id} 不存在或已归档", False
            active = self._active_details(row)
            if not active:
                return None, f"机位 {row.get('机位编号')} 当前空闲，无需释放", False

            targets = [d for d in active if leg is None or d.get("航段") == leg]
            if not targets:
                holder = active[0].get("航段") or "未知航段"
                return None, f"释放失败：该机位当前由航段 {holder} 占用", False
            for detail in targets:
                detail["状态"] = DETAIL_RELEASED
                detail["释放时刻"] = _now()
            row["status"] = STATUS_FREE
            row["pending"] = False
            # 引导线/占用状态不在这里手写，下一次读取由分配明细推导为空闲
            return self._view(row), f"机位 {row.get('机位编号')} 已释放，占用与引导线已清空", False

    def set_maintenance(self, entry_id: int, *, enter: bool) -> tuple[dict[str, Any] | None, str, bool]:
        with self._lock:
            row = store.find(MODULE, entry_id)
            if row is None:
                return None, f"机位 {entry_id} 不存在或已归档", False
            if enter:
                if self._active_details(row):
                    return None, f"冲突：机位 {row.get('机位编号')} 仍有航段占用，请先释放再登记检修", True
                row["status"] = STATUS_MAINTENANCE
                return self._view(row), f"机位 {row.get('机位编号')} 已登记检修", False
            if row.get("status") != STATUS_MAINTENANCE:
                return None, f"机位 {row.get('机位编号')} 不在检修中", False
            row["status"] = STATUS_FREE
            return self._view(row), f"机位 {row.get('机位编号')} 已解除检修，恢复空闲", False

    def run_action(
        self,
        entry_id: int,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str, bool]:
        action = str(values.get("action") or "").strip()
        if action == ACTION_ALLOCATE:
            return self.allocate(
                entry_id,
                leg=str(values.get("航段") or ""),
                flight_no=str(values.get("航班号") or ""),
                operator=str(values.get("操作人") or ""),
            )
        if action == ACTION_RELEASE:
            return self.release(entry_id, leg=str(values.get("航段") or ""))
        if action == ACTION_MAINTENANCE:
            return self.set_maintenance(entry_id, enter=True)
        if action == ACTION_RESUME:
            return self.set_maintenance(entry_id, enter=False)
        return None, f"动作「{action}」不属于机位分配可执行范围", False
