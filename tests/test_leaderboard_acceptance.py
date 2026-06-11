from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import db_insert_bot, db_insert_match


async def test_leaderboard_acceptance(engine: AsyncEngine, client: TestClient) -> None:
    timestamp = "2026-01-02T00:00:00"
    alpha = await db_insert_bot(engine, "Alpha", submitted_at=timestamp)
    alpha_v2 = await db_insert_bot(
        engine, "Alpha", versioned_name="AlphaV2", version=2, submitted_at=timestamp
    )
    beta = await db_insert_bot(engine, "Beta", submitted_at=timestamp)
    gamma = await db_insert_bot(engine, "Gamma", submitted_at=timestamp)
    delta = await db_insert_bot(engine, "Delta", submitted_at=timestamp)
    epsilon = await db_insert_bot(engine, "Epsilon", submitted_at=timestamp)
    zeta = await db_insert_bot(engine, "Zeta", submitted_at=timestamp)

    await db_insert_match(engine, alpha, beta, alpha, "x_wins")
    await db_insert_match(engine, beta, alpha, alpha, "o_wins")

    await db_insert_match(engine, alpha, gamma, gamma, "o_wins")
    await db_insert_match(engine, gamma, alpha, gamma, "x_wins")

    await db_insert_match(engine, alpha, epsilon, epsilon, "x_forfeit")
    await db_insert_match(engine, epsilon, alpha, epsilon, "o_forfeit")

    await db_insert_match(engine, alpha, zeta, alpha, "o_forfeit")
    await db_insert_match(engine, zeta, alpha, alpha, "x_forfeit")

    await db_insert_match(engine, alpha_v2, beta, alpha_v2, "x_wins")
    await db_insert_match(engine, beta, alpha_v2, alpha_v2, "o_wins")

    await db_insert_match(engine, alpha_v2, gamma, gamma, "o_wins")
    await db_insert_match(engine, gamma, alpha_v2, gamma, "x_wins")

    await db_insert_match(engine, alpha_v2, delta, None, "cat")
    await db_insert_match(engine, delta, alpha_v2, None, "cat")

    await db_insert_match(engine, alpha_v2, epsilon, epsilon, "x_forfeit")
    await db_insert_match(engine, epsilon, alpha_v2, epsilon, "o_forfeit")

    await db_insert_match(engine, alpha_v2, zeta, alpha_v2, "o_forfeit")
    await db_insert_match(engine, zeta, alpha_v2, alpha_v2, "x_forfeit")

    response = client.get("/leaderboard")

    assert response.status_code == 200

    soup = BeautifulSoup(response.text, "html.parser")
    alpha_v2_row = soup.find("tr", {"data-bot": "AlphaV2"})
    assert alpha_v2_row is not None
    alpha_v2_cells = alpha_v2_row.find_all("td")

    assert alpha_v2_cells[2].text == "2"
    assert alpha_v2_cells[3].text == "2"
    assert alpha_v2_cells[4].text == "2"
    assert alpha_v2_cells[5].text == "4"
    assert alpha_v2_cells[6].text == "8-8"
