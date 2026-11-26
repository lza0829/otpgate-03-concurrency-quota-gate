from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(类别: str | None = None, 动作: str | None = None):
    """事件痕查询，支持按类别/动作过滤（如 动作=拒投 只看槽位闸拦下的投递）。"""
    sql = "SELECT * FROM audit_events WHERE 1=1"
    params: list = []
    if 类别:
        sql += " AND kind=?"
        params.append(类别)
    if 动作:
        sql += " AND action=?"
        params.append(动作)
    sql += " ORDER BY id DESC LIMIT 200"
    with db() as conn:
        rows = rows_to_list(conn.execute(sql, params).fetchall())
    return [
        {
            "编号": r["id"],
            "类别": r["kind"],
            "关联": r["ref_id"],
            "动作": r["action"],
            "时间": r["created_at"],
            "备注": r["note"],
        }
        for r in rows
    ]
