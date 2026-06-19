"""
Pure k8s Pod lifecycle helpers for warm bot-runner pods.

All functions are synchronous — they're called from a thread-pool executor
to avoid blocking the async event loop.
"""

import json
import time
from typing import Any
from urllib.request import Request, urlopen

NAMESPACE = "bots"
TURN_PORT = 8080
_POLL_INTERVAL = 0.5


def pod_name(bot_id: int) -> str:
    """Return the Kubernetes pod name for a given bot ID."""
    return f"bot-{bot_id}"



def build_bot_pod_manifest(
    pod_name: str,
    image: str,
    source_b64: str,
    bot_id: int,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": NAMESPACE,
            "labels": {
                "app": "bot-runner",
                "bot-id": str(bot_id),
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [
                {
                    "name": "bot",
                    "image": image,
                    "imagePullPolicy": "Never",
                    "env": [
                        {"name": "SOURCE_B64", "value": source_b64},
                    ],
                    "resources": {
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                        "requests": {"cpu": "100m", "memory": "64Mi"},
                    },
                    "ports": [
                        {"containerPort": TURN_PORT},
                    ],
                }
            ],
        },
    }


def wait_for_http_ready(
    pod_ip: str,
    *,
    timeout: float = 60.0,
) -> None:
    """Poll GET /health on the pod until it responds successfully.

    Raises TimeoutError after `timeout` seconds if never successful.
    Kubelet readiness probes are blocked by NetworkPolicy, so we poll ourselves.
    """
    deadline = time.monotonic() + timeout
    url = f"http://{pod_ip}:{TURN_PORT}/health"
    while time.monotonic() < deadline:
        try:
            urlopen(url)  # noqa: S310 — internal cluster URL, not user input
            return
        except Exception:  # noqa: BLE001 — expected while pod is starting up
            time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"pod at {pod_ip} not ready after {timeout}s")


def _check_pod_phase(pod: Any, pod_name: str) -> bool:
    """Inspect *pod* status and return True if the pod is ready to serve traffic.

    Raises RuntimeError immediately if the pod entered a terminal failure phase.
    Returns False while the pod is still starting up (caller should keep polling).
    """
    phase = pod.status.phase
    if phase in ("Failed", "Unknown"):
        raise RuntimeError(f"pod {pod_name} entered phase {phase!r}")
    container_statuses = pod.status.container_statuses
    return bool(
        phase == "Running" and container_statuses and container_statuses[0].ready
    )



def wait_for_pod_ready(
    core_v1: Any,
    pod_name: str,
    *,
    timeout: float = 60.0,
) -> None:
    """Poll until the pod is Running and its container is ready.

    Raises RuntimeError if the pod enters a terminal failure phase.
    Raises TimeoutError if the pod isn't ready within `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pod = core_v1.read_namespaced_pod(pod_name, NAMESPACE)
        if _check_pod_phase(pod, pod_name):
            return
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"pod {pod_name} not ready after {timeout}s")


def get_pod_ip(core_v1: Any, pod_name: str) -> str:
    """Return the pod's cluster IP. Raises RuntimeError if the IP is empty."""
    pod = core_v1.read_namespaced_pod(pod_name, NAMESPACE)
    ip = pod.status.pod_ip
    if not ip:
        raise RuntimeError(f"pod {pod_name} has no IP yet")
    return ip


def request_turn(
    pod_ip: str,
    symbol: str,
    board: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST a turn request to the pod's HTTP server and return the parsed JSON."""
    url = f"http://{pod_ip}:{TURN_PORT}/turn"
    body = json.dumps({"symbol": symbol, "board": board}).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def delete_pod(core_v1: Any, pod_name: str) -> None:
    """Delete the named pod."""
    core_v1.delete_namespaced_pod(pod_name, NAMESPACE)
