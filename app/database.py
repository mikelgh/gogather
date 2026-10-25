"""SQLite 持久化：用户、方案的增删改查。"""
import json
import os
import sqlite3
import string
import secrets
from contextlib import closing

from . import backup, config

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
            data_captured_at TEXT,
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

    # migration: 价格时效基线（飞猪查询批次完成时间）
    if "data_captured_at" not in cols:
        conn.execute("ALTER TABLE plans ADD COLUMN data_captured_at TEXT")

    # migration: 重新验价状态
    if "reverify_status" not in cols:
        conn.execute("ALTER TABLE plans ADD COLUMN reverify_status TEXT DEFAULT 'idle'")
        conn.execute("ALTER TABLE plans ADD COLUMN reverify_error TEXT")

    # migration: add github_id and avatar_url columns for OAuth
    user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    if "github_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN github_id TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN avatar_url TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_github ON users(github_id)")

    # migration: add alipay_id and taobao_id columns for OAuth
    if "alipay_id" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN alipay_id TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN taobao_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_alipay ON users(alipay_id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_taobao ON users(taobao_id)")


    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_user ON plans(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_share ON plans(share_code)")

    # migration: add conversations table for chat history persistence
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id)")

    # migration: 结构化消息体（如大纲卡片），让历史记录能还原成卡片而不是纯文本
    conv_cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "meta" not in conv_cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN meta TEXT")

    # migration: add plan_revisions table for version history
    conn.execute('''
        CREATE TABLE IF NOT EXISTS plan_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id TEXT NOT NULL,
            revision_number INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            message TEXT NOT NULL,
            summary TEXT,
            action_items TEXT,
            timeline TEXT,
            budget TEXT,
            room_allocation TEXT,
            budget_per_person TEXT,
            params TEXT,
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    ''')
    conn.execute("CREATE INDEX IF NOT EXISTS idx_revisions_plan ON plan_revisions(plan_id)")

    # migration: add ai_usage table for tracking AI API calls
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            plan_id TEXT,
            action TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost REAL DEFAULT 0.0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (plan_id) REFERENCES plans(id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_user ON ai_usage(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_plan ON ai_usage(plan_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_usage_created ON ai_usage(created_at)")

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


def get_user_by_id(user_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


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
        "data_captured_at": plan_dict.get("data_captured_at"),
        "reverify_status": plan_dict.get("reverify_status", "idle"),
        "reverify_error": plan_dict.get("reverify_error"),
    }
    # 只以主键判定更新；分享码冲突必须报错，不能删除另一个方案。
    columns = ", ".join(data)
    values = ", ".join(f":{key}" for key in data)
    updates = ", ".join(f"{key} = excluded.{key}" for key in data if key != "id")
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM plans WHERE id = ?", (data["id"],)).fetchone():
            backup.create_backup(DB_PATH, reason="pre-update")
        conn.execute(
            f"INSERT INTO plans ({columns}) VALUES ({values}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            data,
        )
    return plan_dict


def update_plan(plan_id: str, user_id: str, **fields) -> dict | None:
    """更新方案的指定字段。"""
    allowed_fields = {
        "structured", "warnings", "markdown", "reverify_status", "reverify_error",
        "summary", "action_items", "timeline", "budget", "room_allocation",
        "budget_per_person", "members", "phase", "params",
    }
    updates = {k: v for k, v in fields.items() if k in allowed_fields}
    if not updates:
        return get_plan(plan_id, user_id)

    # 处理 JSON 字段
    json_fields = {"structured", "warnings", "action_items", "timeline", "budget", "room_allocation", "members", "params"}
    for k in json_fields:
        if k in updates and isinstance(updates[k], (dict, list)):
            updates[k] = json.dumps(updates[k], ensure_ascii=False)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [plan_id]
    if user_id:
        values.append(user_id)
        user_clause = " AND user_id = ?"
    else:
        user_clause = ""

    with closing(_get_conn()) as conn, conn:
        conn.execute(
            f"UPDATE plans SET {set_clause}, updated_at = datetime('now') WHERE id = ?{user_clause}",
            values,
        )
    return get_plan(plan_id, user_id)


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


def delete_plan(plan_id: str, user_id: str | None = None) -> bool:
    """删除方案。返回是否成功删除。"""
    where = "id = ? AND user_id = ?" if user_id else "id = ?"
    args = (plan_id, user_id) if user_id else (plan_id,)
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute(f"SELECT 1 FROM plans WHERE {where}", args).fetchone():
            return False
        backup.create_backup(DB_PATH, reason="pre-delete")
        cursor = conn.execute(f"DELETE FROM plans WHERE {where}", args)
        return cursor.rowcount > 0


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
    where = "id = ?"
    args = (plan_id,)
    if user_id is not None:
        where += " AND user_id = ?"
        args += (user_id,)
    alphabet = string.ascii_lowercase + string.digits
    with closing(_get_conn()) as conn:
        for _ in range(20):
            try:
                with conn:
                    row = conn.execute(
                        f"SELECT share_code FROM plans WHERE {where}", args
                    ).fetchone()
                    if not row:
                        return None
                    if row["share_code"]:
                        return row["share_code"]
                    code = ''.join(secrets.choice(alphabet) for _ in range(6))
                    # 条件更新保留同一方案并发生成的码，不回写旧方案快照。
                    conn.execute(
                        f"UPDATE plans SET share_code = ? WHERE {where} "
                        "AND (share_code IS NULL OR share_code = '')",
                        (code,) + args,
                    )
                    row = conn.execute(
                        f"SELECT share_code FROM plans WHERE {where}", args
                    ).fetchone()
                    return row["share_code"] if row else None
            except sqlite3.IntegrityError:
                # 唯一索引碰撞：事务已回滚，重新生成。
                continue
    raise RuntimeError("无法生成唯一分享码，请重试")


def list_plans(user_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, user_id, created_at, updated_at, phase, original_input, params, summary, share_code "
        "FROM plans WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    ).fetchall()
    conn.close()
    plans = [_row_to_dict(r) for r in rows]
    for p in plans:
        # 方案名按参数推导：多轮调整后 original_input 可能变成"第三天换个景点"
        p["name"] = build_plan_name(p.get("params") or {}, p.get("original_input", ""))
    return plans


def build_plan_name(params: dict, fallback: str = "") -> str:
    """按方案参数生成简短名称，如「30人 广州→上海及周边城市 同学聚会」。"""
    import re

    city = (params.get("city") or "").strip()
    origin = (params.get("origin") or "").strip()
    size = re.sub(r"\D", "", str(params.get("group_size") or ""))
    activity = (params.get("activity_type") or "").strip()
    route = f"{origin}→{city}" if origin and origin != city else city
    name = " ".join(p for p in [f"{size}人" if size else "", route, activity] if p)
    return name or (fallback[:20] if fallback else "未命名方案")


# ── Conversations ────────────────────────────────────────

def save_conversation(user_id: str, role: str, content: str, meta: dict | None = None) -> None:
    from datetime import datetime
    import json
    conn = _get_conn()
    conn.execute(
        "INSERT INTO conversations (user_id, role, content, meta, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, role, content, json.dumps(meta, ensure_ascii=False) if meta else None,
         datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_conversations(user_id: str, limit: int = 50) -> list[dict]:
    import json
    conn = _get_conn()
    rows = conn.execute(
        "SELECT role, content, meta, created_at FROM conversations "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    # Reverse to get chronological order
    out = []
    for r in reversed(rows):
        try:
            meta = json.loads(r["meta"]) if r["meta"] else None
        except (ValueError, TypeError):
            meta = None
        out.append({"role": r["role"], "content": r["content"], "meta": meta, "created_at": r["created_at"]})
    return out


def get_latest_extracted_params(user_id: str, limit: int = 50) -> dict:
    """多轮对话中已确认的规划参数会随对话记录累积保存；返回最近一次累积的参数，无则空 dict。"""
    import json
    conn = _get_conn()
    rows = conn.execute(
        "SELECT meta FROM conversations "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    for r in rows:
        try:
            meta = json.loads(r["meta"]) if r["meta"] else None
        except (ValueError, TypeError):
            continue
        if isinstance(meta, dict) and isinstance(meta.get("extracted_params"), dict):
            return meta["extracted_params"]
    return {}


def clear_conversations(user_id: str) -> None:
    with closing(_get_conn()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM conversations WHERE user_id = ? LIMIT 1", (user_id,)).fetchone():
            return
        backup.create_backup(DB_PATH, reason="pre-delete")
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))


def get_user_by_alipay_id(alipay_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE alipay_id = ?", (alipay_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def get_user_by_taobao_id(taobao_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE taobao_id = ?", (taobao_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def update_username(user_id: str, username: str) -> dict | None:
    """改昵称；昵称被占用（UNIQUE 冲突）或用户不存在时返回 None。"""
    conn = _get_conn()
    try:
        cur = conn.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
        if cur.rowcount == 0:
            return None
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def create_alipay_user(user_id: str, username: str, alipay_id: str, avatar_url: str = "") -> dict | None:
    from datetime import datetime
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at, alipay_id, avatar_url) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (user_id, username, datetime.now().isoformat(), alipay_id, avatar_url),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return {"id": user_id, "username": username, "avatar_url": avatar_url}


def create_taobao_user(user_id: str, username: str, taobao_id: str, avatar_url: str = "") -> dict | None:
    from datetime import datetime
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at, taobao_id, avatar_url) "
            "VALUES (?, ?, '', ?, ?, ?)",
            (user_id, username, datetime.now().isoformat(), taobao_id, avatar_url),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return None
    conn.close()
    return {"id": user_id, "username": username, "avatar_url": avatar_url}


def link_alipay_account(user_id: str, alipay_id: str, avatar_url: str = "") -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET alipay_id = ?, avatar_url = ? WHERE id = ?",
            (alipay_id, avatar_url, user_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def link_taobao_account(user_id: str, taobao_id: str, avatar_url: str = "") -> bool:
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE users SET taobao_id = ?, avatar_url = ? WHERE id = ?",
            (taobao_id, avatar_url, user_id),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def save_plan_revision(plan_id: str, revision_number: int, message: str, plan_data: dict) -> int:
    """保存方案修订快照。返回 revision id。"""
    from datetime import datetime
    conn = _get_conn()
    cursor = conn.execute(
        """INSERT INTO plan_revisions 
           (plan_id, revision_number, created_at, message, summary, action_items, 
            timeline, budget, room_allocation, budget_per_person, params)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan_id,
            revision_number,
            datetime.now().isoformat(),
            message,
            plan_data.get("summary", ""),
            json.dumps(plan_data.get("action_items", []), ensure_ascii=False),
            json.dumps(plan_data.get("timeline", []), ensure_ascii=False),
            json.dumps(plan_data.get("budget", []), ensure_ascii=False),
            json.dumps(plan_data.get("room_allocation", []), ensure_ascii=False),
            plan_data.get("budget_per_person", ""),
            json.dumps(plan_data.get("params", {}), ensure_ascii=False),
        ),
    )
    conn.commit()
    revision_id = cursor.lastrowid
    conn.close()
    return revision_id


def get_plan_revisions(plan_id: str, limit: int = 20) -> list[dict]:
    """获取方案的修订历史。"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT id, plan_id, revision_number, created_at, message, 
                  summary, action_items, timeline, budget, room_allocation, 
                  budget_per_person, params
           FROM plan_revisions 
           WHERE plan_id = ? 
           ORDER BY revision_number DESC 
           LIMIT ?""",
        (plan_id, limit),
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        for key in ("action_items", "timeline", "budget", "room_allocation", "params"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = [] if key != "params" else {}
        result.append(d)
    return result


def get_plan_revision(revision_id: int) -> dict | None:
    """获取单个修订版本。"""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM plan_revisions WHERE id = ?", (revision_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for key in ("action_items", "timeline", "budget", "room_allocation", "params"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key] = [] if key != "params" else {}
    return d


def rollback_plan(plan_id: str, revision_id: int, user_id: str | None = None) -> dict | None:
    """回滚到指定修订版本。返回更新后的 plan。"""
    revision = get_plan_revision(revision_id)
    if not revision or revision["plan_id"] != plan_id:
        return None
    
    plan = get_plan(plan_id, user_id=user_id)
    if not plan:
        return None
    
    from datetime import datetime
    plan["summary"] = revision.get("summary", plan.get("summary", ""))
    plan["action_items"] = revision.get("action_items", plan.get("action_items", []))
    plan["timeline"] = revision.get("timeline", plan.get("timeline", []))
    plan["budget"] = revision.get("budget", plan.get("budget", []))
    plan["room_allocation"] = revision.get("room_allocation", plan.get("room_allocation", []))
    plan["budget_per_person"] = revision.get("budget_per_person", plan.get("budget_per_person", ""))
    plan["params"] = revision.get("params", plan.get("params", {}))
    plan["updated_at"] = datetime.now().isoformat()
    
    save_plan(plan)
    return plan


# ── AI Usage Tracking ─────────────────────────────────────

def record_ai_usage(user_id: str, action: str, model: str, prompt_tokens: int,
                    completion_tokens: int, plan_id: str | None = None, cost: float = 0.0) -> int:
    """记录 AI API 调用。返回 usage id。"""
    from datetime import datetime
    conn = _get_conn()
    total = prompt_tokens + completion_tokens
    cursor = conn.execute(
        """INSERT INTO ai_usage (user_id, plan_id, action, model, prompt_tokens,
           completion_tokens, total_tokens, cost, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, plan_id, action, model, prompt_tokens, completion_tokens, total, cost,
         datetime.now().isoformat()),
    )
    conn.commit()
    usage_id = cursor.lastrowid
    conn.close()
    return usage_id


def get_user_ai_usage(user_id: str, days: int = 30) -> list[dict]:
    """获取用户最近 N 天的 AI 使用记录。"""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT * FROM ai_usage WHERE user_id = ?
           AND created_at >= datetime('now', ? || ' days')
           ORDER BY created_at DESC""",
        (user_id, f"-{days}"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Admin Queries ─────────────────────────────────────────

def admin_get_all_users() -> list[dict]:
    """获取所有用户及其方案统计。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.github_id, u.alipay_id, u.avatar_url, u.created_at,
               COUNT(p.id) as plan_count,
               COALESCE(SUM(p.used_llm), 0) as ai_plan_count
        FROM users u
        LEFT JOIN plans p ON u.id = p.user_id
        WHERE u.id != 'legacy'
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_get_all_plans(limit: int = 100) -> list[dict]:
    """获取所有方案（管理员视图）。"""
    conn = _get_conn()
    rows = conn.execute("""
        SELECT p.id, p.user_id, u.username, p.created_at, p.updated_at,
               p.summary, p.used_llm, p.phase,
               LENGTH(p.conversation) as conv_size
        FROM plans p
        LEFT JOIN users u ON p.user_id = u.id
        WHERE p.user_id != 'legacy'
        ORDER BY p.created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def admin_get_ai_usage_summary(days: int = 30) -> dict:
    """获取 AI 使用汇总统计。"""
    conn = _get_conn()

    total_calls = conn.execute(
        "SELECT COUNT(*) FROM ai_usage WHERE created_at >= datetime('now', ? || ' days')",
        (f"-{days}",)
    ).fetchone()[0]

    total_tokens = conn.execute(
        "SELECT COALESCE(SUM(total_tokens), 0) FROM ai_usage WHERE created_at >= datetime('now', ? || ' days')",
        (f"-{days}",)
    ).fetchone()[0]

    total_cost = conn.execute(
        "SELECT COALESCE(SUM(cost), 0.0) FROM ai_usage WHERE created_at >= datetime('now', ? || ' days')",
        (f"-{days}",)
    ).fetchone()[0]

    by_model = conn.execute(
        """SELECT model, COUNT(*) as calls, SUM(total_tokens) as tokens
           FROM ai_usage WHERE created_at >= datetime('now', ? || ' days')
           GROUP BY model""",
        (f"-{days}",)
    ).fetchall()

    by_user = conn.execute(
        """SELECT u.username, COUNT(a.id) as calls, SUM(a.total_tokens) as tokens
           FROM ai_usage a
           LEFT JOIN users u ON a.user_id = u.id
           WHERE a.created_at >= datetime('now', ? || ' days')
           GROUP BY a.user_id
           ORDER BY calls DESC""",
        (f"-{days}",)
    ).fetchall()

    conn.close()
    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "by_model": [dict(r) for r in by_model],
        "by_user": [dict(r) for r in by_user],
    }


def admin_get_plan_ai_usage(plan_id: str) -> list[dict]:
    """获取指定方案的所有 AI 调用记录。"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM ai_usage WHERE plan_id = ? ORDER BY created_at DESC",
        (plan_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
