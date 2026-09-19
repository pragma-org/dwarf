"""Single-flight read-only topology health checks for the operator UI."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
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


def _result_path() -> Path:
    state_dir = Path(os.environ.get("ADA2_DWARF_STATE_DIR") or "/var/dwarf/state")
    return state_dir / "topology-health" / "dashboard-latest.json"


def _write_persisted_result(value: dict[str, Any]) -> bool:
    path = _result_path()
    temporary = path.with_suffix(".json.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return True


def _read_persisted_result() -> dict[str, Any] | None:
    try:
        value = json.loads(_result_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not value.get("state"):
        return None
    return value


def _age_seconds(value: dict[str, Any]) -> int | None:
    stamp = value.get("checked_at") or value.get("completed_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - observed).total_seconds()))


def _present_completed(
    value: dict[str, Any], *, observation_source: str
) -> dict[str, Any]:
    presented = _public_copy(value) or {}
    presented.update(
        {
            "observation_source": observation_source,
            "cached": observation_source == "persisted",
            "age_seconds": _age_seconds(value),
            "previous": None,
            "previous_is_current": True,
        }
    )
    return presented


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
        memory_result = _public_copy(_LAST_RESULT)
        persisted_result = None if memory_result is not None else _read_persisted_result()
        previous = memory_result or persisted_result
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
        return _present_completed(
            previous,
            observation_source="memory" if memory_result is not None else "persisted",
        )


def topology_health_evidence() -> dict[str, Any] | None:
    """Return the last completed public observation, never an in-flight placeholder."""
    with _LOCK:
        return _public_copy(_LAST_RESULT) or _public_copy(_read_persisted_result())


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
            previous = _public_copy(_LAST_RESULT) or _read_persisted_result()
        completed = normalize_probe_failure(error, previous=previous)
    _write_persisted_result(completed)
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
                "previous": _public_copy(_LAST_RESULT) or _read_persisted_result(),
                "previous_is_current": False,
            }
        previous = _public_copy(_LAST_RESULT) or _read_persisted_result()
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
            "previous": previous,
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
