"""验收记录领域服务：签署验收后联动工序完成状态、修缮日志、归档和质量评定。

本模块还承载监理批量退回整改：
- 监理可在一次批次中勾选同一现场的多份验收报告及其连带缺陷并退回；
- 仅「已签署且未备案、未在备案中」的报告具备退回补写资格；
- 每条成功退回保存原报告快照、缺陷与整改期限，生成唯一整改回执并同步到修缮日志待办；
- 已备案报告不允许被批量操作直接打开写入；正在提交备案的报告会产生资格冲突；
- 批次内重复选同一份报告、批次重复提交时，每份有效报告只保留一条整改回执。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import (
    RECTIFICATION_BATCH_TABLE,
    RECTIFICATION_RECEIPT_TABLE,
    store,
)

# 备案中（提交备案尚未完成）标记字段；已备案则由档案归档联动写入「已归档」。
FILING_IN_PROGRESS = "备案中"
# 批量退回整改的目标状态
RETURN_STATUS = "待整改"
BATCH_RETURN_ACTION = "批量退回整改"


class AcceptanceRecordService(BaseModuleService):
    MODULE = "acceptance_record"
    ENTITY = "验收报告"
    OPERATION_TERM = "开始验收"
    LIST_FIELDS = ["验收工序", "验收标准", "验收结论", "遗留问题", "整改期限", "验收人", "验收日期", "备注说明"]
    REQUIRED_FIELDS = ["验收工序", "验收标准", "验收结论"]
    STATUS_ORDER = ["待验收", "验收中", "已签署", "待整改"]
    ACTION_RULES = {"开始验收": "验收中", "签署验收": "已签署", "退回整改": "待整改"}
    NEGATIVE_ACTIONS = []

    # ── 资格与备案状态 ──
    def is_filed(self, entry: dict[str, Any]) -> bool:
        """已备案的报告：档案入库联动标记或备案状态已完成。"""
        return bool(entry.get("已归档")) or entry.get("备案状态") == "已备案"

    def is_filing_in_progress(self, entry: dict[str, Any]) -> bool:
        return entry.get("备案状态") == FILING_IN_PROGRESS

    def return_eligibility(self, entry: dict[str, Any]) -> tuple[bool, str]:
        """返回 (是否具备批量退回补写资格, 说明)。说明同时用于整组操作后的资格解释。"""
        if self.is_filed(entry):
            return False, "报告已备案，档案已锁定，不允许批量退回打开写入"
        if self.is_filing_in_progress(entry):
            return False, "报告正在提交备案，存在资格冲突，请待备案结束后再操作"
        # 已生成回执的报告优先按回执解释（其状态通常已为待整改）
        if store.find_receipt(int(entry.get("id", 0))) is not None:
            return False, "已存在整改回执，重复提交不再生成新回执"
        if entry.get("status") != "已签署":
            return False, f"当前状态为「{entry.get('status', '')}」，仅已签署报告可退回补写"
        return True, "已签署未备案，具备退回补写资格"

    def begin_filing(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """模拟提交备案：把已签署报告置为备案中，制造与批量退回的资格冲突窗口。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if self.is_filed(entry):
            return None, "报告已备案，无需重复提交"
        if self.is_filing_in_progress(entry):
            return entry, "报告已在备案提交中"
        if entry.get("status") != "已签署":
            return None, f"当前状态为「{entry.get('status', '')}」，不可提交备案"
        resource_key = f"{self.MODULE}:{entry_id}:filing"
        if not store.acquire_resource(resource_key, wait_timeout=0):
            return None, "备案资源正在被其他操作处理，请稍后重试"
        try:
            entry["备案状态"] = FILING_IN_PROGRESS
            return entry, "备案提交中，报告暂时锁定"
        finally:
            store.release_resource(resource_key)

    def complete_filing(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """模拟备案完成：备案中报告转为已备案并锁定。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if self.is_filed(entry):
            return entry, "报告已备案"
        if not self.is_filing_in_progress(entry):
            return None, "报告未在备案中，无法完成备案"
        entry["备案状态"] = "已备案"
        entry["已归档"] = True
        return entry, "备案完成，报告已锁定"

    # ── 单条退回整改（保留原动作入口，同样只产生一条回执） ──
    def cross_module_verify(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> tuple[bool, str]:
        process = entry.get("验收工序", "")
        if action == "签署验收":
            # 1. 必须有对应的质量检查已通过
            quality_records = store.query_by_field("quality_inspect", "检查工序", process)
            if not quality_records:
                return False, f"工序「{process}」缺少质量检查记录"
            if not any(r.get("status") == "已通过" for r in quality_records):
                return False, "关联质量检查未通过，无法签署验收"
            # 2. 关联排期中工期是否合理
            schedules = store.query_by_field("construction_schedule", "计划工序", process)
            for s in schedules:
                if s.get("status") == "已延期":
                    return False, "关联排期已延期，需先获取变更签证再验收"
        if action == "退回整改":
            eligible, reason = self.return_eligibility(entry)
            if not eligible:
                return False, reason
        return True, ""

    def cross_module_effect(
        self, entry_id: int, action: str, entry: dict[str, Any]
    ) -> list[dict]:
        effects = []
        if action == "签署验收":
            process = entry.get("验收工序", "")
            # 1. 联动工序完成状态
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["status"] = "已完成"
                    ws_row["pending"] = False
                    effects.append(dict(ws_row))

            # 2. 联动修缮日志归档
            for log in store.rows("restoration_log"):
                if log.get("对应工序", "") == process:
                    log["status"] = "已归档"
                    log["pending"] = False
                    effects.append(dict(log))

            # 3. 联动施工排期里程碑更新
            for sched in store.rows("construction_schedule"):
                if sched.get("计划工序", "") == process:
                    sched["里程碑节点"] = f"{process}-验收已签署"
                    effects.append(dict(sched))

            # 4. 触发档案归档（自动创建归档条目）
            arch_rows = store.rows("archive_mgmt")
            new_arch = {
                "id": max((int(r.get("id", 0)) for r in arch_rows), default=0) + 1,
                "档案名称": f"{process}-验收报告",
                "档案类别": "验收档案",
                "形成日期": entry.get("验收日期", ""),
                "保管期限": "永久",
                "密级等级": "内部",
                "归档人": entry.get("验收人", ""),
                "归档状态": "待整理",
                "备注说明": f"验收签署#{entry_id}自动触发",
                "status": "待整理",
                "pending": True,
                "abnormal": False,
            }
            arch_rows.append(new_arch)
            effects.append(new_arch)

            # 5. 更新竣工总结质量评定
            for summary in store.rows("completion_summary"):
                if summary.get("status") in ("编制中", "已提交"):
                    if summary.get("质量评定", "") == "":
                        summary["质量评定"] = f"工序{process}已验收"
                    effects.append(dict(summary))

        elif action == "退回整改":
            process = entry.get("验收工序", "")
            for ws_row in store.rows("work_sequence"):
                if ws_row.get("工序名称", "") == process:
                    ws_row["abnormal"] = True
                    effects.append(dict(ws_row))
            # 单条退回同样落一条整改回执，保证每份报告至多一条；
            # 快照恢复退回前的「已签署」结论，保存原报告而不是退回后的状态。
            frozen = dict(entry)
            frozen["status"] = "已签署"
            self._ensure_receipt(
                frozen, batch_no=f"single-{entry_id}-{int(time.time())}",
                defects=[entry.get("遗留问题", "")], site="", remark="",
            )
        return effects

    # ── 批量退回整改 ──
    def batch_return(
        self,
        acceptance_ids: list[int],
        *,
        defects_by_report: dict[str, str] | None = None,
        site: str = "",
        deadline: str = "",
        remark: str = "",
    ) -> dict[str, Any]:
        """批量退回同一现场的多份验收报告。

        - 入参去重：同一报告在批次内重复勾选只处理一次；
        - 资格校验：已备案/备案中/非已签署/已有回执的报告保留原结论并记录原因；
        - 成功项：保存原报告快照、缺陷与整改期限，生成整改回执并同步修缮日志待办；
        - 重复提交：每份有效报告只保留一条整改回执。
        """
        defects_by_report = defects_by_report or {}
        # 去重且保持顺序
        unique_ids = list(dict.fromkeys(int(i) for i in acceptance_ids))
        duplicated = len(acceptance_ids) - len(unique_ids)

        batch_no = f"BR-{time.strftime('%Y%m%d%H%M%S')}-{len(store.rows(RECTIFICATION_BATCH_TABLE)) + 1}"
        succeeded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        eligibility_after: list[dict[str, Any]] = []

        # 第一阶段：无锁预校验，给出每条失败原因，失败项保持原结论不动
        plans: list[tuple[dict[str, Any], list[str], str]] = []
        for report_id in unique_ids:
            entry = store.find(self.MODULE, report_id)
            if entry is None:
                failed.append({
                    "验收报告id": report_id, "验收工序": "", "原结论": "",
                    "原因": "验收报告不存在或已归档",
                })
                continue
            eligible, reason = self.return_eligibility(entry)
            if not eligible:
                failed.append({
                    "验收报告id": report_id,
                    "验收工序": entry.get("验收工序", ""),
                    "原结论": entry.get("status", ""),
                    "原因": reason,
                })
                continue
            raw_defects = defects_by_report.get(str(report_id), "") or entry.get("遗留问题", "") or ""
            defects = [d.strip() for d in str(raw_defects).replace("；", ";").split(";") if d.strip()]
            if not defects:
                defects = [entry.get("验收结论", "")]
            report_deadline = str(deadline or entry.get("整改期限", "")).strip()
            if not report_deadline:
                failed.append({
                    "验收报告id": report_id,
                    "验收工序": entry.get("验收工序", ""),
                    "原结论": entry.get("status", ""),
                    "原因": "缺少整改期限，无法退回",
                })
                continue
            plans.append((entry, defects, report_deadline))

        # 第二阶段：逐份加锁执行，避免与提交备案并发产生资格冲突
        for entry, defects, report_deadline in plans:
            report_id = int(entry["id"])
            resource_key = f"{self.MODULE}:{report_id}"
            if not store.acquire_resource(resource_key, wait_timeout=0):
                failed.append({
                    "验收报告id": report_id,
                    "验收工序": entry.get("验收工序", ""),
                    "原结论": entry.get("status", ""),
                    "原因": "报告正在被其他操作处理（如提交备案），资源冲突未退回",
                })
                continue
            try:
                # 拿到锁后二次确认资格，防止锁等待期间状态变化
                latest = store.find(self.MODULE, report_id)
                eligible, reason = self.return_eligibility(latest)
                if not eligible:
                    failed.append({
                        "验收报告id": report_id,
                        "验收工序": latest.get("验收工序", ""),
                        "原结论": latest.get("status", ""),
                        "原因": reason,
                    })
                    continue
                receipt = self._ensure_receipt(
                    latest, batch_no=batch_no, defects=defects,
                    site=site, remark=remark, deadline=report_deadline,
                )
                latest["status"] = RETURN_STATUS
                latest["pending"] = True
                latest["abnormal"] = True
                latest["整改期限"] = report_deadline
                latest["连带缺陷"] = "；".join(defects)
                latest["整改回执编号"] = receipt["回执编号"]
                # 联动工序异常标记
                for ws_row in store.rows("work_sequence"):
                    if ws_row.get("工序名称", "") == latest.get("验收工序", ""):
                        ws_row["abnormal"] = True
                succeeded.append({
                    "验收报告id": report_id,
                    "验收工序": latest.get("验收工序", ""),
                    "回执编号": receipt["回执编号"],
                    "整改期限": report_deadline,
                    "连带缺陷": defects,
                })
            finally:
                store.release_resource(resource_key)

        # 第三阶段：登记批次台账，并解释整组操作后每份报告的补写资格
        batch_row = {
            "id": max((int(r.get("id", 0)) for r in store.rows(RECTIFICATION_BATCH_TABLE)), default=0) + 1,
            "批次编号": batch_no,
            "现场": site,
            "提交报告数": len(unique_ids),
            "批次内重复份数": duplicated,
            "成功份数": len(succeeded),
            "失败份数": len(failed),
            "成功明细": succeeded,
            "失败明细": failed,
            "备注": remark,
            "创建时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        store.rows(RECTIFICATION_BATCH_TABLE).append(batch_row)

        for report_id in unique_ids:
            entry = store.find(self.MODULE, report_id)
            if entry is None:
                continue
            eligible, reason = self.return_eligibility(entry)
            receipt = store.find_receipt(report_id)
            eligibility_after.append({
                "验收报告id": report_id,
                "验收工序": entry.get("验收工序", ""),
                "当前结论": entry.get("status", ""),
                "可补写": eligible or (receipt is not None and not receipt.get("已关闭", False)),
                "说明": "已生成整改回执，在回执关闭前可到对应施工班次补写整改日志"
                if receipt is not None and not receipt.get("已关闭", False) else reason,
                "回执编号": receipt.get("回执编号") if receipt else "",
            })

        return {
            "批次编号": batch_no,
            "提交报告数": len(unique_ids),
            "批次内重复份数": duplicated,
            "成功份数": len(succeeded),
            "失败份数": len(failed),
            "成功明细": succeeded,
            "失败明细": failed,
            "资格说明": eligibility_after,
        }

    def _ensure_receipt(
        self,
        entry: dict[str, Any],
        *,
        batch_no: str,
        defects: list[str],
        site: str,
        remark: str,
        deadline: str = "",
    ) -> dict[str, Any]:
        """为报告创建或取回唯一整改回执（幂等：重复提交不产生第二条）。"""
        report_id = int(entry["id"])
        existing = store.find_receipt(report_id)
        if existing is not None:
            return existing
        report_deadline = deadline or str(entry.get("整改期限", ""))
        receipt_rows = store.rows(RECTIFICATION_RECEIPT_TABLE)
        receipt = {
            "id": max((int(r.get("id", 0)) for r in receipt_rows), default=0) + 1,
            "回执编号": f"RC-{report_id}-{int(time.time())}",
            "批次编号": batch_no,
            "现场": site,
            "验收报告id": report_id,
            "验收工序": entry.get("验收工序", ""),
            "原报告快照": dict(entry),
            "连带缺陷": list(defects),
            "整改期限": report_deadline,
            "status": "待整改",
            "已关闭": False,
            "整改日志id": None,
            "备注": remark,
            "创建时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        receipt_rows.append(receipt)
        return receipt

    # ── 整改待办与回执查询 ──
    def rectification_overview(self) -> dict[str, Any]:
        """修缮日志页使用：解释每份报告的补写资格并列出待办回执。"""
        eligibility: list[dict[str, Any]] = []
        for entry in store.rows(self.MODULE):
            eligible, reason = self.return_eligibility(entry)
            receipt = store.find_receipt(int(entry.get("id", 0)))
            has_open_receipt = receipt is not None and not receipt.get("已关闭", False)
            eligibility.append({
                "验收报告id": entry.get("id"),
                "验收工序": entry.get("验收工序", ""),
                "当前结论": entry.get("status", ""),
                "备案状态": entry.get("备案状态", "已备案" if self.is_filed(entry) else "未备案"),
                "可补写": eligible or has_open_receipt,
                "回执编号": receipt.get("回执编号") if receipt else "",
                "说明": "已生成整改回执，在回执关闭前可到对应施工班次补写整改日志"
                if has_open_receipt else reason,
            })

        receipts = [
            {
                "回执编号": r.get("回执编号"),
                "批次编号": r.get("批次编号"),
                "验收报告id": r.get("验收报告id"),
                "验收工序": r.get("验收工序"),
                "连带缺陷": r.get("连带缺陷"),
                "整改期限": r.get("整改期限"),
                "已关闭": r.get("已关闭"),
                "整改日志id": r.get("整改日志id"),
            }
            for r in store.rows(RECTIFICATION_RECEIPT_TABLE)
        ]
        pending = [r for r in receipts if not r["已关闭"]]
        return {
            "待办回执": pending,
            "全部回执": receipts,
            "批次记录": [dict(r) for r in store.rows(RECTIFICATION_BATCH_TABLE)],
            "资格说明": eligibility,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        base["open_receipts"] = sum(
            1 for r in store.rows(RECTIFICATION_RECEIPT_TABLE) if not r.get("已关闭", False)
        )
        base["filing_in_progress"] = sum(
            1 for row in store.rows(self.MODULE) if self.is_filing_in_progress(row)
        )
        return base
