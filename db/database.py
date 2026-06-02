from pathlib import Path

import aiosqlite

DB_PATH = "ttt.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(SCHEMA_PATH.read_text())
        await db.commit()


async def get_owner_token(db: aiosqlite.Connection, base_name: str) -> str | None:
    async with db.execute(
        "SELECT owner_token FROM bots WHERE base_name = ? LIMIT 1", (base_name,)
    ) as cursor:
        row = await cursor.fetchone()
        return row["owner_token"] if row else None


async def get_next_version(db: aiosqlite.Connection, base_name: str) -> int:
    async with db.execute(
        "SELECT MAX(version) AS max_v FROM bots WHERE base_name = ?", (base_name,)
    ) as cursor:
        row = await cursor.fetchone()
        return (row["max_v"] or 0) + 1 if row else 1


async def insert_bot(
    db: aiosqlite.Connection,
    base_name: str,
    versioned_name: str,
    version: int,
    owner_token: str,
    file_path: str,
) -> None:
    await db.execute(
        """INSERT INTO bots (base_name, versioned_name, version, owner_token, file_path)
           VALUES (?, ?, ?, ?, ?)""",
        (base_name, versioned_name, version, owner_token, file_path),
    )
    await db.commit()


async def list_bots(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        "SELECT versioned_name, submitted_at FROM bots ORDER BY submitted_at DESC"
    ) as cursor:
        return list(await cursor.fetchall())


async def get_leaderboard(db: aiosqlite.Connection) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT
            b.id,
            b.versioned_name,
            b.submitted_at,
            COUNT(CASE WHEN m.winner_id = b.id
                            AND m.result IN ('x_wins', 'o_wins')
                            THEN 1 END) AS clean_wins,
            COUNT(CASE WHEN m.winner_id = b.id
                            AND m.result IN ('x_forfeit', 'o_forfeit') THEN 1 END)
                AS forfeit_wins,
            COUNT(CASE WHEN (m.bot_x_id = b.id OR m.bot_o_id = b.id)
                            AND m.result = 'cat' THEN 1 END) AS draws,
            COUNT(CASE WHEN (m.bot_x_id = b.id OR m.bot_o_id = b.id)
                            AND m.result != 'cat'
                            AND m.winner_id != b.id THEN 1 END) AS losses
        FROM bots b
        LEFT JOIN matches m ON (m.bot_x_id = b.id OR m.bot_o_id = b.id)
        GROUP BY b.id
        ORDER BY (clean_wins + forfeit_wins) DESC, b.submitted_at ASC
        """
    ) as cursor:
        return list(await cursor.fetchall())


async def list_bot_names(db: aiosqlite.Connection) -> list[str]:
    async with db.execute(
        "SELECT versioned_name FROM bots ORDER BY versioned_name"
    ) as cursor:
        return [row[0] for row in await cursor.fetchall()]


async def list_matches(
    db: aiosqlite.Connection, bot_name: str | None = None
) -> list[aiosqlite.Row]:
    base_query = """
        SELECT
            m.id,
            bx.versioned_name AS bot_x,
            bo.versioned_name AS bot_o,
            bw.versioned_name AS winner,
            m.result,
            m.played_at
        FROM matches m
        JOIN bots bx ON m.bot_x_id = bx.id
        JOIN bots bo ON m.bot_o_id = bo.id
        LEFT JOIN bots bw ON m.winner_id = bw.id
    """
    if bot_name:
        query = (
            base_query
            + """
            WHERE bx.versioned_name = ? OR bo.versioned_name = ?
            ORDER BY m.played_at DESC
        """
        )
        params: tuple = (bot_name, bot_name)
    else:
        query = base_query + "ORDER BY m.played_at DESC"
        params = ()

    async with db.execute(query, params) as cursor:
        return list(await cursor.fetchall())


async def get_match(db: aiosqlite.Connection, match_id: int) -> aiosqlite.Row | None:
    async with db.execute(
        """
        SELECT
            m.id,
            bx.versioned_name AS bot_x,
            bo.versioned_name AS bot_o,
            bw.versioned_name AS winner,
            m.result,
            m.played_at
        FROM matches m
        JOIN bots bx ON m.bot_x_id = bx.id
        JOIN bots bo ON m.bot_o_id = bo.id
        LEFT JOIN bots bw ON m.winner_id = bw.id
        WHERE m.id = ?
        """,
        (match_id,),
    ) as cursor:
        return await cursor.fetchone()


async def get_moves(db: aiosqlite.Connection, match_id: int) -> list[aiosqlite.Row]:
    async with db.execute(
        """
        SELECT mv.move_number, mv.board_state, mv.error, b.versioned_name AS bot_name
        FROM moves mv
        JOIN bots b ON mv.bot_id = b.id
        WHERE mv.match_id = ?
        ORDER BY mv.move_number
        """,
        (match_id,),
    ) as cursor:
        return list(await cursor.fetchall())
