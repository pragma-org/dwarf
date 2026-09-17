"""Pure health classification for the pre-staged Cardano/Amaru topology.

Collection is deliberately separate from classification.  The host-side probe
records raw Docker, cardano-cli, and Amaru-log observations; this module turns
that evidence into a fail-closed readiness state without invoking a command.
"""
from __future__ import annotations

import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CARDANO_REFERENCE_NODES = ("p1", "p2", "p3", "relay1", "relay2")
AMARU_RELAYS = ("amaru-relay-1", "amaru-relay-2")
AMARU_CONSUMER = "amaru-consumer"


class TopologyLockBusy(RuntimeError):
    """Raised when a topology operation conflicts with an active operation."""


class TopologyLock:
    """Small closeable wrapper around a process-safe advisory file lock."""

    def __init__(self, stream: Any):
        self._stream = stream

    def close(self) -> None:
        if self._stream is None:
            return
        try:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
        finally:
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "TopologyLock":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def acquire_topology_lock(state_dir: Path, *, exclusive: bool) -> TopologyLock:
    """Acquire the fixed mixed-topology lock without waiting.

    Attached scenarios hold a shared lock. A redeploy requires the exclusive
    lock, so it cannot destroy the topology while a scenario is using it.
    """
    lock_dir = Path(state_dir) / "topology-health"
    lock_dir.mkdir(parents=True, exist_ok=True)
    stream = (lock_dir / "cardano_amaru.lock").open("a+", encoding="utf-8")
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(stream.fileno(), operation | fcntl.LOCK_NB)
    except BlockingIOError as error:
        stream.close()
        raise TopologyLockBusy("cardano_amaru topology is in use") from error
    return TopologyLock(stream)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _answer(
    observation: dict[str, Any],
    *,
    state: str,
    reason_code: str,
    affected: list[str] | None = None,
    reference_tip: dict[str, Any] | None = None,
    consumer_lag_slots: int | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "topology_id": observation.get("topology_id") or "unknown",
        "checked_at": observation.get("completed_at") or _utc_now(),
        "state": state,
        "reason_code": reason_code,
        "detail": detail,
        "affected": sorted(affected or []),
        "reference_tip": reference_tip,
        "consumer_lag_slots": consumer_lag_slots,
        "sample_count": len(observation.get("samples") or []),
    }


def _container_gate(observation: dict[str, Any]) -> tuple[str, list[str]] | None:
    containers = observation.get("containers") or {}
    required = observation.get("required_services") or []

    missing = [
        name
        for name in required
        if name not in containers or not bool((containers.get(name) or {}).get("present"))
    ]
    if missing:
        return "required_container_missing", missing

    stopped = [
        name for name in required if not bool((containers.get(name) or {}).get("running"))
    ]
    if stopped:
        return "required_container_not_running", stopped

    oom = [
        name for name in required if bool((containers.get(name) or {}).get("oom_killed"))
    ]
    if oom:
        return "container_oom_killed", oom

    restarted = [
        name
        for name in required
        if int((containers.get(name) or {}).get("restart_count") or 0) != 0
    ]
    if restarted:
        return "container_restarted", restarted

    fatal = [
        name
        for name in required
        if list((containers.get(name) or {}).get("fatal_signatures") or [])
    ]
    if fatal:
        return "amaru_fatal_signal", fatal
    return None


def _tip_slot(tips: dict[str, Any], name: str) -> int | None:
    return _integer((tips.get(name) or {}).get("slot"))


