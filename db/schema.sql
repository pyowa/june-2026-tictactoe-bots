CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_name TEXT NOT NULL,
    versioned_name TEXT NOT NULL UNIQUE,
    version INTEGER NOT NULL DEFAULT 1,
    owner_token TEXT NOT NULL,
    file_path TEXT NOT NULL,
    python_version TEXT NOT NULL DEFAULT '3',
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_x_id INTEGER NOT NULL REFERENCES bots(id),
    bot_o_id INTEGER NOT NULL REFERENCES bots(id),
    winner_id INTEGER REFERENCES bots(id),
    result TEXT NOT NULL CHECK(result IN ('x_wins', 'o_wins', 'cat', 'x_forfeit', 'o_forfeit')),
    played_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    move_number INTEGER NOT NULL,
    bot_id INTEGER NOT NULL REFERENCES bots(id),
    board_state TEXT NOT NULL,
    error TEXT
);
