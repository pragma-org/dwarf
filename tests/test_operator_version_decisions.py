from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from profile_manager import operator_version_decisions as decisions


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def registration(identifier: str = "registration-1") -> dict:
    return {
        "registration_id": identifier,
        "implementation": "amaru",
        "version": "10.11.20260918",
        "image_reference": "registry.invalid/amaru@sha256:" + SHA_A,
        "manifest_digest": "sha256:" + SHA_A,
        "config_image_id": "sha256:" + SHA_B,
        "platform": "linux/amd64",
        "runtime_interface": "extracted-binary",
        "inspected_at": "2026-10-11T12:00:00Z",
        "provenance": {
            "source_revision": None,
            "public": None,
            "qualification": "unverified",
        },
    }


def subject() -> dict:
    return {
        "implementation": "amaru",
        "version": "10.11.20260918",
        "image_reference": "registry.invalid/amaru@sha256:" + SHA_A,
        "manifest_digest": "sha256:" + SHA_A,
        "config_image_id": "sha256:" + SHA_B,
        "scenario_id": "scenario-1",
        "scenario_sha256": SHA_C,
        "scope": "amaru-only",
        "topology_profile_id": "profile-1",
        "deployment_identity_sha256": SHA_A,
        "mixed_pair": None,
    }


def decision(identifier: str, verdict: str, *, decided_at: str) -> dict:
    return {
        "decision_id": identifier,
        "subject_key": decisions.subject_key(subject()),
        "subject": subject(),
        "decision": verdict,
        "reason": "operator reviewed canonical evidence",
        "actor": "local-operator",
        "decided_at": decided_at,
        "evidence": {
            "run_id": "run-1",
            "manifest_sha256": SHA_B,
            "operational_outcome": "completed",
            "assertion_verdict": "fail",
        },
    }


def preference(identifier: str, decision_id: str | None, *, action: str) -> dict:
    return {
        "preference_id": identifier,
        "scope_key": "amaru-only:profile-1",
        "action": action,
        "decision_id": decision_id,
        "recorded_at": "2026-10-11T12:05:00Z",
    }


def test_empty_state_revision_excludes_its_own_revision(tmp_path):
    state_dir = tmp_path / "absent-state"
    ledger = decisions.OperatorVersionDecisionLedger(state_dir)
    state = ledger.read()

    assert state["schema_version"] == "v1"
    assert state["registrations"] == []
    assert state["decisions"] == []
    assert state["preferred_defaults"] == []
    assert state["revision"] == decisions.canonical_revision(state)

    changed_revision_only = {**state, "revision": "sha256:" + SHA_A}
    assert decisions.canonical_revision(changed_revision_only) == state["revision"]
    assert not state_dir.exists(), "an empty metadata read must not create state"


