import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ------------------ 读取 .env ------------------
def load_env(path: str = ".env"):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


load_env()

# ------------------ 全局配置 ------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
ASSETS_DIR = os.path.join(PUBLIC_DIR, "assets")
DB_PATH = os.environ.get("MASTER_DB_PATH", os.path.join(BASE_DIR, "master.db"))
LOG_FILE = os.environ.get("MASTER_LOG_FILE", os.path.join(BASE_DIR, "master.log"))

MASTER_SECRET = os.environ.get("MASTER_SECRET", "change-me-secret")
MASTER_ADMIN_USER = os.environ.get("MASTER_ADMIN_USER", "admin")
MASTER_ADMIN_PASSWORD = os.environ.get("MASTER_ADMIN_PASSWORD", "admin123")
MASTER_TOKEN_EXPIRE_HOURS = int(os.environ.get("MASTER_TOKEN_EXPIRE_HOURS", "48"))
MAX_LOG_LINES = int(os.environ.get("MASTER_MAX_LOG_LINES", "500"))
MASTER_AGENT_OFFLINE_SECONDS = int(os.environ.get("MASTER_AGENT_OFFLINE_SECONDS", "60"))

log_level_name = os.environ.get("MASTER_LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_name, logging.INFO)

os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)

logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("sms-master")

