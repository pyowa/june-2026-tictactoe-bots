"""Server-side allowlist of supported bot runtimes.

Adding a new language is: append one entry here + publish the image.
The `language:` key in a bot's docstring must exactly match a key in RUNTIMES.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Runtime:
    image: str
    interpreter: str
    ext: str


RUNTIMES: dict[str, Runtime] = {
    "python-3.10": Runtime(
        image="pyowa/bot-runner-python:3.10", interpreter="python", ext=".py"
    ),
    "python-3.11": Runtime(
        image="pyowa/bot-runner-python:3.11", interpreter="python", ext=".py"
    ),
    "python-3.12": Runtime(
        image="pyowa/bot-runner-python:3.12", interpreter="python", ext=".py"
    ),
    "python-3.13": Runtime(
        image="pyowa/bot-runner-python:3.13", interpreter="python", ext=".py"
    ),
    "python-3.14": Runtime(
        image="pyowa/bot-runner-python:3.14", interpreter="python", ext=".py"
    ),
}

DEFAULT_RUNTIME_KEY = "python-3.14"
