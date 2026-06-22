def pick_runtime_key(a: str, b: str) -> str:
    """Pick the 'higher' of two runtime keys.

    For Python runtimes (python-X.Y vs python-X.Y), picks the higher version.
    When runtimes differ in language family, prefers `a` (the new bot's key).
    """

    def python_tuple(key: str) -> tuple[int, ...]:
        if key.startswith("python-"):
            try:
                return tuple(int(x) for x in key[len("python-") :].split("."))
            except ValueError:
                return ()
        return ()

    ta, tb = python_tuple(a), python_tuple(b)
    if ta and tb:
        return a if ta >= tb else b  # pragma: no mutate -- >= vs > equivalent
    return a


def pick_python_version(a: str, b: str) -> str:
    """Pick the higher of two Python version strings (e.g. '3.11', '3.12').

    Used when bot X declares one version and bot O declares another; both
    bots run in the higher version (older code should still run on newer
    Python). Falls back to lexicographic comparison if parsing fails."""

    def parse(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            return ()

    return max(a, b, key=parse)


def turn_queue_for(python_version: str) -> str:
    """Map a Python version (e.g. '3', '3.11') to its RabbitMQ queue name.

    `3.11` → `turn.py311.requests` — dots are stripped so the queue name
    is dot-free (RabbitMQ allows dots but they're routing-key delimiters
    elsewhere; keeping the queue name compact avoids subtle bugs)."""
    compact = python_version.replace(".", "")
    return f"turn.py{compact}.requests"
