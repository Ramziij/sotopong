"""
SotoPong — Backend v1.2 (2v2 + avatars)
FastAPI + SQLite

Запуск:
  pip install fastapi uvicorn python-multipart
  python server.py
"""

import sqlite3, os, glob
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DB_PATH      = "sotopong.db"
STATIC_DIR   = "static"
AVATARS_DIR  = "avatars"
INITIAL_ELO  = 1000
K_FACTOR     = 32

app = FastAPI(title="SotoPong API", version="1.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs(AVATARS_DIR, exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                rating     INTEGER NOT NULL DEFAULT 1000,
                wins       INTEGER NOT NULL DEFAULT 0,
                losses     INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS matches (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                p1        TEXT NOT NULL,
                p2        TEXT NOT NULL,
                p1b       TEXT,
                p2b       TEXT,
                s1        INTEGER NOT NULL,
                s2        INTEGER NOT NULL,
                winner    TEXT NOT NULL,
                d1        INTEGER NOT NULL,
                d2        INTEGER NOT NULL,
                played_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
        """)
        conn.commit()
    # Миграция: добавляем p1b/p2b если их нет
    with get_db() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(matches)").fetchall()]
        if "p1b" not in cols:
            conn.execute("ALTER TABLE matches ADD COLUMN p1b TEXT")
        if "p2b" not in cols:
            conn.execute("ALTER TABLE matches ADD COLUMN p2b TEXT")
        conn.commit()

init_db()

# ── Helpers ───────────────────────────────────────────────────────────────────
def calc_elo(ra, rb, sa, sb):
    exp_a = 1 / (1 + 10 ** ((rb - ra) / 400))
    act_a = 1 if sa > sb else (0.5 if sa == sb else 0)
    da = round(K_FACTOR * (act_a - exp_a))
    db = round(K_FACTOR * ((1 - act_a) - (1 - exp_a)))
    return ra + da, rb + db, da, db

def fmt_match(m: dict) -> dict:
    try:
        dt = datetime.fromisoformat(m["played_at"])
        m["date"] = dt.strftime("%d.%m.%Y")
        m["time"] = dt.strftime("%H:%M")
    except Exception:
        m["date"] = m.get("played_at", "")
        m["time"] = ""
    return m

def find_avatar(player_id: int) -> Optional[str]:
    """Возвращает путь к файлу аватара или None."""
    files = glob.glob(os.path.join(AVATARS_DIR, f"{player_id}.*"))
    return files[0] if files else None

def player_to_dict(row) -> dict:
    p = dict(row)
    p["avatar_url"] = f"/api/players/{p['id']}/avatar" if find_avatar(p["id"]) else None
    return p

# ── Schemas ───────────────────────────────────────────────────────────────────
class PlayerCreate(BaseModel):
    name: str

class MatchCreate(BaseModel):
    p1_name:  str
    p2_name:  str
    score1:   int
    score2:   int
    p1b_name: Optional[str] = None
    p2b_name: Optional[str] = None

# ── Players ───────────────────────────────────────────────────────────────────
@app.get("/api/players")
def get_players():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM players ORDER BY rating DESC").fetchall()
    return [player_to_dict(r) for r in rows]

@app.post("/api/players", status_code=201)
def create_player(body: PlayerCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Имя не может быть пустым")
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO players (name, rating, wins, losses) VALUES (?, ?, 0, 0)", (name, INITIAL_ELO))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Игрок «{name}» уже существует")
        row = conn.execute("SELECT * FROM players WHERE name = ?", (name,)).fetchone()
    return player_to_dict(row)

@app.delete("/api/players/{player_id}")
def delete_player(player_id: int):
    with get_db() as conn:
        player = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        if not player:
            raise HTTPException(404, "Игрок не найден")
        name = player["name"]
        rows = conn.execute(
            "SELECT * FROM matches WHERE p1=? OR p2=? OR p1b=? OR p2b=?",
            (name, name, name, name)
        ).fetchall()
        for row in rows:
            m = dict(row)
            is_2v2 = bool(m.get("p1b"))
            team1_won = m["winner"] == m["p1"]
            pairs = ([(m["p1"], m["d1"], team1_won), (m["p1b"], m["d1"], team1_won),
                      (m["p2"], m["d2"], not team1_won), (m["p2b"], m["d2"], not team1_won)]
                     if is_2v2 else
                     [(m["p1"], m["d1"], m["winner"] == m["p1"]),
                      (m["p2"], m["d2"], m["winner"] == m["p2"])])
            for pname, delta, won in pairs:
                if pname and pname != name:
                    conn.execute(
                        "UPDATE players SET rating=rating-?, wins=wins-?, losses=losses-? WHERE name=?",
                        (delta, 1 if won else 0, 0 if won else 1, pname))
        conn.execute("DELETE FROM matches WHERE p1=? OR p2=? OR p1b=? OR p2b=?", (name, name, name, name))
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        conn.commit()
    # Удаляем аватар
    av = find_avatar(player_id)
    if av:
        try: os.remove(av)
        except: pass
    return {"ok": True}

# ── Avatar ────────────────────────────────────────────────────────────────────
@app.post("/api/players/{player_id}/avatar")
async def upload_avatar(player_id: int, file: UploadFile = File(...)):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM players WHERE id=?", (player_id,)).fetchone():
            raise HTTPException(404, "Игрок не найден")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Файл должен быть изображением")
    # Расширение из имени файла
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "gif", "webp", "avif"}:
        ext = "jpg"
    # Удаляем старый аватар
    old = find_avatar(player_id)
    if old:
        try: os.remove(old)
        except: pass
    # Сохраняем новый
    path = os.path.join(AVATARS_DIR, f"{player_id}.{ext}")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:  # 5 MB max
        raise HTTPException(413, "Файл слишком большой (макс. 5 МБ)")
    with open(path, "wb") as f:
        f.write(content)
    return {"ok": True, "avatar_url": f"/api/players/{player_id}/avatar"}

@app.get("/api/players/{player_id}/avatar")
def get_avatar(player_id: int):
    path = find_avatar(player_id)
    if not path:
        raise HTTPException(404, "Аватар не найден")
    return FileResponse(path)

# ── Matches ───────────────────────────────────────────────────────────────────
@app.get("/api/matches")
def get_matches():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM matches ORDER BY id DESC").fetchall()
    return [fmt_match(dict(r)) for r in rows]

@app.post("/api/matches", status_code=201)
def create_match(body: MatchCreate):
    if body.score1 == body.score2:
        raise HTTPException(400, "Ничья не допускается")
    if body.score1 < 0 or body.score2 < 0:
        raise HTTPException(400, "Счёт не может быть отрицательным")
    is_2v2 = bool(body.p1b_name and body.p2b_name)
    with get_db() as conn:
        def gp(name):
            p = conn.execute("SELECT * FROM players WHERE name=?", (name,)).fetchone()
            if not p: raise HTTPException(404, f"Игрок «{name}» не найден")
            return p
        pl1 = gp(body.p1_name); pl2 = gp(body.p2_name)
        winner = body.p1_name if body.score1 > body.score2 else body.p2_name
        team1_won = body.score1 > body.score2
        if is_2v2:
            pl1b = gp(body.p1b_name); pl2b = gp(body.p2b_name)
            _, _, d1, d2 = calc_elo((pl1["rating"]+pl1b["rating"])//2,
                                    (pl2["rating"]+pl2b["rating"])//2,
                                    body.score1, body.score2)
            for pname, delta, won in [(body.p1_name, d1, team1_won), (body.p1b_name, d1, team1_won),
                                       (body.p2_name, d2, not team1_won), (body.p2b_name, d2, not team1_won)]:
                conn.execute("UPDATE players SET rating=rating+?, wins=wins+?, losses=losses+? WHERE name=?",
                             (delta, 1 if won else 0, 0 if won else 1, pname))
            cur = conn.execute(
                "INSERT INTO matches (p1,p2,p1b,p2b,s1,s2,winner,d1,d2,played_at) VALUES (?,?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                (body.p1_name, body.p2_name, body.p1b_name, body.p2b_name, body.score1, body.score2, winner, d1, d2))
        else:
            if pl1["id"] == pl2["id"]: raise HTTPException(400, "Нельзя играть против себя")
            new_r1, new_r2, d1, d2 = calc_elo(pl1["rating"], pl2["rating"], body.score1, body.score2)
            conn.execute("UPDATE players SET rating=?, wins=wins+?, losses=losses+? WHERE name=?",
                         (new_r1, 1 if team1_won else 0, 0 if team1_won else 1, body.p1_name))
            conn.execute("UPDATE players SET rating=?, wins=wins+?, losses=losses+? WHERE name=?",
                         (new_r2, 0 if team1_won else 1, 1 if team1_won else 0, body.p2_name))
            cur = conn.execute(
                "INSERT INTO matches (p1,p2,p1b,p2b,s1,s2,winner,d1,d2,played_at) VALUES (?,?,NULL,NULL,?,?,?,?,?,datetime('now','localtime'))",
                (body.p1_name, body.p2_name, body.score1, body.score2, winner, d1, d2))
        match_id = cur.lastrowid
        conn.commit()
        return fmt_match(dict(conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()))

@app.delete("/api/matches/{match_id}")
def delete_match(match_id: int):
    with get_db() as conn:
        match = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
        if not match: raise HTTPException(404, "Матч не найден")
        m = dict(match)
        is_2v2 = bool(m.get("p1b"))
        team1_won = m["winner"] == m["p1"]
        pairs = ([(m["p1"], m["d1"], team1_won), (m["p1b"], m["d1"], team1_won),
                  (m["p2"], m["d2"], not team1_won), (m["p2b"], m["d2"], not team1_won)]
                 if is_2v2 else
                 [(m["p1"], m["d1"], m["winner"] == m["p1"]),
                  (m["p2"], m["d2"], m["winner"] == m["p2"])])
        for pname, delta, won in pairs:
            conn.execute("UPDATE players SET rating=rating-?, wins=wins-?, losses=losses-? WHERE name=?",
                         (delta, 1 if won else 0, 0 if won else 1, pname))
        conn.execute("DELETE FROM matches WHERE id=?", (match_id,))
        conn.commit()
    return {"ok": True}

# ── Frontend ──────────────────────────────────────────────────────────────────
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("🏓 SotoPong запущен: http://localhost:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
