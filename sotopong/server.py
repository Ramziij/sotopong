"""
SotoPong — Backend v1.3 (tournaments v2: prize modes, bracket persistence, rating)
FastAPI + PostgreSQL

Запуск:
  pip install fastapi uvicorn python-multipart psycopg2-binary
  DATABASE_URL=postgresql://user:pass@host/db python server.py
"""

import os, glob, json
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required (PostgreSQL)")

STATIC_DIR   = "static"
AVATARS_DIR  = "avatars"
INITIAL_ELO  = 1000
K_FACTOR     = 32

TOURNAMENT_RATING = {
    "1st": 50,
    "2nd": 25,
    "3rd": 10,
    "semifinal": 5,
    "other": 0,
}

app = FastAPI(title="SotoPong API", version="1.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
os.makedirs(AVATARS_DIR, exist_ok=True)
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# ── Database ──────────────────────────────────────────────────────────────────
class DBConn:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        cur = self.raw.cursor(cursor_factory=RealDictCursor)
        cur.execute(sql, params)
        return cur

    def commit(self):
        return self.raw.commit()

    def rollback(self):
        return self.raw.rollback()

    def close(self):
        return self.raw.close()

@contextmanager
def get_db():
    raw = psycopg2.connect(DATABASE_URL)
    raw.autocommit = False
    conn = DBConn(raw)
    try:
        yield conn
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id         SERIAL PRIMARY KEY,
                name       TEXT NOT NULL UNIQUE,
                rating     INTEGER NOT NULL DEFAULT 1000,
                wins       INTEGER NOT NULL DEFAULT 0,
                losses     INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id        SERIAL PRIMARY KEY,
                p1        TEXT NOT NULL,
                p2        TEXT NOT NULL,
                p1b       TEXT,
                p2b       TEXT,
                s1        INTEGER NOT NULL,
                s2        INTEGER NOT NULL,
                winner    TEXT NOT NULL,
                d1        INTEGER NOT NULL,
                d2        INTEGER NOT NULL,
                played_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id           SERIAL PRIMARY KEY,
                name         TEXT    NOT NULL,
                status       TEXT    NOT NULL DEFAULT 'active',
                prize_mode   TEXT    NOT NULL DEFAULT 'winner_takes_all',
                winner_name  TEXT,
                second_name  TEXT,
                third_name   TEXT,
                bracket_json TEXT,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tournament_players (
                id              SERIAL PRIMARY KEY,
                tournament_id   INTEGER NOT NULL REFERENCES tournaments(id),
                player_name     TEXT    NOT NULL,
                bet             INTEGER NOT NULL DEFAULT 0,
                rating_delta    INTEGER NOT NULL DEFAULT 0,
                finish_place    INTEGER
            );
        """)
        # Migrations: добавляем колонки если их нет
        conn.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS p1b TEXT;")
        conn.execute("ALTER TABLE matches ADD COLUMN IF NOT EXISTS p2b TEXT;")
        conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS prize_mode TEXT NOT NULL DEFAULT 'winner_takes_all';")
        conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS second_name TEXT;")
        conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS third_name TEXT;")
        conn.execute("ALTER TABLE tournaments ADD COLUMN IF NOT EXISTS bracket_json TEXT;")
        conn.execute("ALTER TABLE tournament_players ADD COLUMN IF NOT EXISTS rating_delta INTEGER NOT NULL DEFAULT 0;")
        conn.execute("ALTER TABLE tournament_players ADD COLUMN IF NOT EXISTS finish_place INTEGER;")

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
        dt = datetime.fromisoformat(str(m["played_at"]))
        m["date"] = dt.strftime("%d.%m.%Y")
        m["time"] = dt.strftime("%H:%M")
    except Exception:
        m["date"] = m.get("played_at", "")
        m["time"] = ""
    return m

def find_avatar(player_id: int) -> Optional[str]:
    files = glob.glob(os.path.join(AVATARS_DIR, f"{player_id}.*"))
    return files[0] if files else None

def player_to_dict(row) -> dict:
    p = dict(row)
    p["avatar_url"] = f"/api/players/{p['id']}/avatar" if find_avatar(p["id"]) else None
    return p

def get_tournament_dict(conn, tid: int) -> dict:
    t = conn.execute("SELECT * FROM tournaments WHERE id=%s", (tid,)).fetchone()
    if not t:
        return None
    d = dict(t)
    rows = conn.execute(
        "SELECT * FROM tournament_players WHERE tournament_id=%s ORDER BY id", (tid,)
    ).fetchall()
    d["players"] = [dict(p) for p in rows]
    d["prize_pool"] = sum(p["bet"] for p in d["players"])
    return d

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

class TournamentCreate(BaseModel):
    name: str
    prize_mode: str = "winner_takes_all"

class TournamentPlayerAdd(BaseModel):
    player_name: str
    bet: int

class TournamentBracketSave(BaseModel):
    bracket_json: str

class TournamentFinish(BaseModel):
    winner_name: str
    second_name:  Optional[str] = None
    third_name:   Optional[str] = None
    bracket_json: Optional[str] = None
    rounds_won:   Optional[dict] = None

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
            conn.execute(
                "INSERT INTO players (name, rating, wins, losses) VALUES (%s, %s, 0, 0)",
                (name, INITIAL_ELO)
            )
        except Exception:
            raise HTTPException(409, f"Игрок «{name}» уже существует")
        row = conn.execute("SELECT * FROM players WHERE name = %s", (name,)).fetchone()
    return player_to_dict(row)

@app.delete("/api/players/{player_id}")
def delete_player(player_id: int):
    with get_db() as conn:
        player = conn.execute("SELECT * FROM players WHERE id = %s", (player_id,)).fetchone()
        if not player:
            raise HTTPException(404, "Игрок не найден")
        name = player["name"]
        rows = conn.execute(
            "SELECT * FROM matches WHERE p1=%s OR p2=%s OR p1b=%s OR p2b=%s",
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
                        "UPDATE players SET rating=rating-%s, wins=wins-%s, losses=losses-%s WHERE name=%s",
                        (delta, 1 if won else 0, 0 if won else 1, pname))
        conn.execute("DELETE FROM matches WHERE p1=%s OR p2=%s OR p1b=%s OR p2b=%s", (name, name, name, name))
        conn.execute("DELETE FROM players WHERE id = %s", (player_id,))
    av = find_avatar(player_id)
    if av:
        try: os.remove(av)
        except: pass
    return {"ok": True}

# ── Avatar ────────────────────────────────────────────────────────────────────
@app.post("/api/players/{player_id}/avatar")
async def upload_avatar(player_id: int, file: UploadFile = File(...)):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM players WHERE id=%s", (player_id,)).fetchone():
            raise HTTPException(404, "Игрок не найден")
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Файл должен быть изображением")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in {"jpg", "jpeg", "png", "gif", "webp", "avif"}:
        ext = "jpg"
    old = find_avatar(player_id)
    if old:
        try: os.remove(old)
        except: pass
    path = os.path.join(AVATARS_DIR, f"{player_id}.{ext}")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
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
            p = conn.execute("SELECT * FROM players WHERE name=%s", (name,)).fetchone()
            if not p: raise HTTPException(404, f"Игрок «{name}» не найден")
            return p
        pl1 = gp(body.p1_name); pl2 = gp(body.p2_name)
        winner = body.p1_name if body.score1 > body.score2 else body.p2_name
        team1_won = body.score1 > body.score2
        if is_2v2:
            pl1b = gp(body.p1b_name); pl2b = gp(body.p2b_name)
            avg1 = (pl1["rating"] + pl1b["rating"]) // 2
            avg2 = (pl2["rating"] + pl2b["rating"]) // 2
            _, _, d1, d2 = calc_elo(avg1, avg2, body.score1, body.score2)
            for p, d, won in [(pl1, d1, team1_won), (pl1b, d1, team1_won),
                               (pl2, d2, not team1_won), (pl2b, d2, not team1_won)]:
                conn.execute(
                    "UPDATE players SET rating=rating+%s, wins=wins+%s, losses=losses+%s WHERE id=%s",
                    (d, 1 if won else 0, 0 if won else 1, p["id"]))
            cur = conn.execute(
                "INSERT INTO matches (p1,p2,p1b,p2b,s1,s2,winner,d1,d2) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (body.p1_name, body.p2_name, body.p1b_name, body.p2b_name,
                 body.score1, body.score2, winner, d1, d2))
        else:
            _, _, d1, d2 = calc_elo(pl1["rating"], pl2["rating"], body.score1, body.score2)
            conn.execute(
                "UPDATE players SET rating=rating+%s, wins=wins+%s, losses=losses+%s WHERE id=%s",
                (d1, 1 if team1_won else 0, 0 if team1_won else 1, pl1["id"]))
            conn.execute(
                "UPDATE players SET rating=rating+%s, wins=wins+%s, losses=losses+%s WHERE id=%s",
                (d2, 0 if team1_won else 1, 1 if team1_won else 0, pl2["id"]))
            cur = conn.execute(
                "INSERT INTO matches (p1,p2,s1,s2,winner,d1,d2) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (body.p1_name, body.p2_name, body.score1, body.score2, winner, d1, d2))
        new_id = cur.fetchone()["id"]
        row = conn.execute("SELECT * FROM matches WHERE id=%s", (new_id,)).fetchone()
    return fmt_match(dict(row))

@app.delete("/api/matches/{match_id}")
def delete_match(match_id: int):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM matches WHERE id=%s", (match_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Матч не найден")
        m = dict(row)
        is_2v2 = bool(m.get("p1b"))
        team1_won = m["winner"] == m["p1"]
        pairs = ([(m["p1"], m["d1"], team1_won), (m["p1b"], m["d1"], team1_won),
                  (m["p2"], m["d2"], not team1_won), (m["p2b"], m["d2"], not team1_won)]
                 if is_2v2 else
                 [(m["p1"], m["d1"], m["winner"] == m["p1"]),
                  (m["p2"], m["d2"], m["winner"] == m["p2"])])
        for pname, delta, won in pairs:
            if pname:
                conn.execute(
                    "UPDATE players SET rating=rating-%s, wins=wins-%s, losses=losses-%s WHERE name=%s",
                    (delta, 1 if won else 0, 0 if won else 1, pname))
        conn.execute("DELETE FROM matches WHERE id=%s", (match_id,))
    return {"ok": True}

# ── Tournaments ───────────────────────────────────────────────────────────────
@app.get("/api/tournaments")
def get_tournaments():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tournaments ORDER BY id DESC").fetchall()
        return [get_tournament_dict(conn, r["id"]) for r in rows]

@app.post("/api/tournaments", status_code=201)
def create_tournament(body: TournamentCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Название не может быть пустым")
    prize_mode = body.prize_mode if body.prize_mode in ("winner_takes_all", "top3_split") else "winner_takes_all"
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO tournaments (name, prize_mode) VALUES (%s,%s) RETURNING id",
            (name, prize_mode)
        )
        tid = cur.fetchone()["id"]
        return get_tournament_dict(conn, tid)

@app.delete("/api/tournaments/{tid}")
def delete_tournament(tid: int):
    with get_db() as conn:
        if not conn.execute("SELECT id FROM tournaments WHERE id=%s", (tid,)).fetchone():
            raise HTTPException(404, "Турнир не найден")
        conn.execute("DELETE FROM tournament_players WHERE tournament_id=%s", (tid,))
        conn.execute("DELETE FROM tournaments WHERE id=%s", (tid,))
    return {"ok": True}

@app.post("/api/tournaments/{tid}/players", status_code=201)
def add_tournament_player(tid: int, body: TournamentPlayerAdd):
    if body.bet < 0:
        raise HTTPException(400, "Ставка не может быть отрицательной")
    with get_db() as conn:
        t = conn.execute("SELECT * FROM tournaments WHERE id=%s", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Турнир не найден")
        if dict(t)["status"] != "active":
            raise HTTPException(400, "Турнир уже завершён")
        name = body.player_name.strip()
        if conn.execute(
            "SELECT id FROM tournament_players WHERE tournament_id=%s AND player_name=%s",
            (tid, name)
        ).fetchone():
            raise HTTPException(409, f"Игрок «{name}» уже в турнире")
        conn.execute(
            "INSERT INTO tournament_players (tournament_id, player_name, bet) VALUES (%s,%s,%s)",
            (tid, name, body.bet)
        )
        return get_tournament_dict(conn, tid)

@app.delete("/api/tournaments/{tid}/players/{pid}")
def remove_tournament_player(tid: int, pid: int):
    with get_db() as conn:
        t = conn.execute("SELECT status FROM tournaments WHERE id=%s", (tid,)).fetchone()
        if not t or dict(t)["status"] != "active":
            raise HTTPException(400, "Нельзя изменить завершённый турнир")
        if not conn.execute(
            "SELECT id FROM tournament_players WHERE id=%s AND tournament_id=%s", (pid, tid)
        ).fetchone():
            raise HTTPException(404, "Игрок не найден")
        conn.execute("DELETE FROM tournament_players WHERE id=%s", (pid,))
    return {"ok": True}

@app.post("/api/tournaments/{tid}/bracket")
def save_bracket(tid: int, body: TournamentBracketSave):
    with get_db() as conn:
        t = conn.execute("SELECT status FROM tournaments WHERE id=%s", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Турнир не найден")
        conn.execute("UPDATE tournaments SET bracket_json=%s WHERE id=%s", (body.bracket_json, tid))
    return {"ok": True}

@app.post("/api/tournaments/{tid}/finish")
def finish_tournament(tid: int, body: TournamentFinish):
    with get_db() as conn:
        t = conn.execute("SELECT * FROM tournaments WHERE id=%s", (tid,)).fetchone()
        if not t:
            raise HTTPException(404, "Турнир не найден")
        td = dict(t)
        if td["status"] != "active":
            raise HTTPException(400, "Турнир уже завершён")
        if not conn.execute(
            "SELECT id FROM tournament_players WHERE tournament_id=%s AND player_name=%s",
            (tid, body.winner_name)
        ).fetchone():
            raise HTTPException(400, "Победитель не найден в турнире")

        players_rows = conn.execute(
            "SELECT * FROM tournament_players WHERE tournament_id=%s", (tid,)
        ).fetchall()
        rounds_won = body.rounds_won or {}

        place_map = {}
        if body.winner_name:
            place_map[body.winner_name] = 1
        if body.second_name:
            place_map[body.second_name] = 2
        if body.third_name:
            place_map[body.third_name] = 3

        for p in players_rows:
            name = p["player_name"]
            place = place_map.get(name)
            rw = rounds_won.get(name, 0)

            if place == 1:
                delta = TOURNAMENT_RATING["1st"]
            elif place == 2:
                delta = TOURNAMENT_RATING["2nd"]
            elif place == 3:
                delta = TOURNAMENT_RATING["3rd"]
            elif rw >= 2:
                delta = TOURNAMENT_RATING["semifinal"]
            else:
                delta = TOURNAMENT_RATING["other"]

            if delta != 0:
                conn.execute("UPDATE players SET rating=rating+%s WHERE name=%s", (delta, name))

            conn.execute(
                "UPDATE tournament_players SET rating_delta=%s, finish_place=%s WHERE tournament_id=%s AND player_name=%s",
                (delta, place, tid, name)
            )

        bj = body.bracket_json or td.get("bracket_json")
        conn.execute(
            "UPDATE tournaments SET status='finished', winner_name=%s, second_name=%s, third_name=%s, bracket_json=%s WHERE id=%s",
            (body.winner_name, body.second_name, body.third_name, bj, tid)
        )
        return get_tournament_dict(conn, tid)

# ── Admin: import from SQLite ─────────────────────────────────────────────────
def require_admin(token: Optional[str]):
    if not ADMIN_TOKEN:
        raise HTTPException(500, "ADMIN_TOKEN not set")
    if token != ADMIN_TOKEN:
        raise HTTPException(403, "Forbidden")

@app.post("/admin/import_sqlite")
async def import_sqlite(token: str, file: UploadFile = File(...)):
    """Upload a sqlite file and import data into Postgres (players, matches)."""
    require_admin(token)
    import sqlite3, tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(tmp_fd)
    try:
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)
        src = sqlite3.connect(tmp_path)
        src.row_factory = sqlite3.Row
        players = src.execute("SELECT * FROM players").fetchall()
        matches  = src.execute("SELECT * FROM matches").fetchall()
        with get_db() as conn:
            for p in players:
                conn.execute(
                    """INSERT INTO players (id, name, rating, wins, losses, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                           name=EXCLUDED.name, rating=EXCLUDED.rating,
                           wins=EXCLUDED.wins, losses=EXCLUDED.losses""",
                    (p["id"], p["name"], p["rating"], p["wins"], p["losses"], p["created_at"]))
            for m in matches:
                conn.execute(
                    """INSERT INTO matches (id,p1,p2,p1b,p2b,s1,s2,winner,d1,d2,played_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (id) DO NOTHING""",
                    (m["id"], m["p1"], m["p2"], m["p1b"], m["p2b"],
                     m["s1"], m["s2"], m["winner"], m["d1"], m["d2"], m["played_at"]))
            conn.execute("SELECT setval('players_id_seq', (SELECT COALESCE(MAX(id),1) FROM players))")
            conn.execute("SELECT setval('matches_id_seq', (SELECT COALESCE(MAX(id),1) FROM matches))")
    finally:
        try: os.remove(tmp_path)
        except: pass
    return {"ok": True, "players": len(players), "matches": len(matches)}

# ── Frontend ──────────────────────────────────────────────────────────────────
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    print("🏓 SotoPong запущен: http://localhost:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
