from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('主路','备路')),
  weight INTEGER NOT NULL DEFAULT 100,
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','熔断中','停用')),
  concurrency_limit INTEGER NOT NULL DEFAULT 20,
  fail_threshold INTEGER NOT NULL DEFAULT 3,
  consecutive_fails INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS route_policy (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  mode TEXT NOT NULL DEFAULT '权重' CHECK(mode IN ('权重','强制主路','强制备路')),
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  phone_mask TEXT NOT NULL,
  scene TEXT NOT NULL DEFAULT '登录',
  status TEXT NOT NULL DEFAULT '待投递'
    CHECK(status IN ('待投递','投递中','已送达','已失败','已切备')),
  channel_id INTEGER,
  failover_channel_id INTEGER,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  sent_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(failover_channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  send_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  result TEXT NOT NULL CHECK(result IN ('成功','失败')),
  vendor_code TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(send_id) REFERENCES sends(id),
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
CREATE TABLE IF NOT EXISTS break_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('熔断','恢复','切备')),
  send_id INTEGER,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(channel_id) REFERENCES channels(id),
  FOREIGN KEY(send_id) REFERENCES sends(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
-- 满槽拒因挂接：口径A（硬闸拒收）下每一笔被槽位闸拦下的投递都在此留痕
CREATE TABLE IF NOT EXISTS dispatch_rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  send_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  inflight INTEGER NOT NULL,
  concurrency_limit INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(send_id) REFERENCES sends(id),
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
-- 溢出票位：口径A 下永不写入，保持为空本身即是「无超槽、无假成功占用」的可核夹具
CREATE TABLE IF NOT EXISTS overflow_tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  send_id INTEGER NOT NULL,
  channel_id INTEGER NOT NULL,
  inflight_at_issue INTEGER NOT NULL,
  concurrency_limit INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(send_id) REFERENCES sends(id),
  FOREIGN KEY(channel_id) REFERENCES channels(id)
);
-- 投递幂等档：同键重放首次结局（含状态码与响应体），双投/重试只许一种结局
CREATE TABLE IF NOT EXISTS idempotency_records (
  idempotency_key TEXT PRIMARY KEY,
  endpoint TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL 允许旁读探针在写事务进行时同刷；busy_timeout 让并发写者排队而非直接报错
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
