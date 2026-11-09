"""F144: fork-switch, range-pressure and Praos-header results come from what the proxy and node did.

Live 33822761 (fork switch read after 6 messages, the session-start rollback counted), 3d9f5d98
(observed_peak_blocks_in_memory = a constant derived from segment counts; no blocks measured) and
37117ba3 (Praos header probe was a fail-closed stub).
"""
from __future__ import annotations

import time

from scripts import runtime_chainsync_blockfetch_fault as fault


def _fork_switch(**changes):
    body = {"connection_id": 2, "injected_at": 100.0, "rollback_depth": 2,
            "rollback_point": {"slot": 50, "hash": "aa" * 32}, "replayed": [{}, {}], "displaced": {"slot": 130},
            "rollback_delivered_at": 100.1, "replays_delivered": 2, "displaced_delivered_at": 100.3,
            "requests_after_rollback": 4, "requests_after_displaced": 1, "forwards_after_rollback": 4,
            "swallowed_requests": 3}
    body.update(changes)
    return body


# --------------------------------------------------------------------------- fork switch

def test_fork_switch_followed_by_the_follower_is_a_clean_rollback_then_forward():
    stats = {"chainsync_messages_observed": 40, "fork_switches": [_fork_switch()],
             "connections": [{"id": 2, "mutated_segments": 1, "closed_by": None}]}
    body = fault.build_result_body(mode="chainsync_responder_fork_switch", stats=stats, target_healthy=True)
    assert body["rollback_then_forward_sequence_observed"] is True
    assert body["follower_state_rewritten"] is True
    assert body["rollback_then_forward_count"] == 1
    assert body["rollback_depth"] == 2 and body["follower_dropped_connection"] is False


def test_session_start_rollbacks_do_not_count():
    """The live false signal: stats rollback_then_forward_count 2 with no injected fork switch."""
    stats = {"chainsync_messages_observed": 90, "rollback_then_forward_count": 2,
             "chainsync_roll_backward_count": 2, "chainsync_roll_forward_count": 26}
    body = fault.build_result_body(mode="chainsync_responder_fork_switch", stats=stats, target_healthy=True)
    assert body["rollback_then_forward_count"] == 0
    assert body["rollback_then_forward_sequence_observed"] is False
    assert body["follower_state_rewritten"] is False
    assert body["fork_switch"] is None


def test_a_follower_that_drops_after_the_rollback_did_not_follow():
    stats = {"fork_switches": [_fork_switch(requests_after_displaced=0, displaced_delivered_at=None)],
             "connections": [{"id": 2, "mutated_segments": 1, "closed_by": "downstream", "drop_seconds": 0.2}]}
    body = fault.build_result_body(mode="chainsync_responder_fork_switch", stats=stats, target_healthy=True)
    assert body["rollback_then_forward_count"] == 0 and body["follower_dropped_connection"] is True
    assert body["follower_state_rewritten"] is False


def test_fork_switch_ready_only_after_the_follower_answered_or_dropped():
    assert not fault.proxy_activity_ready(mode="chainsync_responder_fork_switch",
                                          stats={"chainsync_messages_observed": 6})
    fresh = _fork_switch(displaced_delivered_at=time.time())
    assert not fault.proxy_activity_ready(mode="chainsync_responder_fork_switch",
                                          stats={"fork_switches": [fresh], "connections": [{"id": 2}]})
    settled = _fork_switch(displaced_delivered_at=time.time() - 10)
    assert fault.proxy_activity_ready(mode="chainsync_responder_fork_switch",
                                      stats={"fork_switches": [settled], "connections": [{"id": 2}]})
    dropped = _fork_switch(displaced_delivered_at=None, requests_after_displaced=0)
    assert fault.proxy_activity_ready(mode="chainsync_responder_fork_switch",
                                      stats={"fork_switches": [dropped],
                                             "connections": [{"id": 2, "closed_by": "downstream"}]})


# --------------------------------------------------------------------------- range pressure

def test_range_pressure_peak_comes_from_measured_batches():
    stats = {"blocks_fetched": 120, "block_range_requests_observed": 3, "intercepted_segments": 400,
             "blockfetch_batches": [{"blocks": 100}, {"blocks": 15}, {"blocks": 5}],
             "peak_blocks_per_batch": 100, "peak_outstanding_range_requests": 2}
    body = fault.build_result_body(mode="blockfetch_range_pressure", stats=stats, target_healthy=True,
                                   configured_limit=64)
    assert body["observed_peak_blocks_per_batch"] == 100
    assert body["resource_bound_ok"] is False                 # 100 > 64: measured, not clamped
    assert "observed_peak_blocks_in_memory" not in body
    body = fault.build_result_body(mode="blockfetch_range_pressure", stats=stats, target_healthy=True,
                                   configured_limit=128)
    assert body["resource_bound_ok"] is True and body["batches_completed"] == 3


