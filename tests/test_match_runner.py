import signal
import sqlite3
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runner.engine import MatchResult, Move
from runner.match_runner import find_unplayed_pairs, pull_images, record_match, run, unique_python_versions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def init_schema(db_path: str) -> None:
    from db.database import SCHEMA_PATH
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()


def insert_bot(
    db_path: str,
    name: str,
    file_path: str = "/bots/bot.py",
    python_version: str = "3",
) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO bots"
        " (base_name, versioned_name, version, owner_token, file_path, python_version)"
        " VALUES (?,?,?,?,?,?)",
        (name, name, 1, "token", file_path, python_version),
    )
    bot_id: int = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return bot_id


def insert_match(
    db_path: str,
    bot_x_id: int,
    bot_o_id: int,
    result: str,
    winner_id: int | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO matches (bot_x_id, bot_o_id, winner_id, result) VALUES (?,?,?,?)",
        (bot_x_id, bot_o_id, winner_id, result),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "test.db")
    init_schema(path)
    return path


# ---------------------------------------------------------------------------
# unique_python_versions / pull_images
# ---------------------------------------------------------------------------


def test_unique_python_versions_empty_db(db_path: str) -> None:
    assert unique_python_versions(db_path) == set()


def test_unique_python_versions_returns_distinct(db_path: str) -> None:
    insert_bot(db_path, "BotA", python_version="3.11")
    insert_bot(db_path, "BotB", python_version="3.12")
    insert_bot(db_path, "BotC", python_version="3.11")
    assert unique_python_versions(db_path) == {"3.11", "3.12"}


