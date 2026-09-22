"""Single-flight read-only topology health checks for the operator UI."""
from __future__ import annotations

import json
import os
import threading
import time
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


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_current_profile_health(
    first: dict[str, Any], second: dict[str, Any] | None
) -> dict[str, Any]:
    """Classify two exact-profile observations without masking failures."""
    base = {
        "checked_at": _utc_now(), "topology_kind": "managed_profile",
        "profile_id": first.get("profile_id"), "redeploy_supported": False,
        "evidence_scope": "current_active_profile",
    }
    topology_result = first.get("topology_result")
    if isinstance(topology_result, dict) and topology_result.get("state"):
        return {
            **topology_result,
            **base,
            "observations": [first],
        }
    if not first.get("enabled"):
        state = first.get("state") or "unknown"
        reason = "no_active_profile" if state == "no_active" else "active_profile_probe_failed"
        return {
            **base, "state": state, "reason_code": reason,
            "detail": first.get("error") or "The active profile could not be observed.",
            "observations": [first],
        }
    if second is None or not second.get("enabled"):
        return {
            **base, "state": "unknown", "reason_code": "active_profile_second_sample_failed",
            "detail": (second or {}).get("error") or "The second progress sample is unavailable.",
            "observations": [first] + ([second] if second else []),
        }
    if second.get("profile_id") != first.get("profile_id"):
        return {
            **base, "state": "unknown", "reason_code": "active_profile_changed",
            "detail": "The active profile changed between health samples.", "observations": [first, second],
        }
    first_health = first.get("health") or {}
    second_health = second.get("health") or {}
    first_parsed = first_health.get("parsed") or {}
    parsed = second_health.get("parsed") or {}
    expected = _integer(second.get("expected_nodes"))
    processes = _integer(parsed.get("cardano_node_processes"))
    sockets = _integer(parsed.get("socket_count"))
    listeners = _integer(parsed.get("listener_count"))
    first_slot = _integer(first_parsed.get("tip_slot"))
    second_slot = _integer(parsed.get("tip_slot"))
    try:
        sync = float(parsed.get("sync_progress"))
    except (TypeError, ValueError):
        sync = None
    observation = {
        "expected_nodes": expected, "node_processes": processes,
        "socket_count": sockets, "listener_count": listeners,
        "loopback_only": parsed.get("loopback_only"),
        "first_tip_slot": first_slot, "tip_slot": second_slot,
        "tip_block": parsed.get("tip_block"), "sync_progress": parsed.get("sync_progress"),
    }
    common = {**base, "observation": observation, "observations": [first, second]}
    if first_health.get("returncode") != 0 or second_health.get("returncode") != 0:
        return {**common, "state": "unknown", "reason_code": "active_profile_probe_failed",
                "detail": second_health.get("stderr") or "The profile health command failed."}
    if expected is None or processes != expected or sockets != expected or listeners != expected:
        return {**common, "state": "unhealthy", "reason_code": "active_profile_readiness_failed",
                "detail": "Process, socket, or listener counts do not match the active profile."}
    if parsed.get("loopback_only") != "true":
        return {**common, "state": "unhealthy", "reason_code": "active_profile_exposure_failed",
                "detail": "The active profile is not restricted to loopback listeners."}
    if first_slot is None or second_slot is None or second_slot <= first_slot:
        return {**common, "state": "unhealthy", "reason_code": "active_profile_not_progressing",
                "detail": "The active profile tip did not advance between bounded samples."}
    if sync is None or sync < 99.0:
        return {**common, "state": "catching_up", "reason_code": "active_profile_catching_up",
                "detail": "The active profile is progressing but is not yet synchronized."}
    return {**common, "state": "healthy", "reason_code": "active_profile_ready_and_progressing",
            "detail": "The exact active profile passed readiness and bounded progress checks."}


def _default_probe() -> dict[str, Any]:
    from profile_manager.data.health import _live_health

    first = _live_health(None)
    if not first.get("enabled"):
        return classify_current_profile_health(first, None)
    time.sleep(10)
    second = _live_health(first.get("profile_id"))
    return classify_current_profile_health(first, second)


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