def test_registration_preserves_unknown_provenance_without_invention(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    original = ledger.read()

    updated = ledger.append_registration(registration(), expected_revision=original["revision"])

    assert updated["registrations"] == [registration()]
    assert updated["registrations"][0]["provenance"] == {
        "source_revision": None,
        "public": None,
        "qualification": "unverified",
    }
    assert updated["revision"] != original["revision"]
    assert json.loads(ledger.path.read_text())["revision"] == updated["revision"]


def test_decisions_are_append_only_and_latest_event_is_active(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    supported = decision("decision-1", "supported", decided_at="2026-10-11T12:01:00Z")
    first = ledger.append_decision(supported, expected_revision=initial["revision"])
    unsupported = decision("decision-2", "unsupported", decided_at="2026-10-11T12:02:00Z")
    second = ledger.append_decision(unsupported, expected_revision=first["revision"])

    assert second["decisions"] == [supported, unsupported]
    assert decisions.active_decisions(second) == {supported["subject_key"]: unsupported}


def test_preference_requires_active_supported_decision_and_clear_is_history(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    supported = decision("decision-1", "supported", decided_at="2026-10-11T12:01:00Z")
    decided = ledger.append_decision(supported, expected_revision=initial["revision"])
    set_event = preference("preference-1", supported["decision_id"], action="set")
    preferred = ledger.append_preference(set_event, expected_revision=decided["revision"])

    assert decisions.active_preferences(preferred) == {set_event["scope_key"]: set_event}

    cleared = preference("preference-2", None, action="clear")
    cleared_state = ledger.append_preference(cleared, expected_revision=preferred["revision"])
    assert cleared_state["preferred_defaults"] == [set_event, cleared]
    assert decisions.active_preferences(cleared_state) == {}

    unsupported_state = decisions.OperatorVersionDecisionLedger(tmp_path / "other")
    base = unsupported_state.read()
    rejected = decision("decision-x", "unsupported", decided_at="2026-10-11T12:03:00Z")
    after = unsupported_state.append_decision(rejected, expected_revision=base["revision"])
    with pytest.raises(ValueError, match="active supported decision"):
        unsupported_state.append_preference(
            preference("preference-x", rejected["decision_id"], action="set"),
            expected_revision=after["revision"],
        )


def test_preference_scope_matches_subject_and_is_inactive_after_unsupported(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    supported = decision("decision-1", "supported", decided_at="2026-10-11T12:01:00Z")
    decided = ledger.append_decision(supported, expected_revision=initial["revision"])

    wrong_scope = preference("preference-wrong", supported["decision_id"], action="set")
    wrong_scope["scope_key"] = "cardano-only:profile-elsewhere"
    with pytest.raises(ValueError, match="scope"):
        ledger.append_preference(wrong_scope, expected_revision=decided["revision"])

    set_event = preference("preference-1", supported["decision_id"], action="set")
    preferred = ledger.append_preference(set_event, expected_revision=decided["revision"])
    unsupported = decision("decision-2", "unsupported", decided_at="2026-10-11T12:02:00Z")
    current = ledger.append_decision(unsupported, expected_revision=preferred["revision"])

    assert decisions.active_preferences(current) == {}

    malformed = json.loads(ledger.path.read_text())
    malformed["preferred_defaults"][0]["scope_key"] = "cardano-only:elsewhere"
    malformed["revision"] = decisions.canonical_revision(malformed)
    with pytest.raises(decisions.InvalidOperatorVersionState, match="scope mismatch"):
        decisions.validate_state(malformed)


def test_stale_revision_has_no_write(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    current = ledger.append_registration(registration(), expected_revision=initial["revision"])
    before = ledger.path.read_bytes()

    with pytest.raises(decisions.StaleOperatorVersionState):
        ledger.append_registration(
            registration("registration-2"), expected_revision=initial["revision"]
        )

    assert ledger.path.read_bytes() == before
    assert ledger.read()["revision"] == current["revision"]


def _concurrent_registration(root: str, expected: str, identifier: str, start, output):
    ledger = decisions.OperatorVersionDecisionLedger(root)
    start.wait()
    try:
        state = ledger.append_registration(
            registration(identifier), expected_revision=expected
        )
        output.put(("ok", state["revision"]))
    except decisions.StaleOperatorVersionState:
        output.put(("stale", None))


def test_real_interprocess_writers_cannot_lose_an_update(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    expected = ledger.read()["revision"]
    context = multiprocessing.get_context("fork")
    start = context.Event()
    output = context.Queue()
    workers = [
        context.Process(
            target=_concurrent_registration,
            args=(str(tmp_path), expected, f"registration-{index}", start, output),
        )
        for index in (1, 2)
    ]
    for worker in workers:
        worker.start()
    start.set()
    results = [output.get(timeout=10) for _worker in workers]
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    assert sorted(result[0] for result in results) == ["ok", "stale"]
    final = ledger.read()
    assert len(final["registrations"]) == 1
    assert final["revision"] == decisions.canonical_revision(final)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda body: body.update(schema_version="v2"), "schema_version"),
        (lambda body: body.update(registrations="not-a-list"), "registrations"),
        (lambda body: body.update(extra="not-allowed"), "Additional properties"),
    ],
)
def test_malformed_persisted_state_fails_closed(tmp_path, mutation, message):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    body = ledger.read()
    mutation(body)
    ledger.path.parent.mkdir(parents=True, exist_ok=True)
    ledger.path.write_text(json.dumps(body))

    with pytest.raises(decisions.InvalidOperatorVersionState, match=message):
        ledger.read()


def test_symlink_nonregular_oversized_and_foreign_owned_state_are_refused(tmp_path, monkeypatch):
    target = tmp_path / "target.json"
    target.write_text("{}")
    symlink_root = tmp_path / "symlink-root"
    symlink_root.symlink_to(tmp_path / "real-root", target_is_directory=True)
    with pytest.raises(decisions.UnsafeOperatorVersionState, match="symlink"):
        decisions.OperatorVersionDecisionLedger(symlink_root).read()

    directory_ledger = decisions.OperatorVersionDecisionLedger(tmp_path / "directory")
    directory_ledger.path.mkdir(parents=True)
    with pytest.raises(decisions.UnsafeOperatorVersionState, match="regular"):
        directory_ledger.read()

    oversized = decisions.OperatorVersionDecisionLedger(tmp_path / "oversized")
    oversized.path.parent.mkdir(parents=True)
    oversized.path.write_bytes(b" " * (decisions.MAX_STATE_BYTES + 1))
    with pytest.raises(decisions.UnsafeOperatorVersionState, match="large"):
        oversized.read()

    owned = decisions.OperatorVersionDecisionLedger(tmp_path / "owned")
    owned.path.parent.mkdir(parents=True)
    owned.path.write_text("{}")
    original_fstat = decisions.os.fstat

    def foreign_file_owner(descriptor):
        opened = original_fstat(descriptor)
        return SimpleNamespace(
            st_mode=opened.st_mode,
            st_uid=os.geteuid() + 1,
            st_size=opened.st_size,
        )

    monkeypatch.setattr(decisions.os, "fstat", foreign_file_owner)
    with pytest.raises(decisions.UnsafeOperatorVersionState, match="owner"):
        owned.read()


def _read_fifo(root: str, output):
    try:
        decisions.OperatorVersionDecisionLedger(root).read()
    except Exception as exc:  # process boundary reports the precise safe failure
        output.put((type(exc).__name__, str(exc)))


def test_fifo_state_is_rejected_without_blocking(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    ledger.path.parent.mkdir(parents=True)
    os.mkfifo(ledger.path)
    context = multiprocessing.get_context("fork")
    output = context.Queue()
    worker = context.Process(target=_read_fifo, args=(str(tmp_path), output))
    worker.start()
    worker.join(timeout=1)
    if worker.is_alive():
        worker.terminate()
        worker.join(timeout=2)
    assert not worker.is_alive(), "FIFO read blocked before the regular-file guard"
    assert output.get(timeout=2) == (
        "UnsafeOperatorVersionState",
        "operator state is not a regular file",
    )


def test_atomic_replace_failure_retains_previous_document(tmp_path, monkeypatch):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    first = ledger.append_registration(registration(), expected_revision=initial["revision"])
    before = ledger.path.read_bytes()

    def fail_replace(_source, _destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(decisions.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        ledger.append_registration(
            registration("registration-2"), expected_revision=first["revision"]
        )

    assert ledger.path.read_bytes() == before


def test_post_replace_directory_flush_failure_is_resolved_by_readback(tmp_path, monkeypatch):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    first = ledger.append_registration(registration(), expected_revision=initial["revision"])
    original_fsync = decisions.os.fsync
    calls = 0

    def fail_directory_flush(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated directory fsync failure after replace")
        return original_fsync(descriptor)

    monkeypatch.setattr(decisions.os, "fsync", fail_directory_flush)
    updated = ledger.append_registration(
        registration("registration-2"), expected_revision=first["revision"]
    )

    assert calls == 3, "the ledger must retry the failed parent-directory fsync"
    assert len(updated["registrations"]) == 2
    assert ledger.read() == updated


def test_persistent_post_replace_flush_failure_is_commit_indeterminate(tmp_path, monkeypatch):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    first = ledger.append_registration(registration(), expected_revision=initial["revision"])
    original_fsync = decisions.os.fsync
    calls = 0

    def fail_directory_flushes(descriptor):
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("persistent directory fsync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(decisions.os, "fsync", fail_directory_flushes)
    with pytest.raises(decisions.IndeterminateOperatorVersionCommit) as captured:
        ledger.append_registration(
            registration("registration-2"), expected_revision=first["revision"]
        )

    observed = ledger.read()
    assert captured.value.observed_revision == observed["revision"]
    assert len(observed["registrations"]) == 2


def test_duplicate_event_ids_and_subject_key_mismatch_are_rejected(tmp_path):
    ledger = decisions.OperatorVersionDecisionLedger(tmp_path)
    initial = ledger.read()
    event = decision("decision-1", "supported", decided_at="2026-10-11T12:01:00Z")
    first = ledger.append_decision(event, expected_revision=initial["revision"])

    with pytest.raises(ValueError, match="decision_id"):
        ledger.append_decision(event, expected_revision=first["revision"])

    wrong = decision("decision-2", "supported", decided_at="2026-10-11T12:02:00Z")
    wrong["subject_key"] = "sha256:" + SHA_C
    with pytest.raises(ValueError, match="subject_key"):
        ledger.append_decision(wrong, expected_revision=first["revision"])
