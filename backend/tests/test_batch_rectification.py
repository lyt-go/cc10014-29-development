"""验收记录批量退回整改专项测试。

覆盖：
- 监理批量勾选同一现场多份已签署报告与连带缺陷并退回；
- 已备案报告不能被批量操作直接打开写入；备案中报告产生资格冲突；
- 每条退回保存原报告、缺陷与整改期限，生成唯一整改回执并同步修缮日志待办；
- 批次内重复选中、批次重复提交时每份有效报告只保留一条回执；
- 退回失败条目保留原结论并可查到原因；整组操作后的补写资格可解释；
- 只有取得实际整改回执的施工班次日志内容能被接受，审核后关闭回执。
"""
from __future__ import annotations

import pytest

from app.seed import SEED_ROWS
from app.store import RECTIFICATION_BATCH_TABLE, RECTIFICATION_RECEIPT_TABLE, store
from app.services.acceptance_record import AcceptanceRecordService
from app.services.restoration_log import RestorationLogService


@pytest.fixture(autouse=True)
def reset_store():
    store._idempotent_log.clear()
    store._resource_locks.clear()
    for name in list(store._tables.keys()):
        if name in SEED_ROWS:
            store._tables[name] = [dict(row) for row in SEED_ROWS[name]]
        else:
            store._tables[name] = []
    yield


acceptance_service = AcceptanceRecordService()
log_service = RestorationLogService()


def make_report(
    process: str,
    *,
    status: str = "已签署",
    deadline: str = "2026-12-20",
    defect: str = "瓦面残缺",
    filed: bool = False,
) -> dict:
    """构造一份验收报告（默认已签署、未备案），返回落库后的行。"""
    rows = store.rows("acceptance_record")
    entry = {
        "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
        "验收工序": process,
        "验收标准": "GB50203",
        "验收结论": "合格" if status == "已签署" else "不合格",
        "遗留问题": defect,
        "整改期限": deadline,
        "验收人": "监理张工",
        "验收日期": "2026-11-25",
        "备注说明": "批量退回测试",
        "status": status,
        "pending": status != "待整改",
        "abnormal": False,
    }
    if filed:
        entry["已归档"] = True
        entry["备案状态"] = "已备案"
    rows.append(entry)
    return entry


def make_log(process: str, receipt_no: str = "", *, date: str = "2026-12-01") -> dict:
    values = {
        "施工日期": date,
        "对应工序": process,
        "施工内容": "按连带缺陷返工修复",
        "记录人": "班组长李工",
    }
    if receipt_no:
        values["整改回执编号"] = receipt_no
    entry, missing = log_service.create_entry(values)
    assert missing == []
    return entry


# ═══════════════════════════════════════════════════════════════════
# 1. 批量退回成功路径：多份已签署报告、连带缺陷、期限与回执
# ═══════════════════════════════════════════════════════════════════

def test_batch_return_creates_one_receipt_per_report_and_syncs_log_todo():
    r1 = make_report("东厢房屋面")
    r2 = make_report("东厢房木构", defect="柱根糟朽；榫卯松动")

    result = acceptance_service.batch_return(
        [r1["id"], r2["id"]],
        defects_by_report={
            str(r1["id"]): "瓦面残缺;脊兽移位",
            str(r2["id"]): "柱根糟朽；榫卯松动",
        },
        site="东厢房现场",
        remark="监理周检连带缺陷",
    )

    assert result["成功份数"] == 2
    assert result["失败份数"] == 0
    # 每份有效报告一条回执
    receipts = store.rows(RECTIFICATION_RECEIPT_TABLE)
    assert len(receipts) == 2
    by_report = {r["验收报告id"]: r for r in receipts}
    assert set(by_report) == {r1["id"], r2["id"]}

    # 退回结果同步到修缮日志整改待办
    todos = log_service.rectification_todos()
    assert todos["待办数"] == 2
    todo_processes = {t["对应工序"] for t in todos["待办"]}
    assert todo_processes == {"东厢房屋面", "东厢房木构"}

    # 报告状态变为待整改，并记录回执编号
    assert store.find("acceptance_record", r1["id"])["status"] == "待整改"
    assert store.find("acceptance_record", r2["id"])["status"] == "待整改"

    # 每条退回保存原报告快照、缺陷与整改期限
    receipt1 = by_report[r1["id"]]
    assert receipt1["连带缺陷"] == ["瓦面残缺", "脊兽移位"]
    assert receipt1["整改期限"] == "2026-12-20"
    # 快照保留退回前的原报告结论「已签署」
    assert receipt1["原报告快照"]["status"] == "已签署"
    assert receipt1["原报告快照"]["验收工序"] == "东厢房屋面"
    receipt2 = by_report[r2["id"]]
    assert receipt2["连带缺陷"] == ["柱根糟朽", "榫卯松动"]

    # 批次台账落库
    batches = store.rows(RECTIFICATION_BATCH_TABLE)
    assert len(batches) == 1
    assert batches[0]["现场"] == "东厢房现场"
    assert batches[0]["成功明细"][0]["回执编号"]


