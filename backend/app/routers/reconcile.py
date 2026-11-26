from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.slots import SLOT_POLICY

router = APIRouter(tags=["对账"])


@router.get("/对账")
def reconcile():
    with db() as conn:
        sends = conn.execute("SELECT COUNT(*) c FROM sends").fetchone()["c"]
        ok = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已送达'").fetchone()["c"]
        fail = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已失败'").fetchone()["c"]
        fo = conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已切备'").fetchone()["c"]
        receipts = conn.execute("SELECT COUNT(*) c FROM receipts").fetchone()["c"]
        receipt_ok = conn.execute("SELECT COUNT(*) c FROM receipts WHERE result='成功'").fetchone()["c"]
        breaks = conn.execute("SELECT COUNT(*) c FROM break_events WHERE action='切备'").fetchone()["c"]
        # 槽位闸对账面：溢出桶、拒投桶、按通道在途桶，与通道册/投递闸同一判定
        overflow = conn.execute("SELECT COUNT(*) c FROM overflow_tickets").fetchone()["c"]
        rejected = conn.execute("SELECT COUNT(*) c FROM dispatch_rejections").fetchone()["c"]
        rejected_full = conn.execute(
            "SELECT COUNT(*) c FROM dispatch_rejections WHERE reason='槽位已满'"
        ).fetchone()["c"]
        inflight_total = conn.execute(
            "SELECT COUNT(*) c FROM sends WHERE status='投递中'"
        ).fetchone()["c"]
        inflight_buckets = rows_to_list(
            conn.execute(
                """
                SELECT c.id AS 通道编号, c.name AS 通道, c.concurrency_limit AS 槽位上限,
                       COUNT(s.id) AS 在途占用,
                       c.concurrency_limit - COUNT(s.id) AS 剩余名额
                FROM channels c
                LEFT JOIN sends s
                  ON s.status='投递中'
                 AND COALESCE(s.failover_channel_id, s.channel_id)=c.id
                GROUP BY c.id ORDER BY c.role, c.id
                """
            ).fetchall()
        )
        over_limit = [b for b in inflight_buckets if b["在途占用"] > b["槽位上限"]]
    return {
        "投递申请数": sends,
        "已送达": ok,
        "已失败": fail,
        "已切备在途": fo,
        "回执条数": receipts,
        "回执成功": receipt_ok,
        "切备次数": breaks,
        "送达与回执差": ok - receipt_ok,
        # —— 并发槽位闸口径（硬闸拒收-零溢出）——
        "槽位口径": SLOT_POLICY,
        "在途总数": inflight_total,
        "路况在途桶": inflight_buckets,
        "轧差溢出桶": overflow,          # 口径A 恒为 0；非 0 即超槽事故
        "溢出在途": 0,                   # 溢出票不存在，溢出在途恒 0
        "拒投总数": rejected,
        "满槽拒投数": rejected_full,
        "超槽通道数": len(over_limit),   # 不变量：恒 0
    }
