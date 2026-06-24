"""Tests for `scripts/export_bots.py` — dumps bot source bytes to .py files."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import db.session as d
from entities.bot.model import Bot
from scripts.export_bots import (
    _md_escape,
    export_all,
    export_leaderboard,
    render_leaderboard_md,
    safe_filename,
)
from tests.conftest import TEST_ASYNC_URL, db_insert_bot, db_insert_match


@pytest_asyncio.fixture()
async def _bound_db(engine: AsyncEngine) -> AsyncIterator[None]:
    """Bind the async DB engine to the test Postgres so `export_all` sees
    the same DB the test fixtures populate."""
    d.reconfigure(TEST_ASYNC_URL)
    yield


async def _insert_bot(
    engine: AsyncEngine,
    *,
    base_name: str,
    versioned_name: str,
    source: bytes,
    version: int = 1,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Bot(
                base_name=base_name,
                versioned_name=versioned_name,
                version=version,
                owner_token="t",
                python_version="3.13",
                runtime_key="python-3.13",
                source=source,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# safe_filename — sanitization of versioned_name for filesystem use
# ---------------------------------------------------------------------------


def test_safe_filename_appends_py_extension() -> None:
    assert safe_filename("Alpha") == "Alpha.py"


def test_safe_filename_keeps_alphanumeric_dash_underscore() -> None:
    assert safe_filename("Alpha_Beta-V3") == "Alpha_Beta-V3.py"


def test_safe_filename_replaces_unsafe_chars_with_underscore() -> None:
    assert safe_filename("Bot/with spaces.and/slash") == "Bot_with_spaces_and_slash.py"


def test_safe_filename_collapses_consecutive_unsafe_chars() -> None:
    """Runs of unsafe characters collapse to a single underscore — keeps
    filenames readable instead of producing `My___Bot.py`."""
    assert safe_filename("My   Bot!!!") == "My_Bot_.py"


# ---------------------------------------------------------------------------
# export_all — writes each bot's source to a .py file in the output dir
# ---------------------------------------------------------------------------


async def test_export_all_creates_output_dir(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    out = tmp_path / "does-not-exist-yet"
    count = await export_all(out)
    assert out.exists()
    assert out.is_dir()
    assert count == 0


async def test_export_all_writes_one_file_per_bot(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    await _insert_bot(
        engine,
        base_name="Alpha",
        versioned_name="Alpha",
        source=b'"""\nname: Alpha\n"""\nimport sys\n',
    )
    await _insert_bot(
        engine,
        base_name="Beta",
        versioned_name="BetaV2",
        version=2,
        source=b'"""\nname: Beta\n"""\nprint("beta")\n',
    )

    count = await export_all(tmp_path)

    assert count == 2
    assert (tmp_path / "Alpha.py").read_bytes().startswith(b'"""')
    assert b"print(" in (tmp_path / "BetaV2.py").read_bytes()


