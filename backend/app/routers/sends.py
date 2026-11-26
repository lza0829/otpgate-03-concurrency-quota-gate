from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException, Header, Response
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services.slots import (
    REASON_CHANNEL_UNAVAILABLE,
    REASON_NOT_DISPATCHABLE,
    REASON_NOT_FOUND,
    REASON_SLOT_FULL,
    channel_slots,
    load_idempotent,
    record_rejection,
    save_idempotent,
)

router = APIRouter(tags=["投递"])


class SendCreate(BaseModel):
    手机掩码: str = Field(min_length=3)
    场景: str = "登录"
    备注: str = ""


def pick_channel(conn):
    policy = conn.execute("SELECT mode FROM route_policy WHERE id=1").fetchone()["mode"]
    if policy == "强制主路":
        row = conn.execute(
            "SELECT * FROM channels WHERE role='主路' AND status='启用' LIMIT 1"
        ).fetchone()
    elif policy == "强制备路":
        row = conn.execute(
            "SELECT * FROM channels WHERE role='备路' AND status='启用' LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT * FROM channels WHERE status='启用'
            ORDER BY weight DESC, id ASC LIMIT 1
            """
        ).fetchone()
    return row


@router.get("/投递单")
def list_sends():
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT s.*, c.name AS channel_name, f.name AS failover_name
                FROM sends s
                LEFT JOIN channels c ON c.id = s.channel_id
                LEFT JOIN channels f ON f.id = s.failover_channel_id
                ORDER BY s.id DESC
                """
            ).fetchall()
        )
    return [
        {
            "编号": r["id"],
            "投递单号": r["serial"],
            "手机掩码": r["phone_mask"],
            "场景": r["scene"],
            "状态": r["status"],
            "通道": r["channel_name"] or "-",
            "切备通道": r["failover_name"] or "-",
            "创建时间": r["created_at"],
            "备注": r["note"],
        }
        for r in rows
    ]


@router.post("/投递单")
def create_send(body: SendCreate):
    with db() as conn:
        ch = pick_channel(conn)
        if not ch:
            raise HTTPException(400, "无可用通道")
        serial = f"验{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        now = now_str()
        cur = conn.execute(
            """
            INSERT INTO sends(serial, phone_mask, scene, status, channel_id, note, created_at)
            VALUES (?, ?, ?, '待投递', ?, ?, ?)
            """,
            (serial, body.手机掩码, body.场景, ch["id"], body.备注, now),
        )
        sid = cur.lastrowid
        audit(conn, "投递单", sid, "创建", serial)
        # 创建即回带目标通道槽位探针，投递入口与通道册槽位列同刷
        slots = channel_slots(conn, ch["id"])
    return {"编号": sid, "投递单号": serial, "通道": ch["name"], "槽位": slots}


@router.post("/投递单/{sid}/投递")
def dispatch_send(sid: int, response: Response, idempotency_key: str | None = Header(default=None)):
    """满槽投递互斥闸（口径A：硬闸拒收，零溢出）。

    名额判定与验码单态翻转合并为一条原子条件 UPDATE，单事务提交；
    满槽则单据留在投递前状态、同事务写拒因与事件痕、返回 409。
    同一 Idempotency-Key 重放首次存档结局。
    """
    with db() as conn:
        endpoint = f"/投递单/{sid}/投递"
        if idempotency_key:
            # 带键请求入口即取写锁：同键并行在入口串行，后者必能读到前者已提交存档，
            # 避免两者都在提交前读档落空而各自给出不同结局。
            conn.execute("BEGIN IMMEDIATE")
            archived = load_idempotent(conn, idempotency_key)
            if archived is not None:
                response.status_code = archived["status_code"]
                return archived["payload"]

        s = conn.execute("SELECT * FROM sends WHERE id=?", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, REASON_NOT_FOUND)

        ch_id = s["failover_channel_id"] or s["channel_id"]
        slot = channel_slots(conn, ch_id)
        if slot is None or slot["通道状态"] != "启用":
            # 通道不可用：不占名额，留拒痕后整笔失败回滚到投递前
            if slot is not None:
                rej = record_rejection(conn, sid, ch_id, REASON_CHANNEL_UNAVAILABLE,
                                       slot["在途占用"], slot["槽位上限"])
            else:
                rej = record_rejection(conn, sid, ch_id, REASON_CHANNEL_UNAVAILABLE, 0, 0)
            payload = {"结果": "拒投", "拒因": REASON_CHANNEL_UNAVAILABLE, "拒单": rej,
                       "槽位": slot, "口径": slot["口径"] if slot else None,
                       "明细": f"{REASON_CHANNEL_UNAVAILABLE}：整笔投递失败，未占用名额"}
            if idempotency_key:
                save_idempotent(conn, idempotency_key, endpoint, 409, payload)
            response.status_code = 409
            return payload

        if s["status"] not in ("待投递", "已切备"):
            rej = record_rejection(conn, sid, ch_id, REASON_NOT_DISPATCHABLE,
                                   slot["在途占用"], slot["槽位上限"])
            payload = {"结果": "拒投", "拒因": REASON_NOT_DISPATCHABLE, "拒单": rej,
                       "槽位": slot, "口径": slot["口径"],
                       "明细": f"{REASON_NOT_DISPATCHABLE}：当前 {s['status']}，整笔未受理"}
            if idempotency_key:
                save_idempotent(conn, idempotency_key, endpoint, 409, payload)
            response.status_code = 409
            return payload

        # 原子闸：仅当通道启用且在途占用未达上限时才翻转状态。
        # 子查询在同一写事务内求值，SQLite 写锁串行化并发投递，双投不会各读旧值。
        cur = conn.execute(
            """
            UPDATE sends
            SET status='投递中', channel_id=?, failover_channel_id=?, sent_at=?, finished_at=NULL
            WHERE id=?
              AND status IN ('待投递','已切备')
              AND (
                SELECT COUNT(*) FROM sends s2
                WHERE s2.status='投递中'
                  AND COALESCE(s2.failover_channel_id, s2.channel_id)=?
              ) < (SELECT concurrency_limit FROM channels WHERE id=?)
              AND (SELECT status FROM channels WHERE id=?)='启用'
            """,
            (ch_id, s["failover_channel_id"], now_str(), sid, ch_id, ch_id, ch_id),
        )

        if cur.rowcount == 0:
            # 闸口拦下：名额已满（或闸内状态变化）。单据不动，同事务留痕。
            slot = channel_slots(conn, ch_id)
            reason = REASON_SLOT_FULL if slot["满槽"] else REASON_NOT_DISPATCHABLE
            rej = record_rejection(conn, sid, ch_id, reason,
                                   slot["在途占用"], slot["槽位上限"])
            payload = {"结果": "拒投", "拒因": reason, "拒单": rej,
                       "槽位": slot, "口径": slot["口径"],
                       "明细": f"{reason}：在途 {slot['在途占用']}/{slot['槽位上限']}，整笔拒收不占名额"}
            if idempotency_key:
                save_idempotent(conn, idempotency_key, endpoint, 409, payload)
            response.status_code = 409
            return payload

        audit(conn, "投递单", sid, "投递", slot["通道"])
        after = channel_slots(conn, ch_id)  # 与状态翻转同事务读到的投递后探针
        payload = {"结果": "投递中", "通道": slot["通道"], "槽位": after,
                   "口径": after["口径"]}
        if idempotency_key:
            save_idempotent(conn, idempotency_key, endpoint, 200, payload)
        return payload