def test_range_pressure_without_batches_is_not_bounded():
    """Live 3d9f5d98: 29 segments became a 'peak of 29 blocks'."""
    body = fault.build_result_body(mode="blockfetch_range_pressure", target_healthy=True,
                                   stats={"intercepted_segments": 29, "connections_seen": 2})
    assert body["observed_peak_blocks_per_batch"] is None
    assert body["resource_bound_ok"] is False


def test_range_pressure_waits_for_the_sync_to_settle():
    stats = {"blocks_fetched": 4, "blockfetch_batches": [{"blocks": 4}]}
    assert fault.proxy_activity_ready(mode="blockfetch_range_pressure", stats=stats)
    assert fault.MODE_SETTLE_SECONDS["blockfetch_range_pressure"] > 0


def test_resync_moves_the_db_aside(tmp_path):
    db = tmp_path / "node-data" / "node1" / "db"
    (db / "immutable").mkdir(parents=True)
    (db / "immutable" / "00000.chunk").write_bytes(b"x")
    moved = fault.move_db_aside(db, label="range-pressure")
    assert not db.exists() or not any(db.iterdir())
    assert (moved / "immutable" / "00000.chunk").is_file()


# --------------------------------------------------------------------------- Praos header

def _corruption(**changes):
    body = {"connection_id": 3, "at": 100.0, "slot": 420, "block_no": 21,
            "original_header_hash": "11" * 32, "corrupted_header_hash": "22" * 32,
            "corrupted_field": "kes_signature_last_byte", "block_requested": False}
    body.update(changes)
    return body


def test_praos_header_mode_corrupts_a_header_signature():
    d = fault.MODE_DEFAULTS["praos_header_invalid_signature"]
    assert (d["mutation_mode"], d["mutation_protocol"], d["mutation_direction"]) == (
        "corrupt_header_signature", "2", "inbound")


def test_praos_header_rejected_from_drop_and_log():
    stats = {"header_corruptions": [_corruption()],
             "connections": [{"id": 3, "mutated_segments": 1, "closed_by": "downstream", "drop_seconds": 0.4}]}
    log = {"matched": True, "lines": ["... HeaderError ... InvalidKesSignatureOCERT ..."],
           "reason": "InvalidKesSignatureOCERT", "adopted": False}
    body = fault.build_result_body(mode="praos_header_invalid_signature", stats=stats, target_healthy=True,
                                   log_evidence=log)
    assert body["header_rejected"] is True and body["assertion_boundary_preserved"] is True
    assert body["rejection_reason"] == "InvalidKesSignatureOCERT"
    assert body["corrupted_header_hash"] == "22" * 32


def test_praos_header_fetched_or_adopted_breaks_the_boundary():
    stats = {"header_corruptions": [_corruption(block_requested=True)],
             "connections": [{"id": 3, "mutated_segments": 1, "closed_by": "downstream", "drop_seconds": 0.4}]}
    body = fault.build_result_body(mode="praos_header_invalid_signature", stats=stats, target_healthy=True,
                                   log_evidence={"matched": False, "lines": [], "reason": None, "adopted": False})
    assert body["assertion_boundary_preserved"] is False


def test_no_corruption_is_no_rejection():
    body = fault.build_result_body(mode="praos_header_invalid_signature", target_healthy=True,
                                   stats={"intercepted_segments": 40}, log_evidence=None)
    assert body["header_rejected"] is False and body["violation_crafted"] is False


def test_log_scan_finds_the_header_error_for_the_corrupted_hash():
    lines = [
        '{"ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":{"newtip":"' + "33" * 32 + '@400"}}',
        '{"ns":"Net.ConnectionManager.Remote.ConnectionHandler","data":{"error":"ChainSyncClientException '
        'HeaderError (blockPointHash = ' + "22" * 32 + ') (InvalidKesSignatureOCERT 1 2 3)"}}',
    ]
    found = fault.scan_header_rejection(lines, corrupted_hash="22" * 32)
    assert found["matched"] is True and found["reason"] == "InvalidKesSignatureOCERT"
    assert found["adopted"] is False
    adopted = fault.scan_header_rejection(
        ['{"ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":{"newtip":"' + "22" * 32 + '@420"}}'],
        corrupted_hash="22" * 32)
    assert adopted["adopted"] is True and adopted["matched"] is False