async def test_export_all_preserves_source_bytes_verbatim(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    """The exported file equals the row's `source` bytes exactly — no
    decoding, re-encoding, or line-ending munging."""
    body = b'"""\nname: Gamma\npython: 3.14\n"""\n\nimport sys\n# trailing\n'
    await _insert_bot(
        engine, base_name="Gamma", versioned_name="Gamma", source=body
    )
    await export_all(tmp_path)
    assert (tmp_path / "Gamma.py").read_bytes() == body


async def test_export_all_skips_bots_with_empty_source(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    """Defensive — a bot with `source=b""` (shouldn't happen in production)
    is omitted from the export and not counted."""
    await _insert_bot(
        engine, base_name="Empty", versioned_name="Empty", source=b""
    )
    await _insert_bot(
        engine, base_name="Real", versioned_name="Real", source=b"import sys\n"
    )

    count = await export_all(tmp_path)

    assert count == 1
    assert not (tmp_path / "Empty.py").exists()
    assert (tmp_path / "Real.py").exists()


async def test_export_all_uses_safe_filename_for_unusual_names(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    """A bot whose versioned_name contains unsafe characters still gets a
    valid filename via `safe_filename`."""
    await _insert_bot(
        engine,
        base_name="Weird",
        versioned_name="Weird/Name With Spaces",
        source=b"x = 1\n",
    )

    await export_all(tmp_path)

    expected = tmp_path / "Weird_Name_With_Spaces.py"
    assert expected.exists()
    assert expected.read_bytes() == b"x = 1\n"


# ---------------------------------------------------------------------------
# render_leaderboard_md — pure function: list of rows → markdown table
# ---------------------------------------------------------------------------


class _FakeRow:
    """Stand-in for the SQLAlchemy `Row` returned by `BotRepository.leaderboard`."""

    def __init__(
        self,
        versioned_name: str,
        clean_wins: int,
        forfeit_wins: int,
        draws: int,
        losses: int,
        lifetime_wins: int,
        lifetime_losses: int,
    ) -> None:
        self.versioned_name = versioned_name
        self.clean_wins = clean_wins
        self.forfeit_wins = forfeit_wins
        self.draws = draws
        self.losses = losses
        self.lifetime_wins = lifetime_wins
        self.lifetime_losses = lifetime_losses


def test_render_leaderboard_md_empty_rows_shows_placeholder() -> None:
    out = render_leaderboard_md([])
    assert "# Leaderboard" in out
    assert "No bots submitted yet." in out


def test_render_leaderboard_md_includes_header_row() -> None:
    out = render_leaderboard_md([_FakeRow("AlphaBot", 1, 0, 0, 0, 1, 0)])
    assert "| # | Bot | Clean Wins | Forfeit Wins | Draws | Losses | Lifetime |" in out
    assert "|---|-----|------------|--------------|-------|--------|----------|" in out


def test_render_leaderboard_md_each_row_has_correct_values() -> None:
    rows = [
        _FakeRow("AlphaBot", 5, 1, 0, 2, 6, 2),
        _FakeRow("BetaBot", 3, 0, 1, 4, 3, 4),
    ]
    out = render_leaderboard_md(rows)
    assert "| 1 | AlphaBot | 5 | 1 | 0 | 2 | 6-2 |" in out
    assert "| 2 | BetaBot | 3 | 0 | 1 | 4 | 3-4 |" in out


def test_render_leaderboard_md_escapes_pipe_chars_in_bot_name() -> None:
    """A `|` in a bot name would otherwise break the table layout. The
    renderer escapes it to `\\|` so the row stays well-formed."""
    out = render_leaderboard_md(
        [_FakeRow("Pipe|Bot", 0, 0, 0, 0, 0, 0)]
    )
    assert "Pipe\\|Bot" in out


def test_md_escape_passes_through_normal_strings() -> None:
    assert _md_escape("AlphaBot") == "AlphaBot"
    assert _md_escape("Alpha-Bot_V2") == "Alpha-Bot_V2"


def test_md_escape_escapes_pipe() -> None:
    assert _md_escape("a|b|c") == "a\\|b\\|c"


# ---------------------------------------------------------------------------
# export_leaderboard — async, writes the markdown file to disk
# ---------------------------------------------------------------------------


async def test_export_leaderboard_writes_empty_state_when_no_bots(
    tmp_path: Path, _bound_db: None
) -> None:
    path = await export_leaderboard(tmp_path)
    assert path == tmp_path / "leaderboard.md"
    text = path.read_text()
    assert "No bots submitted yet." in text


async def test_export_leaderboard_writes_actual_standings(
    tmp_path: Path, engine: AsyncEngine, _bound_db: None
) -> None:
    """Inserts two bots + one match, then runs the exporter; the produced
    markdown table reflects the database state."""
    alpha = await db_insert_bot(engine, "AlphaBot")
    beta = await db_insert_bot(engine, "BetaBot")
    await db_insert_match(engine, alpha, beta, winner_id=alpha, result="x_wins")

    path = await export_leaderboard(tmp_path)
    text = path.read_text()

    assert "AlphaBot" in text
    assert "BetaBot" in text
    # AlphaBot has one clean win; BetaBot has one loss.
    assert "| 1 | AlphaBot | 1 |" in text
    assert "| 2 | BetaBot | 0 |" in text


async def test_export_leaderboard_creates_output_dir(
    tmp_path: Path, _bound_db: None
) -> None:
    out = tmp_path / "nested" / "leaderboard-out"
    path = await export_leaderboard(out)
    assert out.exists()
    assert path.exists()
