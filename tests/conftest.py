import sqlite3

import pytest
from fastapi.testclient import TestClient

import db.database
import web.main

BOT_TEMPLATE = '"""\nname: {name}\n"""\nimport sys\n'


def make_bot(name: str, extra: str = "") -> bytes:
    return (BOT_TEMPLATE.format(name=name) + extra).encode()


def upload(client: TestClient, name: str, extra: str = ""):
    return client.post(
        "/submit",
        files={"file": ("bot.py", make_bot(name, extra), "text/plain")},
    )


def db_insert_bot(db_path: str, base_name: str, submitted_at: str | None = None) -> int:
    conn = sqlite3.connect(db_path)
    if submitted_at:
        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version,
                owner_token, file_path, submitted_at)
               VALUES (?,?,?,?,?,?)""",
            (base_name, base_name, 1, "token", f"/bots/{base_name}.py", submitted_at),
        )
    else:
        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version, owner_token, file_path)
               VALUES (?,?,?,?,?)""",
            (base_name, base_name, 1, "token", f"/bots/{base_name}.py"),
        )
    bot_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return bot_id


def db_insert_match(
    db_path: str,
    bot_x_id: int,
    bot_o_id: int,
    winner_id: int | None,
    result: str,
    played_at: str | None = None,
) -> int:
    conn = sqlite3.connect(db_path)
    if played_at:
        conn.execute(
            """INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result, played_at)
               VALUES (?,?,?,?,?)""",
            (bot_x_id, bot_o_id, winner_id, result, played_at),
        )
    else:
        conn.execute(
            """INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result)
               VALUES (?,?,?,?)""",
            (bot_x_id, bot_o_id, winner_id, result),
        )
    match_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return match_id


def db_insert_move(
    db_path: str,
    match_id: int,
    move_number: int,
    bot_id: int,
    board_state: str,
    error: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO moves (match_id, move_number, bot_id, board_state, error)
           VALUES (?,?,?,?,?)""",
        (match_id, move_number, bot_id, board_state, error),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture()
def client(db_path, tmp_path, monkeypatch):
    bots_dir = tmp_path / "bots"
    bots_dir.mkdir()

    monkeypatch.setattr(db.database, "DB_PATH", db_path)
    monkeypatch.setattr(web.main, "DB_PATH", db_path)
    monkeypatch.setattr(web.main, "BOTS_DIR", bots_dir)

    with TestClient(web.main.app) as c:
        yield c
