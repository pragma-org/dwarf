"""Single-flight read-only topology health checks for the operator UI."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable


_LOCK = threading.Lock()
_THREAD: threading.Thread | None = None
_LAST_RESULT: dict[str, Any] | None = None
_STARTED_AT: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _public_copy(value: dict[str, Any] | None) -> dict[str, Any] | None:
    return json.loads(json.dumps(value)) if value is not None else None


def _default_probe() -> dict[str, Any]:
    from profile_manager.config import load_config
    from profile_manager.remote import ssh_command

    config = load_config()
    result = ssh_command(
        config,
        "topology-health",
        timeout=90,
        verb=("topology-health",),
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "topology health command failed").strip()
        )
    for line in reversed(result.stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("state"):
            return parsed
    raise RuntimeError("topology health command returned no JSON result")


def normalize_probe_failure(
    error: BaseException, *, previous: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "state": "unknown",
        "reason_code": "topology_probe_failed",
        "detail": str(error),
        "checked_at": _utc_now(),
        "previous": _public_copy(previous),
        "previous_is_current": False,
    }


def topology_health_snapshot() -> dict[str, Any]:
    with _LOCK:
        checking = _THREAD is not None and _THREAD.is_alive()
        previous = _public_copy(_LAST_RESULT)
        if checking:
            return {
                "state": "checking",
                "started_at": _STARTED_AT,
                "previous": previous,
                "previous_is_current": False,
            }
        if previous is None:
            return {
                "state": "idle",
                "previous": None,
                "previous_is_current": False,
            }
        return {**previous, "previous": None, "previous_is_current": True}


def topology_health_evidence() -> dict[str, Any] | None:
    """Return the last completed public observation, never an in-flight placeholder."""
    with _LOCK:
        return _public_copy(_LAST_RESULT)


def topology_redeploy_command() -> list[str]:
    """Build the one host-controlled mixed-topology mutation command."""
    from profile_manager.config import load_config
    from profile_manager.remote import render_topology_redeploy_command

    return render_topology_redeploy_command(
        load_config(), topology_id="cardano_amaru"
    )


def _run_probe(probe: Callable[[], dict[str, Any]]) -> None:
    global _LAST_RESULT
    try:
        result = probe()
        if not isinstance(result, dict) or not result.get("state"):
            raise ValueError("topology probe returned an invalid result")
        completed = _public_copy(result)
    except BaseException as error:  # noqa: BLE001 - surface probe failures as data
        with _LOCK:
            previous = _public_copy(_LAST_RESULT)
        completed = normalize_probe_failure(error, previous=previous)
    with _LOCK:
        _LAST_RESULT = completed


def request_topology_health_check(
    *, probe: Callable[[], dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Start one fresh check, or join the check already in flight."""
    global _THREAD, _STARTED_AT
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return {
                "state": "checking",
                "started_at": _STARTED_AT,
                "previous": _public_copy(_LAST_RESULT),
                "previous_is_current": False,
            }
        _STARTED_AT = _utc_now()
        _THREAD = threading.Thread(
            target=_run_probe,
            args=(probe or _default_probe,),
            name="dwarf-topology-health",
            daemon=True,
        )
        _THREAD.start()
        return {
            "state": "checking",
            "started_at": _STARTED_AT,
            "previous": _public_copy(_LAST_RESULT),
            "previous_is_current": False,
        }


def wait_for_topology_health(*, timeout: float) -> dict[str, Any]:
    with _LOCK:
        thread = _THREAD
    if thread is not None:
        thread.join(timeout=timeout)
    return topology_health_snapshot()


def reset_topology_health_state() -> None:
    """Clear process-local state. Intended for deterministic tests."""
    global _THREAD, _LAST_RESULT, _STARTED_AT
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            raise RuntimeError("cannot reset topology health while a probe is running")
        _THREAD = None
        _LAST_RESULT = None
        _STARTED_AT = None