def test_batch_return_uses_report_deadline_when_batch_deadline_blank():
    r1 = make_report("西配殿墙体", deadline="2026-12-31")
    result = acceptance_service.batch_return([r1["id"]], site="西配殿")
    receipt = store.find_receipt(r1["id"])
    assert result["成功份数"] == 1
    assert receipt["整改期限"] == "2026-12-31"


# ═══════════════════════════════════════════════════════════════════
# 2. 已备案报告不能被批量操作直接打开写入
# ═══════════════════════════════════════════════════════════════════

def test_filed_report_cannot_be_opened_by_batch_operation():
    filed = make_report("山门斗拱", filed=True)
    normal = make_report("山门台基")

    result = acceptance_service.batch_return([filed["id"], normal["id"]], site="山门现场")

    # 已备案报告失败且原结论未被改写
    assert result["成功份数"] == 1
    failed = {f["验收报告id"]: f for f in result["失败明细"]}
    assert filed["id"] in failed
    assert "备案" in failed[filed["id"]]["原因"]
    filed_row = store.find("acceptance_record", filed["id"])
    assert filed_row["status"] == "已签署"
    assert store.find_receipt(filed["id"]) is None
    # 同批次合格报告仍可退回
    assert store.find_receipt(normal["id"]) is not None


# ═══════════════════════════════════════════════════════════════════
# 3. 正在提交备案的报告出现资格冲突，失败条目保留原结论并可查原因
# ═══════════════════════════════════════════════════════════════════

def test_filing_in_progress_report_conflicts_and_keeps_original_conclusion():
    filing = make_report("大雄宝殿梁架")
    entry, msg = acceptance_service.begin_filing(filing["id"])
    assert entry is not None and "备案" in msg
    assert store.find("acceptance_record", filing["id"])["备案状态"] == "备案中"

    other = make_report("大雄宝殿隔扇")
    result = acceptance_service.batch_return([filing["id"], other["id"]], site="大雄宝殿")

    assert result["成功份数"] == 1
    failed = {f["验收报告id"]: f for f in result["失败明细"]}
    assert filing["id"] in failed
    assert "备案" in failed[filing["id"]]["原因"]
    # 原结论保持「已签署」，没有被批量操作写成待整改
    assert store.find("acceptance_record", filing["id"])["status"] == "已签署"
    assert store.find_receipt(filing["id"]) is None

    # 备案完成后报告被锁定，仍不可退回
    acceptance_service.complete_filing(filing["id"])
    again = acceptance_service.batch_return([filing["id"]], site="大雄宝殿")
    assert again["成功份数"] == 0
    assert "备案" in again["失败明细"][0]["原因"]


def test_non_signed_report_is_rejected_with_reason():
    pending = make_report("钟楼下檐", status="验收中")
    result = acceptance_service.batch_return([pending["id"]], site="钟楼")
    assert result["成功份数"] == 0
    assert "已签署" in result["失败明细"][0]["原因"]
    assert store.find("acceptance_record", pending["id"])["status"] == "验收中"


# ═══════════════════════════════════════════════════════════════════
# 4. 批次内重复选中 + 批次重复提交：每份有效报告只保留一条回执
# ═══════════════════════════════════════════════════════════════════

def test_duplicate_selection_and_resubmit_keep_single_receipt():
    r1 = make_report("藏经阁楼板")
    r2 = make_report("藏经阁栏杆")

    # 批次内重复选中同一报告
    first = acceptance_service.batch_return(
        [r1["id"], r1["id"], r2["id"], r2["id"], r2["id"]], site="藏经阁"
    )
    assert first["提交报告数"] == 2  # 去重后处理
    assert first["批次内重复份数"] == 3
    assert first["成功份数"] == 2
    assert len(store.rows(RECTIFICATION_RECEIPT_TABLE)) == 2

    # 对同一批次重复提交：不新增回执，失败明细可查到原因
    second = acceptance_service.batch_return([r1["id"], r2["id"]], site="藏经阁")
    assert second["成功份数"] == 0
    assert second["失败份数"] == 2
    assert all("回执" in f["原因"] for f in second["失败明细"])
    assert len(store.rows(RECTIFICATION_RECEIPT_TABLE)) == 2
    # 两份批次台账都保留
    assert len(store.rows(RECTIFICATION_BATCH_TABLE)) == 2


