"""并发槽位闸 —— 全系统唯一口径（口径A：槽位耗尽整笔拒收，零溢出）。

读写两侧必须共用本模块的 slot 判定，禁止各写各的 COUNT。
不变量：任意已提交事务之后，每个通道
    在途占用(投递中, 计入 failover_channel_id) <= concurrency_limit
且 overflow_tickets 恒为空。
"""
from __future__ import annotations

import json

from app.services.common import now_str

# 拒因码（挂接 dispatch_rejections.reason 与事件痕备注，全局只能这一套）
REASON_SLOT_FULL = "槽位已满"          # 名额耗尽，整笔拒收
REASON_NOT_DISPATCHABLE = "状态不可投递"  # 验码单态机不允许投递
REASON_CHANNEL_UNAVAILABLE = "通道不可用"  # 通道不存在/非启用
REASON_NOT_FOUND = "投递单不存在"

# 口径标识：轧差/路况/夹具都引用这一个常量，杜绝出现第二种口径
SLOT_POLICY = "硬闸拒收-零溢出"


def channel_slots(conn, channel_id: int) -> dict:
    """单个通道的槽位快照。读侧（通道册/路况/轧差）与写侧（投递闸）同源。

    在途占用只数 status='投递中' 的验码单；已切备单在重新投递成功前不占名额，
    其名额计入 failover_channel_id（投递 UPDATE 会把 channel_id 切到备路）。
    """
    row = conn.execute(
        """
        SELECT c.id, c.name, c.status, c.concurrency_limit,
               (SELECT COUNT(*) FROM sends s
                 WHERE s.status='投递中'
                   AND COALESCE(s.failover_channel_id, s.channel_id) = c.id) AS inflight
        FROM channels c WHERE c.id=?
        """,
        (channel_id,),
    ).fetchone()
    if row is None:
        return None
    limit = row["concurrency_limit"]
    inflight = row["inflight"]
    return {
        "通道编号": row["id"],
        "通道": row["name"],
        "通道状态": row["status"],
        "槽位上限": limit,
        "在途占用": inflight,
        "剩余名额": limit - inflight,
        "满槽": inflight >= limit,
        "口径": SLOT_POLICY,
    }


def all_channel_slots(conn) -> list[dict]:
    """通道册槽位列：每个通道一行，占用探针与槽位上限同刷。"""
    rows = conn.execute("SELECT id FROM channels ORDER BY role, id").fetchall()
    return [channel_slots(conn, r["id"]) for r in rows]


def record_rejection(conn, send_id: int, channel_id: int, reason: str,
                     inflight: int, limit: int) -> dict:
    """满槽/不可投递留痕：拒因挂接表 + 事件痕，同一事务内写入。"""
    created = now_str()
    cur = conn.execute(
        """
        INSERT INTO dispatch_rejections
          (send_id, channel_id, reason, inflight, concurrency_limit, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (send_id, channel_id, reason, inflight, limit, created),
    )
    conn.execute(
        "INSERT INTO audit_events(kind, ref_id, action, created_at, note) VALUES (?, ?, ?, ?, ?)",
        ("槽位闸", send_id, "拒投", created,
         f"{reason}｜通道#{channel_id} 在途{inflight}/上限{limit}｜拒单#{cur.lastrowid}"),
    )
    return {
        "拒单编号": cur.lastrowid,
        "拒因": reason,
        "通道编号": channel_id,
        "在途占用": inflight,
        "槽位上限": limit,
        "时间": created,
    }


def list_rejections(conn, limit: int = 100, action: str | None = None) -> list[dict]:
    sql = """
        SELECT r.*, s.serial, c.name AS channel_name
        FROM dispatch_rejections r
        JOIN sends s ON s.id = r.send_id
        JOIN channels c ON c.id = r.channel_id
    """
    params: list = []
    if action:
        sql += " WHERE r.reason=?"
        params.append(action)
    sql += " ORDER BY r.id DESC LIMIT ?"
    params.append(limit)
    return [
        {
            "编号": r["id"],
            "投递单": r["serial"],
            "通道": r["channel_name"],
            "拒因": r["reason"],
            "在途占用": r["inflight"],
            "槽位上限": r["concurrency_limit"],
            "时间": r["created_at"],
        }
        for r in conn.execute(sql, params).fetchall()
    ]


def list_overflow_tickets(conn, limit: int = 100) -> list[dict]:
    """溢出票位查询。口径A 下恒返回空列表（空表即可核的不变量夹具）。"""
    rows = conn.execute(
        """
        SELECT t.*, s.serial, c.name AS channel_name
        FROM overflow_tickets t
        JOIN sends s ON s.id = t.send_id
        JOIN channels c ON c.id = t.channel_id
        ORDER BY t.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "编号": r["id"],
            "投递单": r["serial"],
            "通道": r["channel_name"],
            "出票时在途": r["inflight_at_issue"],
            "槽位上限": r["concurrency_limit"],
            "时间": r["created_at"],
            "备注": r["note"],
        }
        for r in rows
    ]


def save_idempotent(conn, key: str, endpoint: str, status_code: int, payload: dict) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO idempotency_records
          (idempotency_key, endpoint, status_code, response_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, endpoint, status_code, json.dumps(payload, ensure_ascii=False), now_str()),
    )


def load_idempotent(conn, key: str) -> dict | None:
    row = conn.execute(
        "SELECT status_code, response_json FROM idempotency_records WHERE idempotency_key=?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    return {"status_code": row["status_code"], "payload": json.loads(row["response_json"])}
