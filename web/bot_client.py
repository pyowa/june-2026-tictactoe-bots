"""HTTP+k8s client the play page uses to ask a bot pod to take a turn.

Mirrors `dispatcher/pods.py` in shape: synchronous helpers that callers
schedule on a thread-pool executor to keep them off the async event loop.
The web service has its own ServiceAccount + Role (get/list pods in the
`bots` namespace) so the in-cluster config picks up correct creds."""

import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

NAMESPACE = "bots"
TURN_PORT = 8080
DEFAULT_TURN_TIMEOUT = 10.0


class BotForfeit(Exception):
    """Raised when the bot can't or won't produce a valid move.

    `reason` is the short, user-facing phrase the play page shows after the
    `Game over: ` prefix (e.g. "Bot is unavailable", "Bot took too long",
    "Bot returned an invalid move")."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def get_core_v1() -> Any:
    """Return a kubernetes.client.CoreV1Api wired to the cluster.

    Tries in-cluster config first (web pod with the right ServiceAccount);
    falls back to local kubeconfig for ad-hoc development."""
    from kubernetes import client, config  # pragma: no cover -- needs live cluster

    try:  # pragma: no cover -- needs live cluster
        config.load_incluster_config()
    except config.config_exception.ConfigException:  # pragma: no cover
        config.load_kube_config()
    return client.CoreV1Api()  # pragma: no cover -- needs live cluster


def get_pod_ip(core_v1: Any, pod_name: str) -> str | None:
    """Return the pod's cluster IP, or None if the pod is missing/IP-less."""
    from kubernetes.client.exceptions import ApiException

    try:
        pod = core_v1.read_namespaced_pod(pod_name, NAMESPACE)
    except ApiException:
        return None
    return pod.status.pod_ip or None


def _parse_board_strict(text: str) -> list[list[str]] | None:
    """Parse a board string into a 3x3 grid, returning None if malformed."""
    rows = text.strip().splitlines()
    if len(rows) != 3:
        return None
    board: list[list[str]] = []
    for row in rows:
        cells = row.split("|")
        if len(cells) != 3 or not all(c in ("X", "O", ".") for c in cells):
            return None
        board.append(cells)
    return board


def _validate_one_symbol_placed(
    old: list[list[str]], new: list[list[str]], symbol: str
) -> bool:
    """Confirm the only change between `old` and `new` is one `.` → `symbol`."""
    changes = [
        (r, c, old[r][c], new[r][c])
        for r in range(3)
        for c in range(3)
        if old[r][c] != new[r][c]
    ]
    if len(changes) != 1:
        return False
    _, _, prev, after = changes[0]
    return prev == "." and after == symbol


def request_bot_turn(
    pod_ip: str,
    symbol: str,
    board: str,
    *,
    timeout: float = DEFAULT_TURN_TIMEOUT,
) -> str:
    """POST a turn request to the bot pod and return its new board string.

    Raises `BotForfeit` with a user-facing `reason` on any failure mode:
    timeout, network error, unparseable response, or illegal move."""
    url = f"http://{pod_ip}:{TURN_PORT}/turn"
    body = json.dumps({"symbol": symbol, "board": board}).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- internal cluster URL
            data = resp.read()
    except TimeoutError as err:
        raise BotForfeit("Bot took too long") from err
    except URLError as err:
        raise BotForfeit("Bot is unavailable") from err

    try:
        payload = json.loads(data)
    except json.JSONDecodeError as err:
        raise BotForfeit("Bot returned an invalid move") from err

    new_board_str = payload.get("board")
    if not isinstance(new_board_str, str):
        raise BotForfeit("Bot returned an invalid move")

    old = _parse_board_strict(board)
    new = _parse_board_strict(new_board_str)
    if old is None or new is None:
        raise BotForfeit("Bot returned an invalid move")
    if not _validate_one_symbol_placed(old, new, symbol):
        raise BotForfeit("Bot returned an invalid move")
    return new_board_str
