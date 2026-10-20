"""SQLite 持久化：用户、方案的增删改查。"""
import json
import os
import sqlite3
import string
import random

from . import config

DB_PATH = os.path.join(config.BASE_DIR, "data", "plans.db")


def _ensure_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'legacy',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            phase TEXT DEFAULT 'planning',
            original_input TEXT,
            conversation TEXT,
            params TEXT,
            summary TEXT,
            action_items TEXT,
            timeline TEXT,
            budget TEXT,
            room_allocation TEXT,
            budget_per_person TEXT,
            share_code TEXT UNIQUE,
            members TEXT,
            markdown TEXT,
            warnings TEXT,
            used_llm INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    # migration: add user_id column if missing (for existing DBs)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(plans)").fetchall()]
    if "user_id" not in cols:
        conn.execute("ALTER TABLE plans ADD COLUMN user_id TEXT DEFAULT 'legacy'")
        conn.execute("""
            INSERT OR IGNORE INTO users (id, username, password_hash, created_at)
            VALUES ('legacy', 'legacy', '', '')
        """)

    # migration: add github_id and avatar_url columns for OAuth
    user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "github_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN github_id TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github ON users(github_id)")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_user ON plans(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_share ON plans(share_code)")

    conn.commit()
    conn.close()


# ── Users ─────────────────────────────────────────────────

def create_user(user_id: str, username: str, password_hash: str) -> dict | None:
    from datetime import datetime
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, username, password_hash, datetime.now().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return {"id": user_id, "username": username}


def get_user_by_username(username: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def get_user_by_github_id(github_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE github_id = ?", (str(github_id),)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def link_github_account(user_id: str, github_id: int, avatar_url: str) -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET github_id = ?, avatar_url = ? WHERE id = ?",
            (str(github_id), avatar_url, user_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def create_github_user(user_id: str, username: str, github_id: int, avatar_url: str) -> dict | None:
    from datetime import datetime
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at, github_id, avatar_url) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (user_id, username, datetime.now().isoformat(), str(github_id), avatar_url),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return {"id": user_id, "username": username, "avatar_url": avatar_url}


# ── Plans ─────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("conversation", "params", "action_items", "timeline",
                "budget", "room_allocation", "members", "warnings"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = [] if key not in ("params",) else {}
    d["used_llm"] = bool(d.get("used_llm"))
    return d


def save_plan(plan_dict: dict) -> dict:
    conn = _get_conn()
    data = {
        "id": plan_dict["id"],
        "user_id": plan_dict.get("user_id", "legacy"),
        "created_at": plan_dict.get("created_at", ""),
        "updated_at": plan_dict.get("updated_at", ""),
        "phase": plan_dict.get("phase", "planning"),
        "original_input": plan_dict.get("original_input", ""),
        "conversation": json.dumps(plan_dict.get("conversation", []), ensure_ascii=False),
        "params": json.dumps(plan_dict.get("params", {}), ensure_ascii=False),
        "summary": plan_dict.get("summary", ""),
        "action_items": json.dumps(plan_dict.get("action_items", []), ensure_ascii=False),
        "timeline": json.dumps(plan_dict.get("timeline", []), ensure_ascii=False),
        "budget": json.dumps(plan_dict.get("budget", []), ensure_ascii=False),
        "room_allocation": json.dumps(plan_dict.get("room_allocation", []), ensure_ascii=False),
        "budget_per_person": plan_dict.get("budget_per_person", ""),
        "share_code": plan_dict.get("share_code"),
        "members": json.dumps(plan_dict.get("members", []), ensure_ascii=False),
        "markdown": plan_dict.get("markdown", ""),
        "warnings": json.dumps(plan_dict.get("warnings", []), ensure_ascii=False),
        "used_llm": int(plan_dict.get("used_llm", False)),
    }
    conn.execute("""
        INSERT OR REPLACE INTO plans (
            id, user_id, created_at, updated_at, phase, original_input, conversation,
            params, summary, action_items, timeline, budget, room_allocation,
            budget_per_person, share_code, members, markdown, warnings, used_llm
        ) VALUES (
            :id, :user_id, :created_at, :updated_at, :phase, :original_input, :conversation,
            :params, :summary, :action_items, :timeline, :budget, :room_allocation,
            :budget_per_person, :share_code, :members, :markdown, :warnings, :used_llm
        )
    """, data)
    conn.commit()
    conn.close()
    return plan_dict


def get_plan(plan_id: str, user_id: str | None = None) -> dict | None:
    conn = _get_conn()
    if user_id:
        row = conn.execute(
            "SELECT * FROM plans WHERE id = ? AND user_id = ?", (plan_id, user_id)
        ).fetchone()
    else:
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def get_plan_by_share(share_code: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM plans WHERE share_code = ?", (share_code,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_dict(row)


def update_task_status(plan_id: str, task_id: str, status: str, user_id: str | None = None) -> dict | None:
    plan = get_plan(plan_id, user_id=user_id)
    if not plan:
        return None
    for item in plan.get("action_items", []):
        if item.get("id") == task_id:
            item["status"] = status
            break
    from datetime import datetime
    plan["updated_at"] = datetime.now().isoformat()
    save_plan(plan)
    return plan


def generate_share_code(plan_id: str, user_id: str | None = None) -> str | None:
    plan = get_plan(plan_id, user_id=user_id)
    if not plan:
        return None
    if plan.get("share_code"):
        return plan["share_code"]
    code = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    plan["share_code"] = code
    save_plan(plan)
    return code


def list_plans(user_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, user_id, created_at, updated_at, phase, original_input, summary, share_code "
        "FROM plans WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
