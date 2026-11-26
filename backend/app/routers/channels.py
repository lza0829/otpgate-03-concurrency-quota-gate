from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services.slots import all_channel_slots, channel_slots

router = APIRouter(tags=["通道"])


class ChannelPatch(BaseModel):
    状态: str | None = None
    权重: int | None = Field(default=None, ge=0, le=100)
    并发上限: int | None = Field(default=None, ge=1)


@router.get("/通道")
def list_channels():
    with db() as conn:
        rows = rows_to_list(conn.execute("SELECT * FROM channels ORDER BY role, id").fetchall())
        # 槽位列与投递写侧同源：占用/剩余/满槽均来自 channel_slots 同一判定
        slots = {s["通道编号"]: s for s in all_channel_slots(conn)}
    return [
        {
            "编号": r["id"],
            "代号": r["code"],
            "名称": r["name"],
            "角色": r["role"],
            "权重": r["weight"],
            "状态": r["status"],
            "并发上限": r["concurrency_limit"],
            "失败阈值": r["fail_threshold"],
            "连续失败": r["consecutive_fails"],
            "在途占用": slots[r["id"]]["在途占用"],
            "剩余名额": slots[r["id"]]["剩余名额"],
            "满槽": slots[r["id"]]["满槽"],
            "槽位口径": slots[r["id"]]["口径"],
        }
        for r in rows
    ]


@router.patch("/通道/{cid}")
def patch_channel(cid: int, body: ChannelPatch):
    with db() as conn:
        row = conn.execute("SELECT * FROM channels WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "通道不存在")
        status = body.状态 if body.状态 is not None else row["status"]
        if status not in ("启用", "熔断中", "停用"):
            raise HTTPException(400, "状态非法")
        weight = body.权重 if body.权重 is not None else row["weight"]
        lim = body.并发上限 if body.并发上限 is not None else row["concurrency_limit"]
        conn.execute(
            "UPDATE channels SET status=?, weight=?, concurrency_limit=? WHERE id=?",
            (status, weight, lim, cid),
        )
        audit(conn, "通道", cid, "变更", f"{status}/{weight}/上限{lim}")
        # 启停与槽位上限调整后，立即回带同事务新探针，通道册与投递入口同刷
        probe = channel_slots(conn, cid)
    return {"结果": "已更新", "槽位": probe}
