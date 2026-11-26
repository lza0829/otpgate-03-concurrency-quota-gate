"""复现矩阵：满槽再投 / 释放后再投 / 双投对撞 / 半截脏名额 / 重放 / 读写同源 / 切备释放。

全局口径（唯一）：硬闸拒收-零溢出。
收口不变量：提交后任一通道「投递中占用 <= 槽位上限」，overflow_tickets 恒空。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from conftest import dispatch, main_channel_id, make_send, slots_of


def test_full_slot_redispatch_is_rejected(client, clean):
    """满槽再投：整笔失败，不进投递中、不计路况在途、无假成功占用。"""
    cid = main_channel_id(client)
    sids = [make_send(client, f"139****{i:04d}") for i in range(2)]
    for sid in sids:
        assert dispatch(client, sid).status_code == 200

    extra = make_send(client, "139****9999")
    r = dispatch(client, extra)
    assert r.status_code == 409
    body = r.json()
    assert body["拒因"] == "槽位已满"
    assert body["槽位"]["满槽"] is True
    assert body["槽位"]["在途占用"] == 2

    # 单据留在投递前状态；槽位探针没有被这笔污染
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == extra)
    assert send["状态"] == "待投递"
    assert slots_of(client, cid)["在途占用"] == 2

    # 拒因挂接 + 事件痕过滤 + 溢出票位为空
    rej = client.get("/api/拒投记录", params={"拒因": "槽位已满"}).json()
    assert len(rej) == 1 and rej[0]["投递单"] == send["投递单号"]
    audit_rows = client.get("/api/审计", params={"动作": "拒投"}).json()
    assert len(audit_rows) == 1 and audit_rows[0]["关联"] == extra
    assert client.get("/api/溢出票").json()["溢出票"] == []


def test_redispatch_after_slot_released(client, clean):
    """释放名额后再投：同一笔此前被拒的单据在名额空出后可投，占用重新打满。"""
    cid = main_channel_id(client)
    sids = [make_send(client, f"139****{i:04d}") for i in range(2)]
    for sid in sids:
        dispatch(client, sid)
    extra = make_send(client, "139****9999")
    assert dispatch(client, extra).status_code == 409

    # 回执送达 → 名额与回执同事务释放
    r = client.post(f"/api/投递单/{sids[0]}/回执", json={"结果": "成功", "厂商码": "OK"})
    assert r.status_code == 200
    assert r.json()["槽位"]["剩余名额"] == 1

    r = dispatch(client, extra)
    assert r.status_code == 200
    assert r.json()["结果"] == "投递中"
    probe = slots_of(client, cid)
    assert probe["在途占用"] == 2 and probe["剩余名额"] == 0

    rec = client.get("/api/对账").json()
    assert rec["超槽通道数"] == 0
    assert rec["轧差溢出桶"] == 0
    assert rec["满槽拒投数"] == 1  # 此前那笔拒投仍可核


def test_concurrent_last_slot_single_outcome(client, clean):
    """双投对撞：两笔不同验码单抢最后一个名额，只许一种占用结局且可重放。"""
    cid = main_channel_id(client)
    first = make_send(client, "139****0001")
    dispatch(client, first)  # 预占 1/2

    a, b = make_send(client, "139****0002"), make_send(client, "139****0003")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda sid: dispatch(client, sid), (a, b)))

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409]
    winner = next(r for r in results if r.status_code == 200).json()
    loser = next(r for r in results if r.status_code == 409).json()
    assert winner["槽位"]["在途占用"] == 2
    assert loser["拒因"] == "槽位已满"

    # 唯一占用结局：占用恰好打满，无超槽、无溢出票、无脏名额
    probe = slots_of(client, cid)
    assert probe["在途占用"] == 2 and probe["满槽"] is True
    assert client.get("/api/溢出票").json()["溢出票"] == []
    loser_send = next(s for s in client.get("/api/投递单").json()
                      if s["编号"] in (a, b) and s["状态"] == "待投递")
    assert loser_send["状态"] == "待投递"


def test_concurrent_double_dispatch_same_send(client, clean):
    """同一验码单并发双投：原子条件 UPDATE 保证只投出一次，另一次整笔拦下。"""
    sid = make_send(client, "139****0007")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(client, sid), range(2)))

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409]
    cid = main_channel_id(client)
    assert slots_of(client, cid)["在途占用"] == 1  # 只占一个名额
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert send["状态"] == "投递中"


def test_rollback_leaves_no_dirty_slot(client, clean, monkeypatch):
    """半截脏名额：投递成功路径上的后置写入炸掉 → 整笔回滚，名额/状态回到投递前。"""
    import app.routers.sends as sends_mod

    cid = main_channel_id(client)
    sid = make_send(client, "139****0008")
    before = slots_of(client, cid)["在途占用"]

    def boom(*args, **kwargs):
        # HTTPException 同样穿透 db() 上下文触发整笔回滚，且服务端返回确定的 500
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail="模拟投递事务中途失败")

    monkeypatch.setattr(sends_mod, "audit", boom)
    r = dispatch(client, sid)
    assert r.status_code == 500  # 事务中途失败由服务端回滚并报错
    monkeypatch.undo()
    # 回滚后无脏名额：单据仍是待投递，占用探针不变，半截「投递」事件痕随回滚消失
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert send["状态"] == "待投递"
    assert slots_of(client, cid)["在途占用"] == before
    dispatch_audits = [a for a in client.get("/api/审计", params={"类别": "投递单"}).json()
                       if a["动作"] == "投递"]
    assert dispatch_audits == []

    # 回滚后该单据仍可正常投递（名额未被幽灵占用）
    assert dispatch(client, sid).status_code == 200
    assert slots_of(client, cid)["在途占用"] == before + 1


def test_idempotent_replay_single_outcome(client, clean):
    """同键重放：首次结局（成功或满槽 409）存档，重放不产生第二笔占用。"""
    cid = main_channel_id(client)
    sid = make_send(client, "139****0011")
    r1 = dispatch(client, sid, key="key-ok-1")
    r2 = dispatch(client, sid, key="key-ok-1")
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert slots_of(client, cid)["在途占用"] == 1  # 重放未再占名额

    # 打满后用新键投递新单 → 409 存档；即使之后释放名额，同键重放仍是原 409
    second = make_send(client, "139****0012")
    dispatch(client, second)
    extra = make_send(client, "139****0013")
    f1 = dispatch(client, extra, key="key-full-1")
    assert f1.status_code == 409 and f1.json()["拒因"] == "槽位已满"
    client.post(f"/api/投递单/{sid}/回执", json={"结果": "成功", "厂商码": "OK"})
    f2 = dispatch(client, extra, key="key-full-1")
    assert f2.status_code == 409
    assert f2.json() == f1.json()  # 重放首次存档，不翻案成新投递


def test_parallel_same_idempotency_key_single_outcome(client, clean):
    """同一幂等键并行双投：入口写锁串行，两请求拿到同一存档结局，只占一个名额。"""
    cid = main_channel_id(client)
    sid = make_send(client, "139****0014")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: dispatch(client, sid, key="parallel-key-1"), range(2)))
    assert {r.status_code for r in results} == {200}
    assert results[0].json() == results[1].json()
    assert slots_of(client, cid)["在途占用"] == 1
    # 存档只落一条
    from app.db import db as db_ctx
    with db_ctx() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM idempotency_records WHERE idempotency_key='parallel-key-1'"
        ).fetchone()["c"]
    assert n == 1


def test_read_and_write_share_one_judgment(client, clean):
    """满槽是否放行：通道册、槽位探针、路况、轧差读侧与投递写侧同一套数。"""
    cid = main_channel_id(client)
    for i in range(2):
        dispatch(client, make_send(client, f"139****{i:04d}"))
    extra = make_send(client, "139****9999")
    assert dispatch(client, extra).status_code == 409

    channel_row = next(c for c in client.get("/api/通道").json() if c["编号"] == cid)
    slot_row = slots_of(client, cid)
    overview = client.get("/api/总览").json()
    overview_ch = next(c for c in overview["通道"] if c["通道编号"] == cid)
    reconcile = client.get("/api/对账").json()
    bucket = next(b for b in reconcile["路况在途桶"] if b["通道编号"] == cid)

    for view in (channel_row["在途占用"], slot_row["在途占用"],
                 overview_ch["在途占用"], bucket["在途占用"],
                 reconcile["在途总数"], overview["分布"]["投递中"]):
        assert view == 2
    assert channel_row["满槽"] is slot_row["满槽"] is overview_ch["满槽"] is True
    assert channel_row["槽位口径"] == slot_row["口径"] == overview["口径"] == "硬闸拒收-零溢出"
    assert reconcile["轧差溢出桶"] == overview["溢出在途"] == 0
    assert reconcile["超槽通道数"] == 0
    # 事件痕过滤与拒投记录对读
    assert len(client.get("/api/审计", params={"动作": "拒投"}).json()) == 1
    assert len(client.get("/api/拒投记录").json()) == 1


def test_failover_and_failure_release_slots(client, clean):
    """中断回滚后无脏名额：失败/切备离开投递中即释放，切备单重投占备路名额。"""
    from app.db import db

    cid_main = main_channel_id(client)
    # 把主路熔断阈值调到 1，一次失败即熔断切备
    with db() as conn:
        conn.execute("UPDATE channels SET fail_threshold=1 WHERE id=?", (cid_main,))
    backup = next(c for c in client.get("/api/通道").json() if c["编号"] != cid_main)
    cid_backup = backup["编号"]

    sid = make_send(client, "139****0021")
    dispatch(client, sid)
    assert slots_of(client, cid_main)["在途占用"] == 1

    r = client.post(f"/api/投递单/{sid}/失败", json={"备注": "夹具失败"})
    assert r.status_code == 200 and r.json()["结果"] == "已切备"
    # 原通道名额释放，切备单重投前不占备路
    assert slots_of(client, cid_main)["在途占用"] == 0
    assert slots_of(client, cid_backup)["在途占用"] == 0
    send = next(s for s in client.get("/api/投递单").json() if s["编号"] == sid)
    assert send["状态"] == "已切备"

    r = dispatch(client, sid)
    assert r.status_code == 200
    assert slots_of(client, cid_backup)["在途占用"] == 1
    assert slots_of(client, cid_main)["在途占用"] == 0

    # 备路失败收尾：名额同样释放，无悬挂占用
    client.post(f"/api/投递单/{sid}/回执", json={"结果": "失败", "厂商码": "X"})
    assert slots_of(client, cid_backup)["在途占用"] == 0
    reconcile = client.get("/api/对账").json()
    assert reconcile["超槽通道数"] == 0 and reconcile["轧差溢出桶"] == 0
