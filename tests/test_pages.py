import pytest

from tests.conftest import (
    db_insert_bot,
    db_insert_match,
    db_insert_move,
    upload,
)

BOARD_START = ".|.|.\n.|.|.\n.|.|."
BOARD_AFTER_X = "X|.|.\n.|.|.\n.|.|."
BOARD_AFTER_O = "X|.|.\n.|O|.\n.|.|."


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


def test_leaderboard_returns_200(client):
    resp = client.get("/leaderboard")
    assert resp.status_code == 200


def test_leaderboard_empty_state(client):
    resp = client.get("/leaderboard")
    assert "No bots submitted yet" in resp.text


def test_leaderboard_shows_submitted_bots(client):
    upload(client, "Hal")
    resp = client.get("/leaderboard")
    assert "Hal" in resp.text


def test_leaderboard_orders_by_wins_descending(client, engine):
    a = db_insert_bot(engine, "LowBot")
    b = db_insert_bot(engine, "HighBot")
    db_insert_match(engine, a, b, winner_id=b, result="o_wins")
    db_insert_match(engine, a, b, winner_id=b, result="o_wins")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    # HighBot (2 wins) must appear before LowBot (1 win)
    assert text.index("HighBot") < text.index("LowBot")


def test_leaderboard_tie_broken_by_earlier_submission(client, engine):
    early = db_insert_bot(engine, "EarlyBot", submitted_at="2024-01-01 00:00:00")
    late = db_insert_bot(engine, "LateBot", submitted_at="2024-06-01 00:00:00")
    # Give each one win so they're tied
    db_insert_match(engine, early, late, winner_id=early, result="x_wins")
    db_insert_match(engine, late, early, winner_id=late, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("EarlyBot") < text.index("LateBot")


def test_leaderboard_clean_win_count_is_correct(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    bot_a_pos = text.index("BotA")
    row_section = text[bot_a_pos : bot_a_pos + 300]
    assert ">3<" in row_section


def test_leaderboard_forfeit_win_shown_separately(client, engine):
    a = db_insert_bot(engine, "GoodBot")
    b = db_insert_bot(engine, "CrashBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    text = resp.text
    bot_a_pos = text.index("GoodBot")
    row_section = text[bot_a_pos : bot_a_pos + 400]
    # 1 clean win and 1 forfeit win, each in their own cell
    assert ">1<" in row_section
    assert row_section.count(">1<") == 2


def test_leaderboard_forfeit_win_ranks_above_zero_wins(client, engine):
    a = db_insert_bot(engine, "GoodBot")
    b = db_insert_bot(engine, "CrashBot")
    db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("GoodBot") < text.index("CrashBot")


def test_leaderboard_draw_not_counted_as_win(client, engine):
    a = db_insert_bot(engine, "DrawBot")
    b = db_insert_bot(engine, "OtherBot")
    db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get("/leaderboard")
    text = resp.text
    draw_pos = text.index("DrawBot")
    row_section = text[draw_pos : draw_pos + 300]
    assert ">0<" in row_section


# ---------------------------------------------------------------------------
# Matches list
# ---------------------------------------------------------------------------


def test_matches_returns_200(client):
    resp = client.get("/matches")
    assert resp.status_code == 200


def test_matches_empty_state(client):
    resp = client.get("/matches")
    assert "No matches played yet" in resp.text


def test_matches_shows_both_bot_names(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert "AlphaBot" in resp.text
    assert "BetaBot" in resp.text


@pytest.mark.parametrize(
    "result,expected",
    [
        ("x_wins", "AlphaBot won"),
        ("o_wins", "BetaBot won"),
        ("cat", "Cat game"),
        ("x_forfeit", "AlphaBot forfeited"),
        ("o_forfeit", "BetaBot forfeited"),
    ],
)
def test_matches_result_label(client, engine, result, expected):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    winner_id = a if result == "x_wins" else b if result == "o_wins" else None
    if result in ("x_forfeit",):
        winner_id = b
    if result in ("o_forfeit",):
        winner_id = a
    db_insert_match(engine, a, b, winner_id=winner_id, result=result)

    resp = client.get("/matches")
    assert expected in resp.text


def test_matches_most_recent_first(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    db_insert_match(
        engine, a, b, winner_id=a, result="x_wins", played_at="2024-01-01 00:00:00"
    )
    db_insert_match(
        engine, b, a, winner_id=b, result="x_wins", played_at="2024-06-01 00:00:00"
    )

    resp = client.get("/matches")
    # The newer match has BotB as X; the older has BotA as X.
    # BotB-as-X row should come first in the table.
    text = resp.text
    first_occurrence_a = text.index("2024-01-01")
    first_occurrence_b = text.index("2024-06-01")
    assert first_occurrence_b < first_occurrence_a


def test_matches_contains_link_to_detail(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get("/matches")
    assert f"/matches/{match_id}" in resp.text


# ---------------------------------------------------------------------------
# Matches list — bot filter
# ---------------------------------------------------------------------------


def test_matches_filter_shows_only_matching_bot(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    c = db_insert_bot(engine, "GammaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_match(engine, b, c, winner_id=b, result="x_wins")

    resp = client.get("/matches?bot=AlphaBot")
    # GammaBot appears only in the dropdown, not in a table cell
    assert "<td>AlphaBot " in resp.text
    assert "<td>BetaBot " in resp.text   # shared match with AlphaBot
    assert "<td>GammaBot " not in resp.text


def test_matches_filter_includes_bot_as_o(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    c = db_insert_bot(engine, "GammaBot")
    db_insert_match(engine, c, a, winner_id=c, result="x_wins")  # AlphaBot is O
    db_insert_match(engine, b, c, winner_id=b, result="x_wins")  # AlphaBot uninvolved

    resp = client.get("/matches?bot=AlphaBot")
    assert "<td>GammaBot " in resp.text
    assert "<td>BetaBot " not in resp.text


def test_matches_no_filter_shows_all(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    c = db_insert_bot(engine, "GammaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_match(engine, b, c, winner_id=b, result="x_wins")

    resp = client.get("/matches")
    assert "<td>AlphaBot " in resp.text
    assert "<td>BetaBot " in resp.text
    assert "<td>GammaBot " in resp.text


def test_matches_filter_unknown_bot_shows_empty(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches?bot=NoSuchBot")
    assert "No matches played yet" in resp.text


def test_matches_filter_shows_selected_bot_in_heading(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches?bot=AlphaBot")
    assert "AlphaBot" in resp.text
    # heading should indicate filtered view
    assert "Matches" in resp.text


def test_matches_dropdown_contains_all_bots(client, engine):
    db_insert_bot(engine, "AlphaBot")
    db_insert_bot(engine, "BetaBot")

    resp = client.get("/matches")
    assert 'value="AlphaBot"' in resp.text
    assert 'value="BetaBot"' in resp.text


def test_matches_dropdown_preselects_filtered_bot(client, engine):
    db_insert_bot(engine, "AlphaBot")
    db_insert_bot(engine, "BetaBot")

    resp = client.get("/matches?bot=AlphaBot")
    # The AlphaBot option should carry the selected attribute; BetaBot should not
    text = resp.text
    alpha_option_pos = text.index('value="AlphaBot"')
    beta_option_pos = text.index('value="BetaBot"')
    alpha_section = text[alpha_option_pos : alpha_option_pos + 60]
    beta_section = text[beta_option_pos : beta_option_pos + 60]
    assert "selected" in alpha_section
    assert "selected" not in beta_section


def test_leaderboard_bot_name_links_to_bot_detail(client, engine):
    db_insert_bot(engine, "AlphaBot")

    resp = client.get("/leaderboard")
    assert "/bots/AlphaBot" in resp.text


def test_leaderboard_shows_only_latest_version_per_family(client, engine):
    """When MyBot has V1 and V2, only V2 appears as a leaderboard row."""
    db_insert_bot(engine, "MyBot", submitted_at="2024-01-01 10:00:00")
    # Manually insert a V2 since the helper assumes v1.
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(
            text(
                """INSERT INTO bots
                   (base_name, versioned_name, version, owner_token,
                    python_version, submitted_at)
                   VALUES ('MyBot', 'MyBotV2', 2, 'tok',
                           '3', '2024-01-02 10:00:00')"""
            )
        )

    resp = client.get("/leaderboard")
    # MyBotV2 appears as the bot name; MyBot (V1) is not its own row.
    assert ">MyBotV2<" in resp.text
    assert "<a href=\"/bots/MyBot\">MyBotV2</a>" in resp.text


def test_leaderboard_shows_lifetime_column(client, engine):
    a = db_insert_bot(engine, "AlphaBot")
    b = db_insert_bot(engine, "BetaBot")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    assert "Lifetime" in resp.text
    # AlphaBot's lifetime row should show 1-0; BetaBot's should show 0-1.
    assert "1&ndash;0" in resp.text
    assert "0&ndash;1" in resp.text


# ---------------------------------------------------------------------------
# Match detail
# ---------------------------------------------------------------------------


def test_match_detail_404_for_unknown_id(client):
    resp = client.get("/matches/99999")
    assert resp.status_code == 404


def test_match_detail_returns_200(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert resp.status_code == 200


def test_match_detail_shows_both_bot_names(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA" in resp.text
    assert "BotB" in resp.text


def test_match_detail_shows_python_versions(client, engine):
    a = db_insert_bot(engine, "BotA", python_version="3.11")
    b = db_insert_bot(engine, "BotB", python_version="3.12")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "Python 3.11" in resp.text
    assert "Python 3.12" in resp.text


def test_matches_list_shows_python_versions(client, engine):
    a = db_insert_bot(engine, "BotA", python_version="3.11")
    b = db_insert_bot(engine, "BotB", python_version="3.12")
    db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert "py3.11" in resp.text
    assert "py3.12" in resp.text


def test_match_detail_shows_result(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA won" in resp.text


def test_match_detail_shows_moves_in_order(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    assert text.index("Move 1") < text.index("Move 2")


def test_match_detail_shows_which_bot_made_each_move(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="x_wins")
    db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(engine, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    move1_section = text[text.index("Move 1") : text.index("Move 2")]
    assert "BotA" in move1_section
    move2_section = text[text.index("Move 2") :]
    assert "BotB" in move2_section


def test_match_detail_shows_error_for_forfeit_move(client, engine):
    a = db_insert_bot(engine, "GoodBot")
    b = db_insert_bot(engine, "CrashBot")
    match_id = db_insert_match(engine, a, b, winner_id=a, result="o_forfeit")
    db_insert_move(engine, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(
        engine, match_id, 2, b, BOARD_AFTER_X, error="invalid output: empty response"
    )

    resp = client.get(f"/matches/{match_id}")
    assert "invalid output: empty response" in resp.text


def test_match_detail_no_moves_shows_empty_state(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    assert "No moves recorded" in resp.text


def test_match_detail_back_link_present(client, engine):
    a = db_insert_bot(engine, "BotA")
    b = db_insert_bot(engine, "BotB")
    match_id = db_insert_match(engine, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    assert "/matches" in resp.text


# ---------------------------------------------------------------------------
# Bot family detail (/bots/{base_name})
# ---------------------------------------------------------------------------


def _insert_versioned(engine, base_name, version, submitted_at):
    """Insert a specific version of a bot family."""
    from sqlalchemy import text
    versioned = base_name if version == 1 else f"{base_name}V{version}"
    sql = text(
        """INSERT INTO bots
           (base_name, versioned_name, version, owner_token,
            python_version, submitted_at)
           VALUES (:b, :v, :ver, :t, '3', CAST(:sa AS timestamp))
           RETURNING id"""
    )
    with engine.begin() as conn:
        result = conn.execute(
            sql,
            {
                "b": base_name,
                "v": versioned,
                "ver": version,
                "t": f"tok-{version}",
                "sa": submitted_at,
            },
        )
        return result.scalar_one()


def test_bot_family_404_for_unknown_base_name(client):
    resp = client.get("/bots/NoSuchBot")
    assert resp.status_code == 404


def test_bot_family_lists_all_versions_latest_first(client, engine):
    _insert_versioned(engine, "MyBot", 1, "2024-01-01 10:00:00")
    _insert_versioned(engine, "MyBot", 2, "2024-01-02 10:00:00")
    _insert_versioned(engine, "MyBot", 3, "2024-01-03 10:00:00")

    resp = client.get("/bots/MyBot")
    assert resp.status_code == 200
    body = resp.text
    # Each version has its own <h3> section header.
    assert "<h3>MyBotV3</h3>" in body
    assert "<h3>MyBotV2</h3>" in body
    assert "<h3>MyBot</h3>" in body
    v3 = body.index("<h3>MyBotV3</h3>")
    v2 = body.index("<h3>MyBotV2</h3>")
    v1 = body.index("<h3>MyBot</h3>")
    assert v3 < v2 < v1


def test_bot_family_groups_matches_under_each_version(client, engine):
    v1 = _insert_versioned(engine, "MyBot", 1, "2024-01-01 10:00:00")
    v2 = _insert_versioned(engine, "MyBot", 2, "2024-01-02 10:00:00")
    other = db_insert_bot(engine, "OtherBot")

    # V1 plays Other (V1 wins); V2 plays Other (Other wins).
    db_insert_match(engine, v1, other, winner_id=v1, result="x_wins",
                    played_at="2024-01-05 10:00:00")
    db_insert_match(engine, v2, other, winner_id=other, result="o_wins",
                    played_at="2024-01-06 10:00:00")

    resp = client.get("/bots/MyBot")
    body = resp.text

    # Both result labels appear and the V2 section precedes the V1 section.
    assert "OtherBot won" in body  # V2's loss
    assert "MyBot won" in body  # V1's win — result label uses the X-side bot name
    assert body.index("<h3>MyBotV2</h3>") < body.index("<h3>MyBot</h3>")


def test_bot_family_shows_empty_state_for_version_with_no_matches(client, engine):
    _insert_versioned(engine, "Lonely", 1, "2024-01-01 10:00:00")
    resp = client.get("/bots/Lonely")
    assert "No matches yet" in resp.text


def test_bot_family_intra_family_match_appears_under_both_versions(client, engine):
    """A match between V1 and V2 of the same family is shown under each
    version's section so the row appears twice on the page."""
    v1 = _insert_versioned(engine, "MyBot", 1, "2024-01-01 10:00:00")
    v2 = _insert_versioned(engine, "MyBot", 2, "2024-01-02 10:00:00")
    db_insert_match(engine, v1, v2, winner_id=v2, result="o_wins")

    resp = client.get("/bots/MyBot")
    body = resp.text
    # The result label "MyBotV2 won" should appear exactly twice — once in
    # each version's section.
    assert body.count("MyBotV2 won") == 2


def test_matches_filter_by_base_name_includes_all_versions(client, engine):
    v1 = _insert_versioned(engine, "MyBot", 1, "2024-01-01 10:00:00")
    v2 = _insert_versioned(engine, "MyBot", 2, "2024-01-02 10:00:00")
    other = db_insert_bot(engine, "OtherBot")
    db_insert_match(engine, v1, other, winner_id=v1, result="x_wins")
    db_insert_match(engine, v2, other, winner_id=v2, result="x_wins")

    resp = client.get("/matches?bot=MyBot")
    # Both versions' matches show up in the flat list.
    assert "MyBot won" in resp.text  # V1 row
    assert "MyBotV2 won" in resp.text  # V2 row
