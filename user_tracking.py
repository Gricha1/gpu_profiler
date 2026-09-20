"""Persistent usage sessions and aggregate statistics."""
import csv, io, sqlite3, threading, time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data/users/users.db"
IDLE_TIMEOUT_SEC = 120
_lock = threading.RLock()

def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10); db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    with _lock, _conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL COLLATE NOCASE,first_visit REAL NOT NULL,last_visit REAL NOT NULL,visit_count INTEGER NOT NULL DEFAULT 0,settings TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS usage_sessions(id INTEGER PRIMARY KEY,username TEXT NOT NULL COLLATE NOCASE,client_ip TEXT NOT NULL,started_at REAL NOT NULL,last_seen REAL NOT NULL,ended_at REAL,end_reason TEXT);
        CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_sessions(username);
        CREATE INDEX IF NOT EXISTS idx_usage_open ON usage_sessions(client_ip,ended_at);
        """)

def _expire(db, now):
    db.execute("UPDATE usage_sessions SET ended_at=last_seen,end_reason='timeout' WHERE ended_at IS NULL AND last_seen<?", (now-IDLE_TIMEOUT_SEC,))

def touch(username, client_ip, force_new=False):
    if not username or not client_ip: return
    now=time.time()
    with _lock, _conn() as db:
        _expire(db,now)
        row=db.execute("SELECT id,username FROM usage_sessions WHERE client_ip=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",(client_ip,)).fetchone()
        if row and (force_new or row['username'].casefold()!=username.casefold()):
            db.execute("UPDATE usage_sessions SET ended_at=last_seen,end_reason='switch' WHERE id=?",(row['id'],)); row=None
        if row:
            db.execute("UPDATE usage_sessions SET last_seen=? WHERE id=?",(now,row['id']))
            db.execute("UPDATE users SET last_visit=? WHERE username=?",(now,username))
        else:
            db.execute("INSERT INTO usage_sessions(username,client_ip,started_at,last_seen) VALUES(?,?,?,?)",(username,client_ip,now,now))
            db.execute("INSERT INTO users(username,first_visit,last_visit,visit_count) VALUES(?,?,?,1) ON CONFLICT(username) DO UPDATE SET last_visit=excluded.last_visit,visit_count=users.visit_count+1",(username,now,now))

def leave(username,client_ip,reason='leave'):
    now=time.time()
    with _lock,_conn() as db:
        db.execute("UPDATE usage_sessions SET last_seen=?,ended_at=?,end_reason=? WHERE id=(SELECT id FROM usage_sessions WHERE username=? AND client_ip=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1)",(now,now,reason,username,client_ip))

def dashboard():
    now=time.time()
    with _lock,_conn() as db:
        _expire(db,now)
        users=[dict(r) for r in db.execute("""SELECT u.username,u.first_visit,u.last_visit,COUNT(s.id) sessions,COALESCE(SUM(MAX(0,COALESCE(s.ended_at,s.last_seen)-s.started_at)),0) total_seconds,COALESCE(AVG(MAX(0,COALESCE(s.ended_at,s.last_seen)-s.started_at)),0) avg_seconds,MAX(CASE WHEN s.ended_at IS NULL THEN 1 ELSE 0 END) active FROM users u JOIN usage_sessions s ON s.username=u.username GROUP BY u.username ORDER BY u.last_visit DESC""")]
        daily=[dict(r) for r in db.execute("SELECT date(started_at,'unixepoch','localtime') period,COUNT(DISTINCT username) users,COUNT(*) sessions FROM usage_sessions WHERE started_at>=? GROUP BY period ORDER BY period DESC",(now-30*86400,))]
        monthly=[dict(r) for r in db.execute("SELECT strftime('%Y-%m',started_at,'unixepoch','localtime') period,COUNT(DISTINCT username) users,COUNT(*) sessions FROM usage_sessions GROUP BY period ORDER BY period DESC")]
    return {'generated_at':now,'total_users':len(users),'active_users':sum(int(x['active']) for x in users),'average_users_per_day':sum(x['users'] for x in daily)/len(daily) if daily else 0,'average_users_per_month':sum(x['users'] for x in monthly)/len(monthly) if monthly else 0,'users':users,'daily':daily,'monthly':monthly}

def export_csv():
    out=io.StringIO(); w=csv.writer(out); w.writerow(['username','client_ip','started_at','last_seen','ended_at','end_reason','duration_seconds'])
    with _lock,_conn() as db:
        _expire(db,time.time())
        for r in db.execute("SELECT * FROM usage_sessions ORDER BY started_at DESC"):
            end=r['ended_at'] or r['last_seen']; w.writerow([r['username'],r['client_ip'],r['started_at'],r['last_seen'],r['ended_at'] or '',r['end_reason'] or '',max(0,end-r['started_at'])])
    return out.getvalue()

def track_visit(username):
    touch(username, "legacy", force_new=True)
    with _conn() as db:
        row=db.execute("SELECT username,first_visit,last_visit,visit_count FROM users WHERE username=?",(username,)).fetchone()
    return dict(row) if row else {'error':'username required'}

def get_all_users():
    return dashboard()['users']

def get_user(username):
    with _conn() as db:
        row=db.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
    return dict(row) if row else None

def update_settings(username, settings):
    import json
    with _lock,_conn() as db:
        db.execute("UPDATE users SET settings=? WHERE username=?",(json.dumps(settings),username))
    return True

init_db()
