"""F130: invalid-block rejection is the target node dropping the peer that served a corrupted block.

Before: the proxy flipped the first segment of any protocol and "invalid_block_rejected" was
"target healthy and any proxy traffic".
"""
from scripts import runtime_chainsync_blockfetch_fault as fault


def _conn(**kw):
    base = {"id": 1, "mutated_segments": 0, "first_mutation_at": None, "closed_at": None, "closed_by": None,
            "drop_seconds": None}
    base.update(kw)
    return base


def test_invalid_block_mode_corrupts_only_inbound_blocks():
    defaults = fault.MODE_DEFAULTS["blockfetch_invalid_block_cbor"]
    assert (defaults["mutation_protocol"], defaults["mutation_message"], defaults["mutation_direction"]) == ("3", "block", "inbound")


def test_rejection_is_the_target_dropping_the_corrupting_connection():
    stats = {"mutated_segments": 1, "connections": [_conn(mutated_segments=1, closed_by="downstream", drop_seconds=0.04)]}
    body = fault.build_result_body(mode="blockfetch_invalid_block_cbor", stats=stats, target_healthy=True)
    assert body["invalid_block_rejected"] is True
    assert body["dropped_by_target"] is True and body["drop_seconds"] == 0.04
    assert body["mutation"] == "flip_last_payload_byte" and body["mutated_message"] == "block"


def test_traffic_without_a_corrupted_block_is_not_a_rejection():
    stats = {"intercepted_segments": 50, "connections_seen": 2, "connections": [_conn(closed_by="downstream")]}
    body = fault.build_result_body(mode="blockfetch_invalid_block_cbor", stats=stats, target_healthy=True)
    assert body["invalid_block_rejected"] is False and body["mutated_connection_seen"] is False


def test_a_connection_closed_by_the_upstream_is_not_the_target_rejecting():
    stats = {"connections": [_conn(mutated_segments=1, closed_by="upstream", drop_seconds=0.2)]}
    body = fault.build_result_body(mode="blockfetch_invalid_block_cbor", stats=stats, target_healthy=True)
    assert body["invalid_block_rejected"] is False


def test_ready_only_once_a_corrupting_connection_closed():
    assert not fault.proxy_activity_ready(mode="blockfetch_invalid_block_cbor",
                                          stats={"intercepted_segments": 9, "connections": [_conn(mutated_segments=1)]})
    assert fault.proxy_activity_ready(mode="blockfetch_invalid_block_cbor",
                                      stats={"connections": [_conn(mutated_segments=1, closed_by="downstream", closed_at=1.0)]})
