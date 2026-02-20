"""
SotoPong — Backend
FastAPI + SQLite (встроен в Python, устанавливать не нужно)

Запуск:
  pip install fastapi uvicorn
  python server.py
"""

import sqlite3
import math
import os
from datetime import datetime
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────
DB_PATH      = "sotopong.db"
STATIC_DIR   = "static"          # папка с index.html (фронтенд)
INITIAL_ELO  = 1000
K_FACTOR     = 32

app = FastAPI(title="SotoPong API", version="1.0.0")

# Разрешаем запросы с любого origin (нужно для разработки)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Database ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row   # возвращать dict-like строки
    conn.execute("PRAGMA journal_mode=WAL")  # лучше для конкурентного доступа
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS players (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                name      TEXT NOT NULL UNIQUE,
                rating    INTEGER NOT NULL DEFAULT 1000,
                wins      INTEGER NOT NULL DEFAULT 0,
                losses    INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS matches (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                p1         TEXT NOT NULL,
                p2         TEXT NOT NULL,
                s1         INTEGER NOT NULL,
                s2         INTEGER NOT NULL,
                winner     TEXT NOT NULL,
                d1         INTEGER NOT NULL,
                d2         INTEGER NOT NULL,
                played_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
        """)
        conn.commit()

init_db()

# ── ELO ────────────────────────────────────────────────────────────────────
def calc_elo(ra: int, rb: int, sa: int, sb: int):
    exp_a = 1 / (1 + 10 ** ((rb - ra) / 400))
    exp_b = 1 - exp_a
    act_a = 1 if sa > sb else (0.5 if sa == sb else 0)
    act_b = 1 - act_a
    da = round(K_FACTOR * (act_a - exp_a))
    db = round(K_FACTOR * (act_b - exp_b))
    return ra + da, rb + db, da, db

# ── Pydantic schemas ────────────────────────────────────────────────────────
class PlayerCreate(BaseModel):
    name: str

class MatchCreate(BaseModel):
    p1_name: str
    p2_name: str
    score1:  int
    score2:  int

class MatchDelete(BaseModel):
    match_id: int

# ── Players ─────────────────────────────────────────────────────────────────
@app.get("/api/players")
def get_players():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM players ORDER BY rating DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/players", status_code=201)
def create_player(body: PlayerCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Имя не может быть пустым")
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO players (name, rating, wins, losses) VALUES (?, ?, 0, 0)",
                (name, INITIAL_ELO)
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"Игрок «{name}» уже существует")
        row = conn.execute("SELECT * FROM players WHERE name = ?", (name,)).fetchone()
    return dict(row)


@app.delete("/api/players/{player_id}")
def delete_player(player_id: int):
    with get_db() as conn:
        player = conn.execute(
            "SELECT * FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        if not player:
            raise HTTPException(404, "Игрок не найден")

        name = player["name"]

        # Находим все матчи этого игрока и откатываем рейтинг соперников
        player_matches = conn.execute(
            "SELECT * FROM matches WHERE p1 = ? OR p2 = ?", (name, name)
        ).fetchall()

        for m in player_matches:
            opp_name  = m["p2"] if m["p1"] == name else m["p1"]
            opp_delta = m["d2"] if m["p1"] == name else m["d1"]
            opp_won   = m["winner"] == opp_name
            conn.execute(
                """UPDATE players SET
                     rating = rating - ?,
                     wins   = wins   - ?,
                     losses = losses - ?
                   WHERE name = ?""",
                (opp_delta,
                 1 if opp_won else 0,
                 0 if opp_won else 1,
                 opp_name)
            )

        # Удаляем матчи и самого игрока
        conn.execute("DELETE FROM matches WHERE p1 = ? OR p2 = ?", (name, name))
        conn.execute("DELETE FROM players WHERE id = ?", (player_id,))
        conn.commit()

    return {"ok": True}

# ── Matches ──────────────────────────────────────────────────────────────────
@app.get("/api/matches")
def get_matches():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM matches ORDER BY id DESC"
        ).fetchall()
    # форматируем дату для фронтенда
    result = []
    for r in rows:
        m = dict(r)
        try:
            dt = datetime.fromisoformat(m["played_at"])
            m["date"] = dt.strftime("%d.%m.%Y")
            m["time"] = dt.strftime("%H:%M")
        except Exception:
            m["date"] = m["played_at"]
            m["time"] = ""
        result.append(m)
    return result


@app.post("/api/matches", status_code=201)
def create_match(body: MatchCreate):
    if body.score1 == body.score2:
        raise HTTPException(400, "Ничья не допускается")
    if body.score1 < 0 or body.score2 < 0:
        raise HTTPException(400, "Счёт не может быть отрицательным")

    with get_db() as conn:
        pl1 = conn.execute("SELECT * FROM players WHERE name = ?", (body.p1_name,)).fetchone()
        pl2 = conn.execute("SELECT * FROM players WHERE name = ?", (body.p2_name,)).fetchone()
        if not pl1:
            raise HTTPException(404, f"Игрок «{body.p1_name}» не найден")
        if not pl2:
            raise HTTPException(404, f"Игрок «{body.p2_name}» не найден")
        if pl1["id"] == pl2["id"]:
            raise HTTPException(400, "Нельзя сыграть против самого себя")

        new_r1, new_r2, d1, d2 = calc_elo(
            pl1["rating"], pl2["rating"],
            body.score1, body.score2
        )
        winner = body.p1_name if body.score1 > body.score2 else body.p2_name

        # Обновляем рейтинги
        conn.execute(
            """UPDATE players SET
                 rating = ?,
                 wins   = wins   + ?,
                 losses = losses + ?
               WHERE name = ?""",
            (new_r1,
             1 if body.score1 > body.score2 else 0,
             1 if body.score1 < body.score2 else 0,
             body.p1_name)
        )
        conn.execute(
            """UPDATE players SET
                 rating = ?,
                 wins   = wins   + ?,
                 losses = losses + ?
               WHERE name = ?""",
            (new_r2,
             1 if body.score2 > body.score1 else 0,
             1 if body.score2 < body.score1 else 0,
             body.p2_name)
        )

        # Сохраняем матч
        cur = conn.execute(
            """INSERT INTO matches (p1, p2, s1, s2, winner, d1, d2, played_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
            (body.p1_name, body.p2_name, body.score1, body.score2, winner, d1, d2)
        )
        match_id = cur.lastrowid
        conn.commit()

        match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        m = dict(match)
        try:
            dt = datetime.fromisoformat(m["played_at"])
            m["date"] = dt.strftime("%d.%m.%Y")
            m["time"] = dt.strftime("%H:%M")
        except Exception:
            m["date"] = m["played_at"]
            m["time"] = ""
        return m


@app.delete("/api/matches/{match_id}")
def delete_match(match_id: int):
    with get_db() as conn:
        match = conn.execute("SELECT * FROM matches WHERE id = ?", (match_id,)).fetchone()
        if not match:
            raise HTTPException(404, "Матч не найден")

        m = dict(match)
        # Откатываем рейтинги
        for pname, delta, is_winner in [
            (m["p1"], m["d1"], m["winner"] == m["p1"]),
            (m["p2"], m["d2"], m["winner"] == m["p2"]),
        ]:
            conn.execute(
                """UPDATE players SET
                     rating = rating - ?,
                     wins   = wins   - ?,
                     losses = losses - ?
                   WHERE name = ?""",
                (delta,
                 1 if is_winner else 0,
                 0 if is_winner else 1,
                 pname)
            )

        conn.execute("DELETE FROM matches WHERE id = ?", (match_id,))
        conn.commit()

    return {"ok": True}

# ── Serve frontend ───────────────────────────────────────────────────────────
# Раздаём статику из папки static/
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

# ── Run ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("🏓 SotoPong запущен: http://localhost:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
