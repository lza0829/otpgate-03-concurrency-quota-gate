from __future__ import annotations
from fastapi import APIRouter

from app.db import db
from app.services.slots import (
    SLOT_POLICY,
    all_channel_slots,
    list_overflow_tickets,
    list_rejections,
)

router = APIRouter(tags=["槽位闸"])


@router.get("/槽位")
def slot_snapshot():
    """通道册槽位列旁读探针：上限/在途占用/剩余/满槽，与投递写侧同一判定。"""
    with db() as conn:
        rows = all_channel_slots(conn)
        tickets = conn.execute("SELECT COUNT(*) c FROM overflow_tickets").fetchone()["c"]
    return {"口径": SLOT_POLICY, "槽位": rows, "溢出票数": tickets}


@router.get("/拒投记录")
def rejections(拒因: str | None = None):
    """满槽/不可投递拒因挂接查询，支持按拒因过滤。"""
    with db() as conn:
        return list_rejections(conn, action=拒因)


@router.get("/溢出票")
def overflow_tickets():
    """溢出票位。口径A 下恒为空表 —— 空列表即「无超槽投递」可核证据。"""
    with db() as conn:
        return {"口径": SLOT_POLICY, "溢出票": list_overflow_tickets(conn)}
