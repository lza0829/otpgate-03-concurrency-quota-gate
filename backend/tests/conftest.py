"""并发槽位闸夹具：独立临时库 + 真实 uvicorn 线程（多线程请求才能压到 SQLite 写锁）。"""
from __future__ import annotations

import os
import threading
import time

import httpx
import pytest

# 必须在导入 app 之前指定隔离库
_TMP_DB = "/tmp/otpgate_slot_tests/otpgate.db"
os.environ["OTPGATE_DB"] = _TMP_DB
os.makedirs(os.path.dirname(_TMP_DB), exist_ok=True)
if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def base_url():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    yield url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=30) as c:
        yield c


@pytest.fixture()
def clean(client):
    """每个用例清场：只留种子通道，单据/痕/票/幂等档全部清空，通道复位。"""
    from app.db import db

    with db() as conn:
        for table in (
            "receipts", "dispatch_rejections", "overflow_tickets",
            "break_events", "audit_events", "idempotency_records", "sends",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "UPDATE channels SET status='启用', consecutive_fails=0, "
            "concurrency_limit=2, fail_threshold=3"
        )
    yield


def make_send(client, phone: str = "139****0000") -> int:
    return client.post("/api/投递单", json={"手机掩码": phone}).json()["编号"]


def dispatch(client, sid: int, key: str | None = None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(f"/api/投递单/{sid}/投递", headers=headers)


def slots_of(client, cid: int) -> dict:
    rows = client.get("/api/槽位").json()["槽位"]
    return next(r for r in rows if r["通道编号"] == cid)


def main_channel_id(client) -> int:
    return client.get("/api/通道").json()[0]["编号"]
