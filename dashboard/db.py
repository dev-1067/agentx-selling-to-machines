"""SQLite storage for the SAUDA control room: accounts, sessions, saved arena runs and classifier queries.

Passwords are never stored: only a PBKDF2-HMAC-SHA256 hash (200,000 iterations) with a per-user salt.
Sessions are random 32-byte tokens kept in an HttpOnly cookie and expire after 7 days.
"""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "sauda.db"
SESSION_DAYS = 7
ITER = 200_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
DEMO_EMAIL = "demo@sauda.local"
_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  store TEXT NOT NULL DEFAULT '', pw_hash TEXT NOT NULL, salt TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  buyer TEXT NOT NULL, seed INTEGER NOT NULL, ended TEXT NOT NULL, score REAL NOT NULL, rounds INTEGER NOT NULL,
  deal_total REAL, created_at REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS classifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text TEXT NOT NULL, label TEXT NOT NULL, confidence REAL NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS runs_user ON runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS cls_user ON classifications(user_id, created_at DESC);
"""


class AuthError(ValueError):
    pass


def _conn(path=None):
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


class DB:
    def __init__(self, path=None):
        self.c = _conn(path)
        with _LOCK:
            self.c.executescript(SCHEMA)
            self.c.commit()
        self._ensure_demo()

    # ------------------------------------------------------------ passwords
    @staticmethod
    def _hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITER).hex()

    def _ensure_demo(self):
        if not self.c.execute("SELECT 1 FROM users WHERE email = ?", (DEMO_EMAIL,)).fetchone():
            self.create_user("Demo Seller", DEMO_EMAIL, secrets.token_urlsafe(24) + "a1", "Demo Store")

    # ------------------------------------------------------------ users
    def create_user(self, name, email, password, store=""):
        name, email, store = (name or "").strip(), (email or "").strip().lower(), (store or "").strip()
        if not 2 <= len(name) <= 60:
            raise AuthError("Please enter your name (2 to 60 characters).")
        if not EMAIL_RE.match(email) or len(email) > 120:
            raise AuthError("Please enter a valid email address.")
        if len(password or "") < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise AuthError("Use at least 8 characters, with at least one letter and one number.")
        if len(store) > 60:
            raise AuthError("Store name is too long (60 characters at most).")
        salt = secrets.token_hex(16)
        with _LOCK:
            try:
                cur = self.c.execute(
                    "INSERT INTO users (name, email, store, pw_hash, salt, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (name, email, store or "My Store", self._hash(password, salt), salt, time.time()))
                self.c.commit()
            except sqlite3.IntegrityError:
                raise AuthError("An account with this email already exists. Try signing in.")
        return cur.lastrowid

    def verify(self, email, password):
        row = self.c.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
        if not row or not hmac.compare_digest(row["pw_hash"], self._hash(password or "", row["salt"])):
            raise AuthError("Email or password is incorrect.")
        return row["id"]

    def user(self, user_id):
        row = self.c.execute("SELECT id, name, email, store, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def demo_id(self):
        return self.c.execute("SELECT id FROM users WHERE email = ?", (DEMO_EMAIL,)).fetchone()["id"]

    def update_profile(self, user_id, name, store):
        name, store = (name or "").strip(), (store or "").strip()
        if not 2 <= len(name) <= 60:
            raise AuthError("Please enter your name (2 to 60 characters).")
        if not 1 <= len(store) <= 60:
            raise AuthError("Please enter a store name (1 to 60 characters).")
        with _LOCK:
            self.c.execute("UPDATE users SET name = ?, store = ? WHERE id = ?", (name, store, user_id))
            self.c.commit()
        return self.user(user_id)

    # ------------------------------------------------------------ sessions
    def new_session(self, user_id):
        token, now = secrets.token_urlsafe(32), time.time()
        with _LOCK:
            self.c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self.c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (token, user_id, now, now + SESSION_DAYS * 86400))
            self.c.commit()
        return token

    def session_user(self, token):
        if not token:
            return None
        row = self.c.execute("SELECT user_id FROM sessions WHERE token = ? AND expires_at > ?", (token, time.time())).fetchone()
        return self.user(row["user_id"]) if row else None

    def end_session(self, token):
        with _LOCK:
            self.c.execute("DELETE FROM sessions WHERE token = ?", (token or "",))
            self.c.commit()

    # ------------------------------------------------------------ activity
    def save_run(self, user_id, res, buyer, seed):
        deal_total = sum(i["qty"] * i["unit_price"] for i in res["deal"]) if res.get("deal") else None
        with _LOCK:
            cur = self.c.execute(
                "INSERT INTO runs (user_id, buyer, seed, ended, score, rounds, deal_total, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, buyer, seed, res["ended"], res["score"], len([m for m in res["transcript"] if m["from"] == "seller"]),
                 deal_total, time.time(), json.dumps(res, ensure_ascii=False)))
            self.c.commit()
        return cur.lastrowid

    def runs(self, user_id, limit=100):
        rows = self.c.execute("SELECT id, buyer, seed, ended, score, rounds, deal_total, created_at FROM runs "
                              "WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def run(self, user_id, run_id):
        row = self.c.execute("SELECT payload FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def delete_run(self, user_id, run_id):
        with _LOCK:
            n = self.c.execute("DELETE FROM runs WHERE id = ? AND user_id = ?", (run_id, user_id)).rowcount
            self.c.commit()
        return n

    def save_classification(self, user_id, text, label, conf):
        with _LOCK:
            self.c.execute("INSERT INTO classifications (user_id, text, label, confidence, created_at) VALUES (?, ?, ?, ?, ?)",
                           (user_id, text, label, conf, time.time()))
            self.c.commit()

    def classifications(self, user_id, limit=50):
        rows = self.c.execute("SELECT id, text, label, confidence, created_at FROM classifications WHERE user_id = ? "
                              "ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def stats(self, user_id):
        r = self.c.execute("SELECT COUNT(*) n, SUM(ended = 'deal') deals, AVG(score) avg FROM runs WHERE user_id = ?",
                           (user_id,)).fetchone()
        c = self.c.execute("SELECT COUNT(*) n, SUM(label != 'benign') flagged FROM classifications WHERE user_id = ?",
                           (user_id,)).fetchone()
        return {"runs": r["n"] or 0, "deals": r["deals"] or 0, "avg_score": round(r["avg"], 1) if r["avg"] is not None else None,
                "classified": c["n"] or 0, "flagged": c["flagged"] or 0}