def test_pull_images_calls_docker_pull_for_each_version(db_path: str) -> None:
    insert_bot(db_path, "BotA", python_version="3.11")
    insert_bot(db_path, "BotB", python_version="3.12")
    with patch("runner.match_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        pull_images(db_path)
    pulled = [call[0][0] for call in mock_run.call_args_list]
    assert ["docker", "pull", "python:3.11"] in pulled
    assert ["docker", "pull", "python:3.12"] in pulled


def test_pull_images_warns_on_failure(db_path: str, capsys: pytest.CaptureFixture) -> None:
    insert_bot(db_path, "BotA", python_version="3.99")
    with patch("runner.match_runner.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        pull_images(db_path)
    assert "Warning" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# find_unplayed_pairs
# ---------------------------------------------------------------------------


def test_find_unplayed_pairs_empty_db(db_path: str) -> None:
    assert find_unplayed_pairs(db_path) == []


def test_find_unplayed_pairs_two_bots_no_matches(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    pairs = find_unplayed_pairs(db_path)
    ids = {(x, o) for x, _, _, o, _, _ in pairs}
    assert (a, b) in ids
    assert (b, a) in ids
    assert len(pairs) == 2


def test_find_unplayed_pairs_one_direction_played(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    insert_match(db_path, a, b, "x_wins", winner_id=a)
    pairs = find_unplayed_pairs(db_path)
    ids = {(x, o) for x, _, _, o, _, _ in pairs}
    assert (a, b) not in ids
    assert (b, a) in ids
    assert len(pairs) == 1


def test_find_unplayed_pairs_both_directions_played(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    insert_match(db_path, a, b, "x_wins", winner_id=a)
    insert_match(db_path, b, a, "x_wins", winner_id=b)
    assert find_unplayed_pairs(db_path) == []


def test_find_unplayed_pairs_three_bots(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    c = insert_bot(db_path, "BotC")
    pairs = find_unplayed_pairs(db_path)
    ids = {(x, o) for x, _, _, o, _, _ in pairs}
    assert len(ids) == 6
    for x in (a, b, c):
        for o in (a, b, c):
            if x != o:
                assert (x, o) in ids


def test_find_unplayed_pairs_returns_file_paths(db_path: str) -> None:
    insert_bot(db_path, "BotA", file_path="/bots/BotA.py")
    insert_bot(db_path, "BotB", file_path="/bots/BotB.py")
    pairs = find_unplayed_pairs(db_path)
    paths = {(xp, op) for _, xp, _, _, op, _ in pairs}
    assert ("/bots/BotA.py", "/bots/BotB.py") in paths
    assert ("/bots/BotB.py", "/bots/BotA.py") in paths


def test_find_unplayed_pairs_returns_python_versions(db_path: str) -> None:
    insert_bot(db_path, "BotA", python_version="3.11")
    insert_bot(db_path, "BotB", python_version="3.12")
    pairs = find_unplayed_pairs(db_path)
    versions = {(xv, ov) for _, _, xv, _, _, ov in pairs}
    assert ("3.11", "3.12") in versions
    assert ("3.12", "3.11") in versions


# ---------------------------------------------------------------------------
# record_match — winner_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "result,winner_is_x",
    [
        ("x_wins", True),
        ("o_wins", False),
        ("x_forfeit", False),
        ("o_forfeit", True),
        ("cat", None),
    ],
)
def test_record_match_winner_id(
    db_path: str, result: str, winner_is_x: bool | None
) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    match_result = MatchResult(result=result, moves=[])
    record_match(db_path, a, b, match_result)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT winner_id FROM matches").fetchone()
    conn.close()

    if winner_is_x is True:
        assert row[0] == a
    elif winner_is_x is False:
        assert row[0] == b
    else:
        assert row[0] is None


def test_record_match_stores_result_string(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    record_match(db_path, a, b, MatchResult(result="cat", moves=[]))

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT result FROM matches").fetchone()
    conn.close()
    assert row[0] == "cat"


# ---------------------------------------------------------------------------
# record_match — moves
# ---------------------------------------------------------------------------


def test_record_match_records_moves(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    moves = [
        Move(1, "x", "X|.|.\n.|.|.\n.|.|."),
        Move(2, "o", "X|.|.\n.|O|.\n.|.|."),
    ]
    record_match(db_path, a, b, MatchResult(result="cat", moves=moves))

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT move_number, bot_id, board_state, error FROM moves ORDER BY move_number"
    ).fetchall()
    conn.close()

    assert len(rows) == 2
    assert rows[0] == (1, a, "X|.|.\n.|.|.\n.|.|.", None)
    assert rows[1] == (2, b, "X|.|.\n.|O|.\n.|.|.", None)


def test_record_match_records_error_on_forfeit_move(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    moves = [
        Move(1, "x", "X|.|.\n.|.|.\n.|.|."),
        Move(2, "o", "X|.|.\n.|.|.\n.|.|.", error="invalid output: empty response"),
    ]
    record_match(db_path, a, b, MatchResult(result="o_forfeit", moves=moves))

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT error FROM moves WHERE move_number = 2"
    ).fetchone()
    conn.close()
    assert row[0] == "invalid output: empty response"


def test_record_match_x_move_uses_x_bot_id(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    moves = [Move(1, "x", "X|.|.\n.|.|.\n.|.|.")]
    record_match(db_path, a, b, MatchResult(result="x_wins", moves=moves))

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT bot_id FROM moves").fetchone()
    conn.close()
    assert row[0] == a


def test_record_match_o_move_uses_o_bot_id(db_path: str) -> None:
    a = insert_bot(db_path, "BotA")
    b = insert_bot(db_path, "BotB")
    moves = [
        Move(1, "x", "X|.|.\n.|.|.\n.|.|."),
        Move(2, "o", "X|.|.\n.|O|.\n.|.|."),
    ]
    record_match(db_path, a, b, MatchResult(result="cat", moves=moves))

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT bot_id FROM moves WHERE move_number = 2"
    ).fetchone()
    conn.close()
    assert row[0] == b


# ---------------------------------------------------------------------------
# run — shutdown / signal handling
# ---------------------------------------------------------------------------


def test_run_exits_when_no_pairs(db_path: str) -> None:
    """Runner exits the loop immediately when shutdown is set and no pairs exist."""
    call_count = 0

    def fake_find(path: str) -> list:
        nonlocal call_count
        call_count += 1
        return []

    def fake_sleep(secs: float) -> None:
        import os
        os.kill(os.getpid(), signal.SIGINT)

    with (
        patch("runner.match_runner.pull_images"),
        patch("runner.match_runner.find_unplayed_pairs", side_effect=fake_find),
        patch("runner.match_runner.time.sleep", side_effect=fake_sleep),
    ):
        run(db_path=db_path, poll_interval=0)

    assert call_count >= 1


def test_run_shutdown_flag_stops_between_matches(db_path: str) -> None:
    """Once shutdown is set, the runner stops before starting the next match."""
    insert_bot(db_path, "BotA", file_path="/bots/BotA.py")
    insert_bot(db_path, "BotB", file_path="/bots/BotB.py")

    played: list[tuple[int, int]] = []

    def fake_play(
        x_path: str, o_path: str,
        x_python: str = "3", o_python: str = "3",
        timeout: int = 10,
    ) -> MatchResult:
        import os
        os.kill(os.getpid(), signal.SIGINT)
        return MatchResult(result="cat", moves=[])

    def fake_record(
        path: str, x_id: int, o_id: int, result: MatchResult
    ) -> None:
        played.append((x_id, o_id))

    with (
        patch("runner.match_runner.pull_images"),
        patch("runner.match_runner.play_match", side_effect=fake_play),
        patch("runner.match_runner.record_match", side_effect=fake_record),
    ):
        run(db_path=db_path, poll_interval=0)

    assert len(played) == 1
