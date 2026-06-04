#!/usr/bin/env python3
"""
Drop and recreate the database, AND purge all RabbitMQ queues, so the next
run starts from a clean slate (no stale match jobs from old DB IDs lingering
on the broker).

Run with: uv run python scripts/reset_db.py
"""

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from sqlalchemy import text

from db.database import DATABASE_URL, create_sync_engine
from db.models import Base

RABBITMQ_MGMT_URL = "http://localhost:15672"
RABBITMQ_USER = "guest"
RABBITMQ_PASS = "guest"


def purge_rabbitmq_queues() -> None:
    """Empty every queue on the default vhost via the management HTTP API.

    Stale messages on `matches.todo` referencing dropped DB rows are the
    most common source of confusion after a reset, so this is bundled in."""
    auth = base64.b64encode(f"{RABBITMQ_USER}:{RABBITMQ_PASS}".encode()).decode()
    auth_header = f"Basic {auth}"

    try:
        req = urllib.request.Request(
            f"{RABBITMQ_MGMT_URL}/api/queues",
            headers={"Authorization": auth_header},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            queues = json.load(resp)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  Skipping queue purge: RabbitMQ management API unreachable ({exc}).")
        return

    purged = 0
    for q in queues:
        name = q["name"]
        # Auto-generated reply queues (amq.gen-... / amq_...) are exclusive
        # to their owning connection and can't be touched from the outside.
        # They'll be auto-deleted when that connection dies — skip them.
        if name.startswith("amq"):
            continue
        vhost = urllib.parse.quote(q.get("vhost", "/"), safe="")
        encoded = urllib.parse.quote(name, safe="")
        url = f"{RABBITMQ_MGMT_URL}/api/queues/{vhost}/{encoded}/contents"
        req = urllib.request.Request(
            url, method="DELETE", headers={"Authorization": auth_header}
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            print(f"  Purged queue: {name} ({q.get('messages', 0)} messages)")
            purged += 1
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                print(f"  Failed to purge {name}: {exc}")
    print(f"  Purged {purged} queue(s).")


def main() -> None:
    print(f"Dropping all tables in {DATABASE_URL}...")
    engine = create_sync_engine()
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()

    print("Running migrations...")
    subprocess.run(["alembic", "upgrade", "head"], check=True)

    print(f"Purging RabbitMQ queues at {RABBITMQ_MGMT_URL}...")
    purge_rabbitmq_queues()

    print("Done.")


if __name__ == "__main__":
    main()
