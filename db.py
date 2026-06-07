import os, hashlib, json
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = DATABASE_URL.startswith("postgres")

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3
    from pathlib import Path
    DB_PATH = Path(__file__).parent / "reviews.db"

@contextmanager
def get_conn():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL)
        try:
            yield conn
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()

def _exec(sql, params=(), fetchone=False, fetchall=False):
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
    with get_conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if USE_POSTGRES else conn.cursor()
        cur.execute(sql, params)
        if fetchone:
            row = cur.fetchone()
            return dict(row) if row else None
        if fetchall:
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        return None

def init_db():
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'client',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""" if USE_POSTGRES else """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'client',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS stores (
            place_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_own INTEGER DEFAULT 0,
            line_group_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (place_id, user_id)
        )""",
        """CREATE TABLE IF NOT EXISTS seen_reviews (
            review_hash TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            place_id TEXT NOT NULL,
            place_name TEXT NOT NULL,
            author TEXT,
            rating INTEGER,
            text TEXT,
            publish_time TEXT,
            seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sentiment TEXT,
            urgency INTEGER DEFAULT 0,
            topics TEXT,
            summary_ja TEXT,
            reply_draft_ja TEXT,
            PRIMARY KEY (review_hash, user_id)
        )"""
    ]
    with get_conn() as conn:
        cur = conn.cursor()
        for stmt in stmts:
            try:
                cur.execute(stmt)
            except Exception as e:
                if "already exists" not in str(e):
                    raise

def _sha256(pw): return hashlib.sha256(pw.encode()).hexdigest()

def create_user(email, password, name, role='client'):
    try:
        _exec("INSERT INTO users (email,password_hash,name,role) VALUES (?,?,?,?)",
              (email.lower().strip(), _sha256(password), name, role))
        return True
    except: return False

def authenticate(email, password):
    return _exec("SELECT * FROM users WHERE email=? AND password_hash=?",
                 (email.lower().strip(), _sha256(password)), fetchone=True)

def get_user(user_id):
    return _exec("SELECT * FROM users WHERE id=?", (user_id,), fetchone=True)

def get_all_users():
    return _exec("""
        SELECT u.*, COUNT(DISTINCT s.place_id) as store_count
        FROM users u LEFT JOIN stores s ON u.id=s.user_id
        GROUP BY u.id, u.email, u.password_hash, u.name, u.role, u.created_at
        ORDER BY u.created_at DESC
    """, fetchall=True) or []

def update_password(user_id, new_password):
    _exec("UPDATE users SET password_hash=? WHERE id=?", (_sha256(new_password), user_id))

def delete_user(user_id):
    _exec("DELETE FROM seen_reviews WHERE user_id=?", (user_id,))
    _exec("DELETE FROM stores WHERE user_id=?", (user_id,))
    _exec("DELETE FROM users WHERE id=?", (user_id,))

def get_stores(user_id):
    return _exec("SELECT * FROM stores WHERE user_id=? ORDER BY is_own DESC,name",
                 (user_id,), fetchall=True) or []

def upsert_store(user_id, place_id, name, is_own=False, line_group_id=None):
    if USE_POSTGRES:
        _exec("""INSERT INTO stores (place_id,user_id,name,is_own,line_group_id) VALUES (?,?,?,?,?)
            ON CONFLICT(place_id,user_id) DO UPDATE SET
            name=EXCLUDED.name,is_own=EXCLUDED.is_own,line_group_id=EXCLUDED.line_group_id""",
              (place_id, user_id, name, int(is_own), line_group_id))
    else:
        _exec("""INSERT INTO stores (place_id,user_id,name,is_own,line_group_id) VALUES (?,?,?,?,?)
            ON CONFLICT(place_id,user_id) DO UPDATE SET
            name=excluded.name,is_own=excluded.is_own,line_group_id=excluded.line_group_id""",
              (place_id, user_id, name, int(is_own), line_group_id))

def delete_store(user_id, place_id):
    _exec("DELETE FROM stores WHERE user_id=? AND place_id=?", (user_id, place_id))

def review_hash(place_id, author, publish_time):
    return hashlib.sha256(f"{place_id}|{author}|{publish_time}".encode()).hexdigest()[:16]

def is_seen(user_id, h):
    return _exec("SELECT 1 FROM seen_reviews WHERE review_hash=? AND user_id=?",
                 (h, user_id), fetchone=True) is not None

def mark_seen(user_id, h, place_id, place_name, author, rating, text, publish_time, analysis=None):
    a = analysis or {}
    try:
        _exec("""INSERT INTO seen_reviews
            (review_hash,user_id,place_id,place_name,author,rating,text,publish_time,
             sentiment,urgency,topics,summary_ja,reply_draft_ja)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (h,user_id,place_id,place_name,author,rating,text,publish_time,
               a.get("sentiment"),a.get("urgency",0),
               json.dumps(a.get("topics",[]),ensure_ascii=False),
               a.get("summary_ja"),a.get("reply_draft_ja")))
    except: pass

def get_store_stats(user_id):
    interval = "NOW() - INTERVAL '1 day'" if USE_POSTGRES else "datetime('now','-1 day')"
    return _exec(f"""
        SELECT s.place_id,s.name,s.is_own,
               COUNT(r.review_hash) as total_reviews,
               AVG(r.rating) as avg_rating,
               SUM(CASE WHEN r.seen_at >= {interval} THEN 1 ELSE 0 END) as new_today,
               SUM(CASE WHEN r.urgency>=70 THEN 1 ELSE 0 END) as alert_count
        FROM stores s LEFT JOIN seen_reviews r ON s.place_id=r.place_id AND s.user_id=r.user_id
        WHERE s.user_id=? GROUP BY s.place_id,s.name,s.is_own ORDER BY s.is_own DESC,s.name
    """, (user_id,), fetchall=True) or []

def get_recent_reviews(user_id, limit=30, own_only=False):
    where = "AND s.is_own=1" if own_only else ""
    return _exec(f"""
        SELECT r.*,s.is_own FROM seen_reviews r
        LEFT JOIN stores s ON r.place_id=s.place_id AND r.user_id=s.user_id
        WHERE r.user_id=? {where} ORDER BY r.seen_at DESC LIMIT ?
    """, (user_id,limit), fetchall=True) or []

def get_alerts(user_id, limit=20):
    return _exec("""
        SELECT r.*,s.is_own FROM seen_reviews r
        LEFT JOIN stores s ON r.place_id=s.place_id AND r.user_id=s.user_id
        WHERE r.user_id=? AND r.urgency>=70 AND s.is_own=1
        ORDER BY r.urgency DESC,r.seen_at DESC LIMIT ?
    """, (user_id,limit), fetchall=True) or []
