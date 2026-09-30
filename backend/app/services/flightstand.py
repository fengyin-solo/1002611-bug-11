"""机位分配业务规则。

设计要点（对应交接时发现的几处毛病）：

1. 唯一事实来源：服务里维护一份「分配明细账本」``_allocations``，每条记录是
   某航段对某机位的一次生效分配。机位上的 机位状态/占用状态/引导线状态/分配航段
   以及看板占用张数，全部由账本「重算」得到，列表、详情、看板因此读到的是同一份结果。
2. 动作只做原子的「检查并落库」：模块级锁内完成校验与写入，落库成功后立即把
   结果物化到机位行，前端只在服务端返回成功后再拉取，不做乐观更新。
3. 同一航段重复提交只生效第一次（幂等），已被占用 / 正在检修的机位不允许再分配；
   并发分配同一机位时按先落库者胜，后一次返回冲突并保持原占用。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "flightstand"
REQUIRED_FIELDS = ["机位编号", "机位类型", "可停机型"]

STATUS_FREE = "空闲"
STATUS_ALLOCATED = "已分配"
STATUS_MAINTENANCE = "检修中"
VALID_STATUSES = [STATUS_FREE, STATUS_ALLOCATED, STATUS_MAINTENANCE]

# 引导线是占用的可视化：空闲时引导线空闲，占用时引导线同步翻新，检修时封锁。
GUIDE_FREE = "空闲"
GUIDE_BUSY = "占用"
GUIDE_LOCKED = "封锁"

ACTION_ALLOCATE = "分配机位"
ACTION_RELEASE = "释放机位"
ACTION_MAINTAIN = "登记检修"
ACTION_REOPEN = "恢复开放"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_segment(segment: str) -> str:
    return segment.strip()


class FlightstandService:
    """机位状态看板服务：机位是主数据，分配明细账本是唯一可变的占用事实。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._ready = False
        # 分配明细账本：状态为 生效中 的记录才参与占用，释放后改为 已释放。
        self._allocations: list[dict[str, Any]] = []
        self._allocation_seq = 0
        # 已分配机位允许直接登记的字段
        self._stand_fields = [
            "机位编号", "机位类型", "可停机型", "廊桥配置",
            "引导线状态", "占用状态", "分配航段", "机位状态", "对应航班",
        ]

    # ------------------------------------------------------------------
    # 启动与重算
    # ------------------------------------------------------------------
    def _bootstrap(self) -> None:
        """首访时把种子里已有的占用（status=已分配）转成生效分配，保证口径统一。"""
        if self._ready:
            return
        self._ready = True
        for stand in store.rows(MODULE):
            # 种子里直接给成检修中的机位，补上标记，避免被重算回空闲。
            if str(stand.get("status")) == STATUS_MAINTENANCE:
                stand["_检修标记"] = True
            segment = str(stand.get("分配航段") or "").strip()
            if str(stand.get("status")) == STATUS_ALLOCATED and segment:
                self._allocations.append({
                    "id": self._next_id(),
                    "机位id": int(stand.get("id", 0)),
                    "机位编号": str(stand.get("机位编号") or ""),
                    "分配航段": segment,
                    "对应航班": str(stand.get("对应航班") or segment.split("/", 1)[0]),
                    "操作员": "交接班",
                    "分配时间": str(stand.get("分配时间") or _now()),
                    "释放时间": "",
                    "状态": "生效中",
                })
        self._reconcile()

    def _next_id(self) -> int:
        self._allocation_seq += 1
        return self._allocation_seq

    def _active_for_stand(self, stand_id: int) -> dict[str, Any] | None:
        for alloc in self._allocations:
            if int(alloc["机位id"]) == stand_id and alloc["状态"] == "生效中":
                return alloc
        return None

    def _active_for_segment(self, segment: str) -> dict[str, Any] | None:
        for alloc in self._allocations:
            if alloc["状态"] == "生效中" and _normalize_segment(str(alloc["分配航段"])) == segment:
                return alloc
        return None

    def _active_allocations(self) -> list[dict[str, Any]]:
        return [alloc for alloc in self._allocations if alloc["状态"] == "生效中"]

    def _materialize(self, stand: dict[str, Any]) -> None:
        """把机位状态、占用、引导线、航段统一写回机位行——所有读口径都从这里来。"""
        stand_id = int(stand.get("id", 0))
        manual_maintenance = stand.get("_检修标记") is True
        active = None if manual_maintenance else self._active_for_stand(stand_id)

        if active is not None:
            stand["status"] = STATUS_ALLOCATED
            stand["机位状态"] = STATUS_ALLOCATED
            stand["占用状态"] = "占用"
            stand["引导线状态"] = GUIDE_BUSY
            stand["分配航段"] = active["分配航段"]
            stand["对应航班"] = active["对应航班"]
            stand["pending"] = True
            stand["abnormal"] = False
        elif manual_maintenance:
            stand["status"] = STATUS_MAINTENANCE
            stand["机位状态"] = STATUS_MAINTENANCE
            stand["占用状态"] = STATUS_FREE
            stand["引导线状态"] = GUIDE_LOCKED
            stand["分配航段"] = ""
            stand["对应航班"] = ""
            stand["pending"] = False
            stand["abnormal"] = True
        else:
            stand["status"] = STATUS_FREE
            stand["机位状态"] = STATUS_FREE
            stand["占用状态"] = STATUS_FREE
            stand["引导线状态"] = GUIDE_FREE
            stand["分配航段"] = ""
            stand["对应航班"] = ""
            stand["pending"] = True
            stand["abnormal"] = False

    def _reconcile(self) -> None:
        """按账本逐机位重算，看板占用张数、占用标记随之与分配明细保持一致。"""
        for stand in store.rows(MODULE):
            self._materialize(stand)

    def _public_stand(self, stand: dict[str, Any]) -> dict[str, Any]:
        result = {field: stand.get(field, "") for field in self._stand_fields}
        result["id"] = int(stand.get("id", 0))
        result["status"] = stand.get("status", STATUS_FREE)
        result["pending"] = bool(stand.get("pending"))
        result["abnormal"] = bool(stand.get("abnormal"))
        active = self._active_for_stand(int(stand.get("id", 0)))
        result["当前分配"] = active or None
        return result

    def _public_allocation(self, alloc: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(alloc["id"]),
            "机位id": int(alloc["机位id"]),
            "机位编号": alloc["机位编号"],
            "分配航段": alloc["分配航段"],
            "对应航班": alloc["对应航班"],
            "操作员": alloc["操作员"],
            "分配时间": alloc["分配时间"],
            "释放时间": alloc["释放时间"],
            "状态": alloc["状态"],
        }

    # ------------------------------------------------------------------
    # 读：列表 / 看板 / 详情，同一份账本重算后的结果
    # ------------------------------------------------------------------
    def board(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._bootstrap()
            self._reconcile()
            stands = [self._public_stand(stand) for stand in store.rows(MODULE)]

            if keyword:
                word = keyword.strip()
                stands = [
                    stand for stand in stands
                    if word in str(stand.get("机位编号", ""))
                    or word in str(stand.get("分配航段", ""))
                    or word in str(stand.get("对应航班", ""))
                ]
            if status:
                stands = [stand for stand in stands if stand.get("status") == status]
            stands.sort(key=lambda item: str(item.get("机位编号", "")))

            all_stands = [self._public_stand(stand) for stand in store.rows(MODULE)]
            active = [self._public_allocation(alloc) for alloc in self._active_allocations()]
            active.sort(key=lambda item: int(item["id"]))

            def count(target: str) -> int:
                return sum(1 for stand in all_stands if stand["status"] == target)

            return {
                "stands": stands,
                "allocations": active,
                "stats": {
                    "总机位数": len(all_stands),
                    "空闲": count(STATUS_FREE),
                    "已分配": count(STATUS_ALLOCATED),
                    "检修中": count(STATUS_MAINTENANCE),
                    # 占用张数跟着分配明细（生效中）重算，不允许单独维护。
                    "占用张数": len(active),
                },
            }

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        data = self.board(keyword=keyword, status=status)
        rows = data["stands"]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        with self._lock:
            self._bootstrap()
            self._reconcile()
            stand = store.find(MODULE, entry_id)
            if stand is None:
                return None
            detail = self._public_stand(stand)
            history = [
                self._public_allocation(alloc)
                for alloc in self._allocations
                if int(alloc["机位id"]) == entry_id
            ]
            history.sort(key=lambda item: int(item["id"]), reverse=True)
            detail["分配明细"] = history
            return detail

    # ------------------------------------------------------------------
    # 机位登记
    # ------------------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with self._lock:
            self._bootstrap()
            rows = store.rows(MODULE)
            code = str(values["机位编号"]).strip()
            if any(str(row.get("机位编号")) == code for row in rows):
                return None, [f"机位编号 {code} 已存在"]
            entry: dict[str, Any] = {
                "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
                "机位编号": code,
                "机位类型": str(values.get("机位类型") or "").strip(),
                "可停机型": str(values.get("可停机型") or "").strip(),
                "廊桥配置": str(values.get("廊桥配置") or "无廊桥").strip(),
                "对应航班": "",
                "_检修标记": False,
            }
            rows.append(entry)
            self._materialize(entry)
            return self._public_stand(entry), []

    # ------------------------------------------------------------------
    # 动作：分配 / 释放 / 检修 / 恢复
    # ------------------------------------------------------------------
    def allocate(
        self,
        entry_id: int,
        segment: str,
        *,
        flight: str = "",
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str, bool, bool]:
        """返回 (机位, 提示, conflict, duplicated)。成功/幂等都返回机位。"""
        segment = _normalize_segment(segment)
        if not segment:
            return None, "分配航段不能为空", False, False
        with self._lock:
            self._bootstrap()
            stand = store.find(MODULE, entry_id)
            if stand is None:
                return None, f"机位 {entry_id} 不存在或已归档", False, False
            if stand.get("_检修标记") is True or stand.get("status") == STATUS_MAINTENANCE:
                self._reconcile()
                return self._public_stand(stand), "该机位正在检修，暂不接受分配", True, False

            # 同一航段：已在本机位生效 -> 幂等，只生效第一次；落到别的机位 -> 冲突。
            same_segment = self._active_for_segment(segment)
            if same_segment is not None:
                self._reconcile()
                current = self._public_stand(stand)
                if int(same_segment["机位id"]) == entry_id:
                    return current, f"航段 {segment} 已分配给本机位，重复请求只生效第一次", False, True
                return current, (
                    f"航段 {segment} 已分配至机位 {same_segment['机位编号']}，"
                    "同一航段只能占用一个机位"
                ), True, False

            # 机位已被其他航段占用（含并发：先落库者胜）-> 冲突，保持原占用。
            holder = self._active_for_stand(entry_id)
            if holder is not None:
                self._reconcile()
                return self._public_stand(stand), (
                    f"机位已被航段 {holder['分配航段']} 占用"
                    + (f"，操作员 {holder['操作员']} 先完成落库" if holder.get("操作员") else "")
                    + "，请改选空闲机位"
                ), True, False

            # 检查通过，落库（锁内），随后物化：列表/详情/看板下一次读到的都是新状态。
            record = {
                "id": self._next_id(),
                "机位id": entry_id,
                "机位编号": str(stand.get("机位编号") or ""),
                "分配航段": segment,
                "对应航班": flight.strip() or segment.split("/", 1)[0],
                "操作员": operator.strip() or "值班管理员",
                "分配时间": _now(),
                "释放时间": "",
                "状态": "生效中",
            }
            self._allocations.append(record)
            self._reconcile()
            return self._public_stand(stand), f"机位 {record['机位编号']} 已分配给航段 {segment}", False, False

    def release(
        self,
        entry_id: int,
        *,
        operator: str = "",
    ) -> tuple[dict[str, Any] | None, str, bool, bool]:
        with self._lock:
            self._bootstrap()
            stand = store.find(MODULE, entry_id)
            if stand is None:
                return None, f"机位 {entry_id} 不存在或已归档", False, False
            active = self._active_for_stand(entry_id)
            if active is None:
                self._reconcile()
                if stand.get("status") == STATUS_MAINTENANCE:
                    return self._public_stand(stand), "该机位正在检修，无需释放", False, False
                # 重复释放按幂等处理，引导线本就应处于空闲。
                return self._public_stand(stand), "机位当前为空闲，无需重复释放", False, True
            active["状态"] = "已释放"
            active["释放时间"] = _now()
            if operator.strip():
                active["释放操作员"] = operator.strip()
            # 物化时清掉占用与引导线，卡片回到空闲。
            self._reconcile()
            return self._public_stand(stand), f"机位 {active['机位编号']} 已释放，占用与引导线已清空", False, False

    def maintain(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            self._bootstrap()
            stand = store.find(MODULE, entry_id)
            if stand is None:
                return None, f"机位 {entry_id} 不存在或已归档"
            if self._active_for_stand(entry_id) is not None:
                self._reconcile()
                return self._public_stand(stand), "机位仍有航段占用，请先释放再登记检修"
            stand["_检修标记"] = True
            self._reconcile()
            return self._public_stand(stand), "机位已登记检修，引导线封锁"

    def reopen(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        with self._lock:
            self._bootstrap()
            stand = store.find(MODULE, entry_id)
            if stand is None:
                return None, f"机位 {entry_id} 不存在或已归档"
            if stand.get("_检修标记") is not True:
                self._reconcile()
                return self._public_stand(stand), "机位未处于检修状态"
            stand["_检修标记"] = False
            self._reconcile()
            return self._public_stand(stand), "机位已恢复开放，回到空闲"

    # 兼容旧入口：按动作名分发。
    def run_action(
        self,
        entry_id: int,
        action: str,
        values: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str, bool, bool]:
        values = values or {}
        if action == ACTION_ALLOCATE:
            return self.allocate(
                entry_id,
                str(values.get("分配航段") or ""),
                flight=str(values.get("对应航班") or ""),
                operator=str(values.get("操作员") or ""),
            )
        if action == ACTION_RELEASE:
            return self.release(entry_id, operator=str(values.get("操作员") or ""))
        if action == ACTION_MAINTAIN:
            entry, message = self.maintain(entry_id)
            return entry, message, False, False
        if action == ACTION_REOPEN:
            entry, message = self.reopen(entry_id)
            return entry, message, False, False
        return None, f"动作「{action}」不属于机位分配可执行范围", False, False
