"""Taps rerun 20261110T102424Z: with taps on, Amaru writes verbose JSON traces. The fresh-peer wait
refused a log slice over 4 MiB and only matched the text form, so the governance run errored."""
import json

from scripts import runtime_governance_zero_denominator as governance

PEER = "172.30.0.3:3001"


def _json_connected(conn_id):
    return json.dumps({"timestamp": "2026-11-10T10:30:00Z", "level": "INFO", "target": "amaru::manager",
                       "fields": {"message": "manager.peer.connected", "peer": PEER, "conn_id": conn_id}}) + "\n"


def _noise(size):
    line = json.dumps({"timestamp": "2026-11-10T10:29:00Z", "level": "INFO", "target": "amaru::consensus",
                       "fields": {"message": "chain_sync.roll_forward", "slot": 1}}) + "\n"
    return line * (size // len(line) + 1)


def _wait(tmp_path, body, start=0):
    log = tmp_path / "amaru.log"
    log.write_text(body)
    return governance._wait_for_fresh_peer_connection(
        target={"log_path": str(log)}, expected_peer=PEER, prior_connection_id=5,
        log_start_offset=start, timeout_seconds=1.0)


def test_a_json_connected_record_is_found(tmp_path):
    result = _wait(tmp_path, _noise(1000) + _json_connected(77))
    assert result["status"] == "connected" and result["conn_id"] == 77


def test_a_verbose_log_larger_than_the_bound_is_scanned_in_chunks(tmp_path):
    body = _noise(governance._MAX_P2P_TRACE_BYTES * 2) + _json_connected(78)
    result = _wait(tmp_path, body)
    assert result["status"] == "connected" and result["conn_id"] == 78
    assert result["event_offset"] == len(body.encode()) - len(_json_connected(78).encode())


def test_the_prior_connection_does_not_count(tmp_path):
    import pytest
    with pytest.raises(RuntimeError, match="did not establish a fresh peer connection"):
        _wait(tmp_path, _noise(1000) + _json_connected(5))
