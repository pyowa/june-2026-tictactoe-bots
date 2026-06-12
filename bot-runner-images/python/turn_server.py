"""
Minimal HTTP turn server for bot-runner pods.

Reads SOURCE_B64 from env at startup, decodes and runs the bot as a
subprocess on POST /turn, returns the result as JSON. GET /health returns
200 OK for the readinessProbe.

No extra dependencies — stdlib http.server only.
"""

import base64
import http.server
import json
import os
import subprocess
import sys
import tempfile


def _run_bot(source: bytes, symbol: str, board: str) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
        f.write(source)
        tmpfile_path = f.name

    try:
        proc = subprocess.run(
            [sys.executable, tmpfile_path],
            input=f"{symbol}\n{board}",
            capture_output=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout after 10s"}
    except Exception as exc:
        return {"error": f"runtime error: {exc}"}
    finally:
        os.unlink(tmpfile_path)

    stdout = proc.stdout.strip()
    if not stdout:
        stderr = proc.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return {"error": f"invalid output: empty response{detail}"}
    return {"board": stdout}


class _TurnHandler(http.server.BaseHTTPRequestHandler):
    _source: bytes = b""

    def log_message(self, format: str, *args: object) -> None:  # noqa: ANN002
        """Suppress access logs."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/turn":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
            symbol = payload["symbol"]
            board = payload["board"]
        except Exception as exc:
            response = json.dumps({"error": f"bad request: {exc}"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)
            return

        result = _run_bot(self._source, symbol, board)
        response = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response)


def main() -> None:
    source_b64 = os.environ.get("SOURCE_B64", "")
    _TurnHandler._source = base64.b64decode(source_b64) if source_b64 else b""

    server = http.server.HTTPServer(("0.0.0.0", 8080), _TurnHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