# ═══════════════════════════════════════════════════════════════════
# 5. 整组操作后日志页可解释哪些报告仍有补写资格
# ═══════════════════════════════════════════════════════════════════

def test_overview_explains_eligibility_after_group_operation():
    returned = make_report("廊庑瓦作")
    filed = make_report("廊庑地砖", filed=True)
    untouched = make_report("廊庑彩绘")  # 已签署未退回，仍具备退回资格

    acceptance_service.batch_return([returned["id"], filed["id"]], site="西廊庑")

    overview = acceptance_service.rectification_overview()
    by_id = {item["验收报告id"]: item for item in overview["资格说明"]}

    # 已退回：持有未关闭回执，可补写整改日志
    assert by_id[returned["id"]]["可补写"] is True
    assert "回执" in by_id[returned["id"]]["说明"]
    # 已备案：不可补写
    assert by_id[filed["id"]]["可补写"] is False
    assert "备案" in by_id[filed["id"]]["说明"]
    # 未参与操作的已签署报告：仍具备退回补写资格
    assert by_id[untouched["id"]]["可补写"] is True
    assert by_id[untouched["id"]]["回执编号"] == ""

    # 待办回执与批次记录可在同一视图查到
    assert len(overview["待办回执"]) == 1
    assert overview["批次记录"][0]["失败份数"] == 1


# ═══════════════════════════════════════════════════════════════════
# 6. 只有取得实际整改回执的施工班次日志能被接受
# ═══════════════════════════════════════════════════════════════════

def test_log_without_real_receipt_is_rejected():
    r1 = make_report("后罩房屋面")
    acceptance_service.batch_return([r1["id"]], site="后罩房")
    receipt = store.find_receipt(r1["id"])

    # 伪造不存在的回执编号：拒绝
    fake = make_log("后罩房屋面", receipt_no="RC-NOT-EXIST")
    entry, msg = log_service.run_action(fake["id"], "提交日志")
    assert entry is None
    assert "回执" in msg

    # 回执对应另一施工班次（工序不一致）：拒绝
    other_process_log = make_log("其他工序", receipt_no=receipt["回执编号"])
    entry, msg = log_service.run_action(other_process_log["id"], "提交日志")
    assert entry is None
    assert "施工班次" in msg or "不一致" in msg

    # 持真实且匹配的回执：接受
    valid = make_log("后罩房屋面", receipt_no=receipt["回执编号"])
    entry, msg = log_service.run_action(valid["id"], "提交日志")
    assert entry is not None, msg
    linked = store.find_receipt(r1["id"])
    assert linked["整改日志id"] == valid["id"]
    assert linked["status"] == "整改已记录"
    assert linked["已关闭"] is False  # 审核前回执不关闭

    # 审核通过后关闭回执，同一回执不能重复补写
    audited, _ = log_service.run_action(valid["id"], "审核通过")
    assert audited is not None
    assert store.find_receipt(r1["id"])["已关闭"] is True
    assert log_service.rectification_todos()["待办数"] == 0

    duplicate = make_log("后罩房屋面", receipt_no=receipt["回执编号"])
    entry, msg = log_service.run_action(duplicate["id"], "提交日志")
    assert entry is None
    assert "关闭" in msg


def test_log_beyond_deadline_is_rejected():
    r1 = make_report("值房台明", deadline="2026-12-10")
    acceptance_service.batch_return([r1["id"]], site="值房")
    receipt = store.find_receipt(r1["id"])
    late = make_log("值房台明", receipt_no=receipt["回执编号"], date="2026-12-15")
    entry, msg = log_service.run_action(late["id"], "提交日志")
    assert entry is None
    assert "整改期限" in msg


# ═══════════════════════════════════════════════════════════════════
# 7. 单条「退回整改」入口与批量入口共享回执唯一性
# ═══════════════════════════════════════════════════════════════════

def test_single_return_and_batch_return_share_single_receipt():
    r1 = make_report("耳房墙面")
    # 单条退回（基类 run_action 路径）
    entry, msg = acceptance_service.run_action(r1["id"], "退回整改")
    assert entry is not None, msg
    assert store.find_receipt(r1["id"]) is not None

    # 再走批量入口不会产生第二条回执
    result = acceptance_service.batch_return([r1["id"]], site="耳房")
    assert result["成功份数"] == 0
    assert len(store.rows(RECTIFICATION_RECEIPT_TABLE)) == 1

    # 已备案报告走单条动作同样被拦截
    filed = make_report("耳房基础", filed=True)
    entry, msg = acceptance_service.run_action(filed["id"], "退回整改")
    assert entry is None
    assert "备案" in msg
