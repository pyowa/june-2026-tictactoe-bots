#!/usr/bin/env python3
"""
Drop and recreate the database, AND purge all RabbitMQ queues, so the next
run starts from a clean slate (no stale match jobs from old DB IDs lingering
on the broker).

Run with: uv run poe reset-db  (or `python -m scripts.reset_db`)
"""

import asyncio
import base64
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import db.session

# Importing the entity models registers them on `Base.metadata` so the
# drop_all call below knows what tables exist.
import entities.bot.model  # noqa: F401
import entities.match.model  # noqa: F401
import entities.move.model  # noqa: F401
from db.base import Base

RABBITMQ_MGMT_URL = os.environ.get("RABBITMQ_MGMT_URL", "http://localhost:15672")
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")


def _list_queues(auth_header: str) -> list[dict] | None:
    """HTTP GET /api/queues; returns None if unreachable, list otherwise."""
    try:
        req = urllib.request.Request(
            f"{RABBITMQ_MGMT_URL}/api/queues",
            headers={"Authorization": auth_header},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.load(resp)  # type: ignore[no-any-return]
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  Skipping queue purge: RabbitMQ management API unreachable ({exc}).")
        return None


def _purge_queue(name: str, vhost: str, auth_header: str) -> bool:
    """HTTP DELETE /api/queues/{vhost}/{name}/contents.

    Returns True if purged, False on 404 or other HTTP error."""
    encoded_vhost = urllib.parse.quote(vhost, safe="")
    encoded_name = urllib.parse.quote(name, safe="")
    url = f"{RABBITMQ_MGMT_URL}/api/queues/{encoded_vhost}/{encoded_name}/contents"
    req = urllib.request.Request(
        url, method="DELETE", headers={"Authorization": auth_header}
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            print(f"  Failed to purge {name}: {exc}")
        return False


def purge_rabbitmq_queues() -> None:
    """Empty every queue on the default vhost via the management HTTP API.

    Stale messages on `matches.build`, `matches.ondeck`, etc. referencing
    dropped DB rows are the most common source of confusion after a reset,
    so this is bundled in."""
    auth = base64.b64encode(f"{RABBITMQ_USER}:{RABBITMQ_PASS}".encode()).decode()
    auth_header = f"Basic {auth}"

    queues = _list_queues(auth_header)
    if queues is None:
        return

    purged = 0
    for q in queues:
        name = q["name"]
        # Auto-generated reply queues (amq.gen-... / amq_...) are exclusive
        # to their owning connection and can't be touched from the outside.
        # They'll be auto-deleted when that connection dies — skip them.
        if name.startswith("amq"):
            continue
        if _purge_queue(name, q["vhost"], auth_header):
            print(f"  Purged queue: {name} ({q.get('messages', 0)} messages)")
            purged += 1
    print(f"  Purged {purged} queue(s).")


async def _drop_all_tables() -> None:
    """Drop the ORM tables and Alembic's version table via async engine.

    Build a fresh async engine here (instead of using `db.session`'s shared
    one) so this script doesn't depend on the module-level engine being in
    any particular state. Reads `db.session.DATABASE_URL` at call time so
    tests that call `db.session.reconfigure(...)` take effect here."""
    engine = create_async_engine(db.session.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        # Raw SQL: dropping alembic's bookkeeping table isn't expressed via
        # `Base.metadata` (it's owned by alembic, not the ORM).
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


def delete_bot_pods() -> None:
    """Delete all bot pods from the k8s bots namespace.

    Skips gracefully when kubectl is not on PATH or no cluster is reachable."""
    try:
        subprocess.run(
            ["kubectl", "delete", "pods", "--all", "-n", "bots"],
            check=True,
            capture_output=True,
        )
        print("  Deleted bot pods from k8s bots namespace.")
    except FileNotFoundError:
        print("  Skipping pod deletion: kubectl not found.")
    except subprocess.CalledProcessError:
        print("  Skipping pod deletion: No k8s cluster reachable.")


async def main() -> None:
    print(f"Dropping all tables in {db.session.DATABASE_URL}...")
    await _drop_all_tables()

    print("Running migrations...")
    # `alembic` is its own CLI with sync transport. Stays a subprocess.
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    print(f"Purging RabbitMQ queues at {RABBITMQ_MGMT_URL}...")
    purge_rabbitmq_queues()

    print("Deleting bot pods from k8s...")
    delete_bot_pods()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
