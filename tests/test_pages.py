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


def test_leaderboard_orders_by_wins_descending(client, db_path):
    a = db_insert_bot(db_path, "LowBot")
    b = db_insert_bot(db_path, "HighBot")
    db_insert_match(db_path, a, b, winner_id=b, result="o_wins")
    db_insert_match(db_path, a, b, winner_id=b, result="o_wins")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    # HighBot (2 wins) must appear before LowBot (1 win)
    assert text.index("HighBot") < text.index("LowBot")


def test_leaderboard_tie_broken_by_earlier_submission(client, db_path):
    early = db_insert_bot(db_path, "EarlyBot", submitted_at="2024-01-01 00:00:00")
    late = db_insert_bot(db_path, "LateBot", submitted_at="2024-06-01 00:00:00")
    # Give each one win so they're tied
    db_insert_match(db_path, early, late, winner_id=early, result="x_wins")
    db_insert_match(db_path, late, early, winner_id=late, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("EarlyBot") < text.index("LateBot")


def test_leaderboard_clean_win_count_is_correct(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get("/leaderboard")
    text = resp.text
    bot_a_pos = text.index("BotA")
    row_section = text[bot_a_pos : bot_a_pos + 300]
    assert ">3<" in row_section


def test_leaderboard_forfeit_win_shown_separately(client, db_path):
    a = db_insert_bot(db_path, "GoodBot")
    b = db_insert_bot(db_path, "CrashBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_match(db_path, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    text = resp.text
    bot_a_pos = text.index("GoodBot")
    row_section = text[bot_a_pos : bot_a_pos + 400]
    # 1 clean win and 1 forfeit win, each in their own cell
    assert ">1<" in row_section
    assert row_section.count(">1<") == 2


def test_leaderboard_forfeit_win_ranks_above_zero_wins(client, db_path):
    a = db_insert_bot(db_path, "GoodBot")
    b = db_insert_bot(db_path, "CrashBot")
    db_insert_match(db_path, a, b, winner_id=a, result="o_forfeit")

    resp = client.get("/leaderboard")
    text = resp.text
    assert text.index("GoodBot") < text.index("CrashBot")


def test_leaderboard_draw_not_counted_as_win(client, db_path):
    a = db_insert_bot(db_path, "DrawBot")
    b = db_insert_bot(db_path, "OtherBot")
    db_insert_match(db_path, a, b, winner_id=None, result="cat")

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


def test_matches_shows_both_bot_names(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

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
def test_matches_result_label(client, db_path, result, expected):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    winner_id = a if result == "x_wins" else b if result == "o_wins" else None
    if result in ("x_forfeit",):
        winner_id = b
    if result in ("o_forfeit",):
        winner_id = a
    db_insert_match(db_path, a, b, winner_id=winner_id, result=result)

    resp = client.get("/matches")
    assert expected in resp.text


def test_matches_most_recent_first(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    db_insert_match(
        db_path, a, b, winner_id=a, result="x_wins", played_at="2024-01-01 00:00:00"
    )
    db_insert_match(
        db_path, b, a, winner_id=b, result="x_wins", played_at="2024-06-01 00:00:00"
    )

    resp = client.get("/matches")
    # The newer match has BotB as X; the older has BotA as X.
    # BotB-as-X row should come first in the table.
    text = resp.text
    first_occurrence_a = text.index("2024-01-01")
    first_occurrence_b = text.index("2024-06-01")
    assert first_occurrence_b < first_occurrence_a


def test_matches_contains_link_to_detail(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=None, result="cat")

    resp = client.get("/matches")
    assert f"/matches/{match_id}" in resp.text


# ---------------------------------------------------------------------------
# Matches list — bot filter
# ---------------------------------------------------------------------------


def test_matches_filter_shows_only_matching_bot(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    c = db_insert_bot(db_path, "GammaBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_match(db_path, b, c, winner_id=b, result="x_wins")

    resp = client.get("/matches?bot=AlphaBot")
    # GammaBot appears only in the dropdown, not in a table cell
    assert "<td>AlphaBot " in resp.text
    assert "<td>BetaBot " in resp.text   # shared match with AlphaBot
    assert "<td>GammaBot " not in resp.text


def test_matches_filter_includes_bot_as_o(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    c = db_insert_bot(db_path, "GammaBot")
    db_insert_match(db_path, c, a, winner_id=c, result="x_wins")  # AlphaBot is O
    db_insert_match(db_path, b, c, winner_id=b, result="x_wins")  # AlphaBot uninvolved

    resp = client.get("/matches?bot=AlphaBot")
    assert "<td>GammaBot " in resp.text
    assert "<td>BetaBot " not in resp.text


def test_matches_no_filter_shows_all(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    c = db_insert_bot(db_path, "GammaBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_match(db_path, b, c, winner_id=b, result="x_wins")

    resp = client.get("/matches")
    assert "<td>AlphaBot " in resp.text
    assert "<td>BetaBot " in resp.text
    assert "<td>GammaBot " in resp.text


def test_matches_filter_unknown_bot_shows_empty(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches?bot=NoSuchBot")
    assert "No matches played yet" in resp.text


def test_matches_filter_shows_selected_bot_in_heading(client, db_path):
    a = db_insert_bot(db_path, "AlphaBot")
    b = db_insert_bot(db_path, "BetaBot")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches?bot=AlphaBot")
    assert "AlphaBot" in resp.text
    # heading should indicate filtered view
    assert "Matches" in resp.text


def test_matches_dropdown_contains_all_bots(client, db_path):
    db_insert_bot(db_path, "AlphaBot")
    db_insert_bot(db_path, "BetaBot")

    resp = client.get("/matches")
    assert 'value="AlphaBot"' in resp.text
    assert 'value="BetaBot"' in resp.text


def test_matches_dropdown_preselects_filtered_bot(client, db_path):
    db_insert_bot(db_path, "AlphaBot")
    db_insert_bot(db_path, "BetaBot")

    resp = client.get("/matches?bot=AlphaBot")
    # The AlphaBot option should carry the selected attribute; BetaBot should not
    text = resp.text
    alpha_option_pos = text.index('value="AlphaBot"')
    beta_option_pos = text.index('value="BetaBot"')
    alpha_section = text[alpha_option_pos : alpha_option_pos + 60]
    beta_section = text[beta_option_pos : beta_option_pos + 60]
    assert "selected" in alpha_section
    assert "selected" not in beta_section


def test_leaderboard_bot_name_links_to_filtered_matches(client, db_path):
    db_insert_bot(db_path, "AlphaBot")

    resp = client.get("/leaderboard")
    assert "/matches?bot=AlphaBot" in resp.text


# ---------------------------------------------------------------------------
# Match detail
# ---------------------------------------------------------------------------


def test_match_detail_404_for_unknown_id(client):
    resp = client.get("/matches/99999")
    assert resp.status_code == 404


def test_match_detail_returns_200(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert resp.status_code == 200


def test_match_detail_shows_both_bot_names(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA" in resp.text
    assert "BotB" in resp.text


def test_match_detail_shows_python_versions(client, db_path):
    a = db_insert_bot(db_path, "BotA", python_version="3.11")
    b = db_insert_bot(db_path, "BotB", python_version="3.12")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "Python 3.11" in resp.text
    assert "Python 3.12" in resp.text


def test_matches_list_shows_python_versions(client, db_path):
    a = db_insert_bot(db_path, "BotA", python_version="3.11")
    b = db_insert_bot(db_path, "BotB", python_version="3.12")
    db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get("/matches")
    assert "py3.11" in resp.text
    assert "py3.12" in resp.text


def test_match_detail_shows_result(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")

    resp = client.get(f"/matches/{match_id}")
    assert "BotA won" in resp.text


def test_match_detail_shows_moves_in_order(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_move(db_path, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(db_path, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    assert text.index("Move 1") < text.index("Move 2")


def test_match_detail_shows_which_bot_made_each_move(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="x_wins")
    db_insert_move(db_path, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(db_path, match_id, 2, b, BOARD_AFTER_O)

    resp = client.get(f"/matches/{match_id}")
    text = resp.text
    move1_section = text[text.index("Move 1") : text.index("Move 2")]
    assert "BotA" in move1_section
    move2_section = text[text.index("Move 2") :]
    assert "BotB" in move2_section


def test_match_detail_shows_error_for_forfeit_move(client, db_path):
    a = db_insert_bot(db_path, "GoodBot")
    b = db_insert_bot(db_path, "CrashBot")
    match_id = db_insert_match(db_path, a, b, winner_id=a, result="o_forfeit")
    db_insert_move(db_path, match_id, 1, a, BOARD_AFTER_X)
    db_insert_move(
        db_path, match_id, 2, b, BOARD_AFTER_X, error="invalid output: empty response"
    )

    resp = client.get(f"/matches/{match_id}")
    assert "invalid output: empty response" in resp.text


def test_match_detail_no_moves_shows_empty_state(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    assert "No moves recorded" in resp.text


def test_match_detail_back_link_present(client, db_path):
    a = db_insert_bot(db_path, "BotA")
    b = db_insert_bot(db_path, "BotB")
    match_id = db_insert_match(db_path, a, b, winner_id=None, result="cat")

    resp = client.get(f"/matches/{match_id}")
    assert "/matches" in resp.text