# ------------------ Token & 密码 ------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = os.environ.get("MASTER_PW_SALT", "sms-master-salt")
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def create_token(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(MASTER_SECRET.encode(), body, hashlib.sha256).digest()
    return _b64url_encode(body) + "." + _b64url_encode(sig)


def verify_token(token: str) -> Dict[str, Any]:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
        expected = hmac.new(MASTER_SECRET.encode(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("invalid signature")
        payload = json.loads(body.decode())
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            raise ValueError("token expired")
        return payload
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# ------------------ DB ------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            api_key TEXT UNIQUE,
            description TEXT,
            status TEXT,
            last_seen TEXT,
            meta TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER,
            modem_port TEXT,
            sender TEXT,
            timestamp TEXT,
            text TEXT,
            created_at TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER,
            phone TEXT,
            text TEXT,
            status TEXT,
            created_at TEXT,
            delivered_at TEXT,
            done_at TEXT,
            result TEXT,
            FOREIGN KEY(agent_id) REFERENCES agents(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def ensure_default_admin():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM users")
    cnt = cur.fetchone()[0]
    if cnt == 0:
        cur.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
            (
                MASTER_ADMIN_USER,
                hash_password(MASTER_ADMIN_PASSWORD),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        logger.info("Created default admin user %s", MASTER_ADMIN_USER)
    conn.close()


def _is_online(last_seen: Optional[str]) -> bool:
    if not last_seen:
        return False
    try:
        dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    return (datetime.now() - dt) <= timedelta(seconds=MASTER_AGENT_OFFLINE_SECONDS)


# ------------------ Pydantic models ------------------
class LoginRequest(BaseModel):
    username: str
    password: str


class AgentCreate(BaseModel):
    name: str
    description: Optional[str] = ""


class SendTask(BaseModel):
    agent_id: int
    phone: str
    text: str


class AgentHeartbeat(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class AgentMessage(BaseModel):
    modem_port: Optional[str] = ""
    sender: str
    timestamp: str
    text: str


class AgentMessagesPayload(BaseModel):
    messages: List[AgentMessage]


class AgentTaskAck(BaseModel):
    status: str
    result: Optional[str] = ""


# ------------------ Auth helpers ------------------
def get_current_user(authorization: str = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization
    if authorization.lower().startswith("bearer "):
        token = authorization[7:]
    payload = verify_token(token)
    uid = payload.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = ?", (uid,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")
    return {"id": row[0], "username": row[1]}


def get_agent_by_key(api_key: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM agents WHERE api_key = ?", (api_key,))
    row = cur.fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


# ------------------ FastAPI ------------------
app = FastAPI()
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


@app.on_event("startup")
def on_startup():
    init_db()
    ensure_default_admin()
    logger.info("Master service started")


@app.get("/")
def index():
    index_path = os.path.join(PUBLIC_DIR, "index.html")
    return FileResponse(index_path)


@app.post("/api/login")
def api_login(req: LoginRequest):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, password_hash FROM users WHERE username = ?", (req.username,)
    )
    row = cur.fetchone()
    conn.close()
    if row is None or row[1] != hash_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    exp = time.time() + MASTER_TOKEN_EXPIRE_HOURS * 3600
    token = create_token({"uid": row[0], "username": req.username, "exp": exp})
    return {"token": token, "expire_at": exp}


@app.get("/api/profile")
def api_profile(user=Depends(get_current_user)):
    return {"username": user["username"], "id": user["id"]}


@app.get("/api/agents")
def api_agents(user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, description, status, last_seen, api_key
        FROM agents
        ORDER BY id DESC
        """
    )
    rows_raw = [dict(r) for r in cur.fetchall()]
    conn.close()
    rows = []
    for r in rows_raw:
        online = _is_online(r.get("last_seen"))
        r["online"] = online
        r["derived_status"] = "online" if online else "offline"
        rows.append(r)
    return rows


@app.post("/api/agents")
def api_create_agent(req: AgentCreate, user=Depends(get_current_user)):
    api_key = secrets.token_hex(16)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO agents(name, api_key, description, status, last_seen)
        VALUES (?, ?, ?, ?, ?)
        """,
        (req.name, api_key, req.description or "", "new", now),
    )
    conn.commit()
    agent_id = cur.lastrowid
    conn.close()
    return {
        "id": agent_id,
        "name": req.name,
        "api_key": api_key,
        "description": req.description or "",
        "status": "new",
        "last_seen": now,
    }


@app.delete("/api/agents/{agent_id}")
def api_delete_agent(agent_id: int, user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM tasks WHERE agent_id = ?", (agent_id,))
    cur.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "ok", "deleted": agent_id}


@app.get("/api/messages")
def api_messages(
    limit: int = 100,
    after_id: Optional[int] = None,
    agent_id: Optional[int] = None,
    user=Depends(get_current_user),
):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    base_query = """
        SELECT m.id, m.agent_id, a.name AS agent_name, m.modem_port, m.sender,
               m.timestamp, m.text, m.created_at
        FROM messages m
        LEFT JOIN agents a ON m.agent_id = a.id
    """
    params: List[Any] = []
    where_clauses = []
    if after_id is not None:
        where_clauses.append("m.id > ?")
        params.append(after_id)
    if agent_id is not None:
        where_clauses.append("m.agent_id = ?")
        params.append(agent_id)
    if where_clauses:
        base_query += " WHERE " + " AND ".join(where_clauses)
    order_clause = " ORDER BY m.id DESC"
    limit_clause = " LIMIT ?"
    params.append(limit)
    cur.execute(base_query + order_clause + limit_clause, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.get("/api/tasks")
def api_tasks(user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id, t.agent_id, a.name AS agent_name, t.phone, t.text,
               t.status, t.created_at, t.delivered_at, t.done_at, t.result
        FROM tasks t
        LEFT JOIN agents a ON t.agent_id = a.id
        ORDER BY t.id DESC
        LIMIT 200
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.post("/api/send")
def api_send(req: SendTask, user=Depends(get_current_user)):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM agents WHERE id = ?", (req.agent_id,))
    row = cur.fetchone()
    if row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Agent not found")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO tasks(agent_id, phone, text, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (req.agent_id, req.phone.strip(), req.text, "pending", now),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()
    return {"status": "queued", "task_id": task_id}


# ------------------ Agent facing接口 ------------------
def require_agent(api_key: str = Header(None, alias="X-Agent-Key")) -> Dict[str, Any]:
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Key")
    agent = get_agent_by_key(api_key)
    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid agent key")
    return agent


@app.post("/api/agent/heartbeat")
def api_agent_heartbeat(payload: AgentHeartbeat, agent=Depends(require_agent)):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE agents
        SET name = COALESCE(?, name),
            status = COALESCE(?, status),
            meta = COALESCE(?, meta),
            last_seen = ?
        WHERE id = ?
        """,
        (
            payload.name,
            payload.status,
            json.dumps(payload.meta or {}),
            now,
            agent["id"],
        ),
    )
    conn.commit()
    # 查询 pending 任务
    cur.execute(
        """
        SELECT id, phone, text FROM tasks
        WHERE agent_id = ? AND status = 'pending'
        ORDER BY id ASC
        LIMIT 10
        """,
        (agent["id"],),
    )
    tasks = cur.fetchall()
    task_list = [{"id": t[0], "phone": t[1], "text": t[2]} for t in tasks]
    # 标记为 delivered
    if tasks:
        ids = [t[0] for t in tasks]
        delivered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            f"UPDATE tasks SET status = 'delivered', delivered_at = ? WHERE id IN ({','.join(['?']*len(ids))})",
            (delivered_at, *ids),
        )
        conn.commit()
    conn.close()
    return {"status": "ok", "tasks": task_list}


@app.post("/api/agent/messages")
def api_agent_messages(payload: AgentMessagesPayload, agent=Depends(require_agent)):
    if not payload.messages:
        return {"status": "ok", "inserted": 0}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    inserted = 0
    for msg in payload.messages:
        cur.execute(
            """
            INSERT INTO messages(agent_id, modem_port, sender, timestamp, text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                agent["id"],
                msg.modem_port or "",
                msg.sender,
                msg.timestamp,
                msg.text,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        inserted += 1
    conn.commit()
    conn.close()
    return {"status": "ok", "inserted": inserted}


@app.get("/api/agent/tasks")
def api_agent_tasks(agent=Depends(require_agent)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, phone, text FROM tasks
        WHERE agent_id = ? AND status = 'pending'
        ORDER BY id ASC
        LIMIT 10
        """,
        (agent["id"],),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if rows:
        delivered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ids = [r["id"] for r in rows]
        cur.execute(
            f"UPDATE tasks SET status = 'delivered', delivered_at = ? WHERE id IN ({','.join(['?']*len(ids))})",
            (delivered_at, *ids),
        )
        conn.commit()
    conn.close()
    return {"tasks": rows}


@app.post("/api/agent/tasks/{task_id}/ack")
def api_agent_task_ack(task_id: int, payload: AgentTaskAck, agent=Depends(require_agent)):
    if payload.status not in ("done", "error"):
        raise HTTPException(status_code=400, detail="Invalid status")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE tasks
        SET status = ?, done_at = ?, result = ?
        WHERE id = ? AND agent_id = ?
        """,
        (payload.status, now, payload.result or "", task_id, agent["id"]),
    )
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Task not found")
    conn.commit()
    conn.close()
    return {"status": "ok"}


# ------------------ 工具接口 ------------------
@app.get("/api/logs")
def api_logs(lines: int = 200, user=Depends(get_current_user)):
    try:
        if not os.path.exists(LOG_FILE):
            return {"lines": []}
        n = max(1, min(lines, MAX_LOG_LINES))
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            content = f.readlines()
        selected = content[-n:]
        return {"lines": [line.rstrip("\n") for line in selected]}
    except Exception as e:
        logger.error(f"read log error: {e}")
        return {"lines": []}
