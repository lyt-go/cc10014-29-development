"""修缮日志领域服务：每日记录施工内容和材料用量，与工序编排、质量检查联动。

整改补写规则：
- 验收记录批量退回后，整改回执同步为本模块的整改待办；
- 只有持实际整改回执（对应施工班次/工序、回执未关闭）的日志内容才能被接受；
- 日志审核通过后关闭对应回执，施工班次完成整改闭环。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RestorationLogService(BaseModuleService):
    MODULE = "restoration_log"
    ENTITY = "施工日志"
    OPERATION_TERM = "提交日志"
    LIST_FIELDS = ["施工日期", "对应工序", "施工内容", "用工数量", "材料用量", "存在问题", "记录人", "备注说明"]
    REQUIRED_FIELDS = ["施工日期", "对应工序", "施工内容"]
    STATUS_ORDER = ["待填写", "已填写", "已审核", "已归档"]
    ACTION_RULES = {"提交日志": "已填写", "审核通过": "已审核", "归档日志": "已归档"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记日志时可携带整改回执编号；回执有效性在提交时校验。"""
        receipt_no = str(values.get("整改回执编号") or "").strip()
        entry, missing = super().create_entry(values)
        if entry is not None and receipt_no:
            entry["整改回执编号"] = receipt_no
        return entry, missing

    def _find_receipt_by_no(self, receipt_no: str) -> dict[str, Any] | None:
        for row in store.rows("rectification_receipt"):
            if row.get("回执编号") == receipt_no:
                return row
        return None

    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        if action == "提交日志":
            process = entry.get("对应工序", "")
            # 关联工序必须已发布或施工中
            ws_rows = store.query_by_field("work_sequence", "工序名称", process)
            if ws_rows and all(r.get("status") not in ("已发布", "施工中", "已完成") for r in ws_rows):
                return False, f"关联工序「{process}」状态不允许提交日志"

            # 整改补写：只有取得实际整改回执的内容能被接受
            receipt_no = str(entry.get("整改回执编号") or "").strip()
            if receipt_no:
                receipt = self._find_receipt_by_no(receipt_no)
                if receipt is None:
                    return False, f"整改回执「{receipt_no}」不存在，补写内容不能被接受"
                if receipt.get("已关闭"):
                    return False, f"整改回执「{receipt_no}」已关闭，不能重复补写"
                if receipt.get("验收工序", "") != process:
                    return False, (
                        f"整改回执「{receipt_no}」对应施工班次为"
                        f"「{receipt.get('验收工序', '')}」，与本日志工序不一致"
                    )
                if str(entry.get("施工日期", "")) > str(receipt.get("整改期限", "")):
                    return False, (
                        f"补写日期晚于整改期限「{receipt.get('整改期限', '')}」，"
                        "需先由监理调整期限"
                    )
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 如果日志提到材料用量，对应材料核验单需已签认
            material_used = entry.get("材料用量", "")
            if material_used and material_used not in ("", "无", "0"):
                verified = [
                    r for r in store.rows("material_verify")
                    if r.get("材料名称", "") in material_used and r.get("status") == "已签认"
                ]
                if material_used and not verified:
                    return False, f"日志中材料「{material_used}」未经过核验签认"
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "提交日志":
            # 提交成功即把日志挂到回执上，标记整改已记录，待审核后关闭回执
            receipt_no = str(entry.get("整改回执编号") or "").strip()
            if receipt_no:
                for receipt in store.rows("rectification_receipt"):
                    if receipt.get("回执编号") == receipt_no and not receipt.get("已关闭"):
                        receipt["整改日志id"] = entry_id
                        receipt["status"] = "整改已记录"
                        effects.append(dict(receipt))
        if action == "审核通过":
            process = entry.get("对应工序", "")
            # 日志审核通过后联动排期执行中
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process and sched.get("status") == "已发布":
                    sched["status"] = "执行中"
                    effects.append(dict(sched))
            # 审核通过关闭整改回执，施工班次完成整改闭环
            receipt_no = str(entry.get("整改回执编号") or "").strip()
            if receipt_no:
                for receipt in store.rows("rectification_receipt"):
                    if receipt.get("回执编号") == receipt_no and not receipt.get("已关闭"):
                        receipt["已关闭"] = True
                        receipt["status"] = "整改完成"
                        receipt["整改日志id"] = entry_id
                        effects.append(dict(receipt))
        if action == "归档日志":
            # 检查是否可以联动工序完成
            process = entry.get("对应工序", "")
            logs_for_process = [
                r for r in store.rows(self.MODULE)
                if r.get("对应工序", "") == process
            ]
            if all(l.get("status") == "已归档" for l in logs_for_process):
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == process and ws_row.get("status") == "施工中":
                        ws_row["status"] = "已完成"
                        ws_row["pending"] = False
                        effects.append(dict(ws_row))
        return effects

    def rectification_todos(self) -> dict[str, Any]:
        """修缮日志页整改待办：来自验收批量退回、尚未关闭的整改回执。"""
        todos = []
        for receipt in store.rows("rectification_receipt"):
            todos.append({
                "回执编号": receipt.get("回执编号"),
                "批次编号": receipt.get("批次编号"),
                "现场": receipt.get("现场", ""),
                "验收报告id": receipt.get("验收报告id"),
                "对应工序": receipt.get("验收工序", ""),
                "连带缺陷": receipt.get("连带缺陷", []),
                "整改期限": receipt.get("整改期限", ""),
                "状态": receipt.get("status", ""),
                "已关闭": receipt.get("已关闭", False),
                "整改日志id": receipt.get("整改日志id"),
            })
        open_todos = [t for t in todos if not t["已关闭"]]
        return {
            "待办数": len(open_todos),
            "待办": open_todos,
            "全部回执": todos,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        # 返回按工序聚合的日志状态
        all_rows = store.rows(self.MODULE)
        by_process: dict[str, list] = {}
        for row in all_rows:
            process = row.get("对应工序", "")
            by_process.setdefault(process, []).append({
                "id": row.get("id"),
                "status": row.get("status"),
                "date": row.get("施工日期"),
            })
        base["by_process"] = by_process
        base["rectification_todos"] = self.rectification_todos()
        return base
