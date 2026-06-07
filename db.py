import sqlite3, hashlib, json
from pathlib import Path

DB_PATH = Path(__file__).parent / "reviews.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name        TEXT NOT NULL,
            role        TEXT DEFAULT 'client',  -- 'admin' or 'client'
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stores (
            place_id    TEXT NOT NULL,
            user_id     INTEGER NOT NULL,
            name        TEXT NOT NULL,
            is_own      INTEGER DEFAULT 0,
            line_group_id TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (place_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS seen_reviews (
            review_hash   TEXT NOT NULL,
            user_id       INTEGER NOT NULL,
            place_id      TEXT NOT NULL,
            place_name    TEXT NOT NULL,
            author        TEXT,
            rating        INTEGER,
            text          TEXT,
            publish_time  TEXT,
            seen_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            sentiment     TEXT,
            urgency       INTEGER DEFAULT 0,
            topics        TEXT,
            summary_ja    TEXT,
            reply_draft_ja TEXT,
            PRIMARY KEY (review_hash, user_id)
        );
    """)
    conn.commit()
    conn.close()

# ---------- Auth ----------
def _sha256(pw): return hashlib.sha256(pw.encode()).hexdigest()

def create_user(email, password, name, role='client'):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO users (email,password_hash,name,role) VALUES (?,?,?,?)",
                     (email.lower().strip(), _sha256(password), name, role))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()

def authenticate(email, password):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email=? AND password_hash=?",
                       (email.lower().strip(), _sha256(password))).fetchone()
    conn.close()
    return dict(row) if row else None

def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_users():
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.*, COUNT(DISTINCT s.place_id) as store_count
        FROM users u LEFT JOIN stores s ON u.id=s.user_id
        GROUP BY u.id ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_password(user_id, new_password):
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (_sha256(new_password), user_id))
    conn.commit()
    conn.close()

def delete_user(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM seen_reviews WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM stores WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()

# ---------- Stores ----------
def get_stores(user_id):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM stores WHERE user_id=? ORDER BY is_own DESC,name",
                        (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def upsert_store(user_id, place_id, name, is_own=False, line_group_id=None):
    conn = get_conn()
    conn.execute("""
        INSERT INTO stores (place_id,user_id,name,is_own,line_group_id) VALUES (?,?,?,?,?)
        ON CONFLICT(place_id,user_id) DO UPDATE SET
            name=excluded.name,is_own=excluded.is_own,line_group_id=excluded.line_group_id
    """, (place_id, user_id, name, int(is_own), line_group_id))
    conn.commit()
    conn.close()

def delete_store(user_id, place_id):
    conn = get_conn()
    conn.execute("DELETE FROM stores WHERE user_id=? AND place_id=?", (user_id, place_id))
    conn.commit()
    conn.close()

# ---------- Reviews ----------
def review_hash(place_id, author, publish_time):
    return hashlib.sha256(f"{place_id}|{author}|{publish_time}".encode()).hexdigest()[:16]

def is_seen(user_id, h):
    conn = get_conn()
    row = conn.execute("SELECT 1 FROM seen_reviews WHERE review_hash=? AND user_id=?",
                       (h, user_id)).fetchone()
    conn.close()
    return row is not None

def mark_seen(user_id, h, place_id, place_name, author, rating, text, publish_time, analysis=None):
    a = analysis or {}
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO seen_reviews
        (review_hash,user_id,place_id,place_name,author,rating,text,publish_time,
         sentiment,urgency,topics,summary_ja,reply_draft_ja)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (h,user_id,place_id,place_name,author,rating,text,publish_time,
          a.get("sentiment"),a.get("urgency",0),
          json.dumps(a.get("topics",[]),ensure_ascii=False),
          a.get("summary_ja"),a.get("reply_draft_ja")))
    conn.commit()
    conn.close()

def get_store_stats(user_id):
    conn = get_conn()
    rows = conn.execute("""
        SELECT s.place_id,s.name,s.is_own,
               COUNT(r.review_hash) as total_reviews,
               AVG(r.rating) as avg_rating,
               SUM(CASE WHEN r.seen_at>=datetime('now','-1 day') THEN 1 ELSE 0 END) as new_today,
               SUM(CASE WHEN r.urgency>=70 THEN 1 ELSE 0 END) as alert_count
        FROM stores s LEFT JOIN seen_reviews r ON s.place_id=r.place_id AND s.user_id=r.user_id
        WHERE s.user_id=? GROUP BY s.place_id ORDER BY s.is_own DESC,s.name
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_recent_reviews(user_id, limit=30, own_only=False):
    conn = get_conn()
    where = "AND s.is_own=1" if own_only else ""
    rows = conn.execute(f"""
        SELECT r.*,s.is_own FROM seen_reviews r
        LEFT JOIN stores s ON r.place_id=s.place_id AND r.user_id=s.user_id
        WHERE r.user_id=? {where}
        ORDER BY r.seen_at DESC LIMIT ?
    """, (user_id,limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_alerts(user_id, limit=20):
    conn = get_conn()
    rows = conn.execute("""
        SELECT r.*,s.is_own FROM seen_reviews r
        LEFT JOIN stores s ON r.place_id=s.place_id AND r.user_id=s.user_id
        WHERE r.user_id=? AND r.urgency>=70 AND s.is_own=1
        ORDER BY r.urgency DESC,r.seen_at DESC LIMIT ?
    """, (user_id,limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