def classify_topology_health(observation: dict[str, Any]) -> dict[str, Any]:
    """Classify a raw two-or-more-sample mixed-topology observation.

    ``healthy`` is intentionally difficult to obtain: all required containers,
    observations, image identities, peer contracts, convergence, and Amaru-side
    progress evidence must be present.  Missing evidence returns ``unknown``;
    it never silently becomes a pass.
    """
    if not isinstance(observation, dict):
        observation = {}

    container_failure = _container_gate(observation)
    if container_failure:
        reason, affected = container_failure
        return _answer(
            observation, state="unhealthy", reason_code=reason, affected=affected
        )

    containers = observation.get("containers") or {}
    required = observation.get("required_services") or []
    missing_images = [
        name
        for name in required
        if not (containers.get(name) or {}).get("image")
        or not (containers.get(name) or {}).get("image_digest")
    ]
    if missing_images:
        return _answer(
            observation,
            state="unknown",
            reason_code="incomplete_image_identity",
            affected=missing_images,
        )

    peer_contract = observation.get("peer_contract") or {}
    peer_contract_ok = peer_contract.get("amaru_consumer_only_amaru_upstreams")
    if peer_contract_ok is False:
        return _answer(
            observation,
            state="unhealthy",
            reason_code="consumer_peer_contract_invalid",
            affected=[AMARU_CONSUMER],
        )
    if peer_contract_ok is not True:
        return _answer(
            observation,
            state="unknown",
            reason_code="consumer_peer_contract_unknown",
            affected=[AMARU_CONSUMER],
        )

    samples = observation.get("samples") or []
    if len(samples) < 2:
        return _answer(
            observation, state="unknown", reason_code="insufficient_samples"
        )

    first, final = samples[0], samples[-1]
    first_tips = first.get("tips") or {}
    final_tips = final.get("tips") or {}
    missing_tips = [
        name
        for name in (*CARDANO_REFERENCE_NODES, AMARU_CONSUMER)
        if _tip_slot(first_tips, name) is None or _tip_slot(final_tips, name) is None
    ]
    if missing_tips:
        return _answer(
            observation,
            state="unknown",
            reason_code="insufficient_cardano_tip_visibility",
            affected=missing_tips,
        )

    final_reference = {
        name: final_tips[name] for name in CARDANO_REFERENCE_NODES
    }
    producer_slots = {
        name: _tip_slot(final_tips, name) for name in CARDANO_REFERENCE_NODES
    }
    tolerance = observation.get("tolerances") or {}
    producer_spread_limit = int(tolerance.get("producer_slot_spread", 6))
    slot_lag_limit = int(tolerance.get("slot_lag", 6))
    slot_values = [int(slot) for slot in producer_slots.values() if slot is not None]
    if max(slot_values) - min(slot_values) > producer_spread_limit:
        return _answer(
            observation,
            state="unhealthy",
            reason_code="cardano_reference_divergent",
            affected=[
                name
                for name, slot in producer_slots.items()
                if slot != max(slot_values)
            ],
        )

    by_slot: dict[int, set[str]] = {}
    for tip in final_reference.values():
        slot = int(tip["slot"])
        hash_value = tip.get("hash")
        if hash_value:
            by_slot.setdefault(slot, set()).add(str(hash_value))
    if any(len(hashes) > 1 for hashes in by_slot.values()):
        return _answer(
            observation,
            state="unhealthy",
            reason_code="cardano_reference_divergent",
            affected=list(CARDANO_REFERENCE_NODES),
        )

    reference_name = max(
        CARDANO_REFERENCE_NODES, key=lambda name: int(producer_slots[name])
    )
    reference_tip = dict(final_tips[reference_name])
    reference_slot = int(reference_tip["slot"])

    first_relays = first.get("amaru_relays") or {}
    final_relays = final.get("amaru_relays") or {}
    missing_relays = [
        name
        for name in AMARU_RELAYS
        if name not in first_relays
        or name not in final_relays
        or _integer((first_relays.get(name) or {}).get("current_slot")) is None
        or _integer((final_relays.get(name) or {}).get("current_slot")) is None
    ]
    if missing_relays:
        return _answer(
            observation,
            state="unknown",
            reason_code="insufficient_amaru_visibility",
            affected=missing_relays,
            reference_tip=reference_tip,
        )

    fatal_relays = [
        name
        for name in AMARU_RELAYS
        if list((final_relays.get(name) or {}).get("fatal_signatures") or [])
    ]
    if fatal_relays:
        return _answer(
            observation,
            state="unhealthy",
            reason_code="amaru_fatal_signal",
            affected=fatal_relays,
            reference_tip=reference_tip,
        )

    initial_consumer_slot = int(first_tips[AMARU_CONSUMER]["slot"])
    final_consumer_slot = int(final_tips[AMARU_CONSUMER]["slot"])
    consumer_lag = max(0, reference_slot - final_consumer_slot)

    stalled_relays = []
    catching_up = []
    for name in AMARU_RELAYS:
        initial_slot = int(first_relays[name]["current_slot"])
        current_slot = int(final_relays[name]["current_slot"])
        lag = max(0, reference_slot - current_slot)
        if lag <= slot_lag_limit:
            continue
        if current_slot <= initial_slot:
            stalled_relays.append(name)
        else:
            catching_up.append(name)
    if stalled_relays:
        return _answer(
            observation,
            state="unhealthy",
            reason_code="amaru_relay_stalled",
            affected=stalled_relays,
            reference_tip=reference_tip,
            consumer_lag_slots=consumer_lag,
        )

    if consumer_lag > slot_lag_limit and final_consumer_slot <= initial_consumer_slot:
        return _answer(
            observation,
            state="unhealthy",
            reason_code="amaru_consumer_stalled",
            affected=[AMARU_CONSUMER],
            reference_tip=reference_tip,
            consumer_lag_slots=consumer_lag,
        )
    if consumer_lag > slot_lag_limit:
        catching_up.append(AMARU_CONSUMER)

    if catching_up:
        return _answer(
            observation,
            state="catching_up",
            reason_code="mixed_topology_catching_up",
            affected=catching_up,
            reference_tip=reference_tip,
            consumer_lag_slots=consumer_lag,
        )

    return _answer(
        observation,
        state="healthy",
        reason_code="all_mixed_readiness_gates_passed",
        reference_tip=reference_tip,
        consumer_lag_slots=consumer_lag,
    )
