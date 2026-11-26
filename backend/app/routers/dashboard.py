from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.slots import SLOT_POLICY, all_channel_slots

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        dist = {
            "待投递": conn.execute("SELECT COUNT(*) c FROM sends WHERE status='待投递'").fetchone()["c"],
            "投递中": conn.execute("SELECT COUNT(*) c FROM sends WHERE status='投递中'").fetchone()["c"],
            "已送达": conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已送达'").fetchone()["c"],
            "已失败": conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已失败'").fetchone()["c"],
            "已切备": conn.execute("SELECT COUNT(*) c FROM sends WHERE status='已切备'").fetchone()["c"],
            "熔断通道": conn.execute("SELECT COUNT(*) c FROM channels WHERE status='熔断中'").fetchone()["c"],
        }
        inflight = rows_to_list(
            conn.execute(
                """
                SELECT s.id AS 编号, s.serial AS 投递单号, s.phone_mask AS 手机掩码, s.scene AS 场景,
                       s.status AS 状态, c.name AS 当前通道
                FROM sends s LEFT JOIN channels c ON c.id = s.channel_id
                WHERE s.status IN ('待投递','投递中','已切备')
                ORDER BY s.id DESC
                """
            ).fetchall()
        )
        # 通道态势直接挂同源槽位探针：路况在途格与通道册槽位列、投递闸同刷
        chans = all_channel_slots(conn)
        overflow = conn.execute("SELECT COUNT(*) c FROM overflow_tickets").fetchone()["c"]
        rejected = conn.execute("SELECT COUNT(*) c FROM dispatch_rejections").fetchone()["c"]
    return {
        "口径": SLOT_POLICY,
        "分布": dist,
        "在途": inflight,
        "通道": chans,
        "溢出在途": overflow,
        "拒投数": rejected,
    }
