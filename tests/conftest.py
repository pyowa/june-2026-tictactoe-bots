import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import db.database
import web.main
from db.models import Base


def create_schema(db_path: str) -> None:
    """Build the full schema synchronously from the ORM models."""
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()

BOT_TEMPLATE = '"""\nname: {name}\n"""\nimport sys\n'


def make_bot(name: str, extra: str = "") -> bytes:
    return (BOT_TEMPLATE.format(name=name) + extra).encode()


def upload(client: TestClient, name: str, extra: str = ""):
    return client.post(
        "/submit",
        files={"file": ("bot.py", make_bot(name, extra), "text/plain")},
    )


def db_insert_bot(
    db_path: str,
    base_name: str,
    submitted_at: str | None = None,
    python_version: str = "3",
) -> int:
    conn = sqlite3.connect(db_path)
    if submitted_at:
        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version,
                owner_token, file_path, python_version, submitted_at)
               VALUES (?,?,?,?,?,?,?)""",
            (base_name, base_name, 1, "token", f"/bots/{base_name}.py",
             python_version, submitted_at),
        )
    else:
        conn.execute(
            """INSERT INTO bots
               (base_name, versioned_name, version,
                owner_token, file_path, python_version)
               VALUES (?,?,?,?,?,?)""",
            (
                base_name,
                base_name,
                1,
                "token",
                f"/bots/{base_name}.py",
                python_version,
            ),
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
    path = str(tmp_path / "test.db")
    create_schema(path)
    return path


@pytest.fixture()
def bots_dir(tmp_path):
    path = tmp_path / "bots"
    path.mkdir()
    return path


@pytest.fixture()
def client(db_path, bots_dir, monkeypatch):
    db.database.reconfigure(db_path)
    monkeypatch.setattr(web.main, "BOTS_DIR", bots_dir)

    with TestClient(web.main.app) as c:
        yield c
