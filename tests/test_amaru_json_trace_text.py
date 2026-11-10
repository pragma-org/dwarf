"""Amaru log parsers read both trace forms (text and AMARU_WITH_JSON_TRACES=true).

Fixtures are real records: JSON from profile / Antithesis-rig Amaru runs
(measurements/shared/*amaru-json.ndjson), text from composed-substrate Amaru node logs.
"""
import json
from pathlib import Path

import pytest

from scripts.runtime_common import amaru_trace_as_text, amaru_trace_line_as_text

FIXTURES = Path(__file__).parent / "fixtures"
JSON_LINES = (FIXTURES / "amaru-json-traces-runtime-sample.ndjson").read_text().splitlines()
TEXT_LINES = (FIXTURES / "amaru-text-traces-runtime-sample.log").read_text().splitlines()


def _json(message, *, span=None):
    for line in JSON_LINES:
        record = json.loads(line)
        if record["fields"].get("message") == message and (
            span is None or (record.get("span") or {}).get("name") == span
        ):
            return line, record
    raise AssertionError(message)


def _text(fragment):
    return next(line for line in TEXT_LINES if fragment in line)


def test_text_lines_and_non_amaru_lines_pass_through_unchanged():
    for line in TEXT_LINES + ["plain stdout", "[1, 2]", '{"no": "fields"}', ""]:
        assert amaru_trace_line_as_text(line) == line


def test_tip_adopt_renders_in_the_text_field_order():
    line, record = _json("tip.adopt")
    fields = record["fields"]
    rendered = amaru_trace_line_as_text(line)
    assert rendered.startswith(f"{record['timestamp']}  INFO amaru::consensus: tip.adopt ")
    assert (
        f'tip.adopt slot={fields["slot"]} header_hash="{fields["header_hash"]}" '
        f'block_height={fields["block_height"]}'
    ) in rendered
    # Same shape as a real text record of the same event.
    text = _text("tip.adopt")
    assert text.split(": tip.adopt ")[1].split("=")[0] == rendered.split(": tip.adopt ")[1].split("=")[0]


def test_span_close_renders_as_span_name_close_with_points_in_debug_form():
    line, record = _json("close", span="chain.select_from_block_validation")
    rendered = amaru_trace_line_as_text(line)
    slot, header_hash, height = record["fields"]["point"]
    assert "chain.select_from_block_validation close time.busy=" in rendered
    assert f'point="[{slot}, h\'{header_hash}\', {height}]"' in rendered
    assert rendered.index("valid=true") < rendered.index("header_hash=")
    text = _text("chain.select_from_block_validation close")
    assert text.index("valid=") < text.index("header_hash=")


def test_whole_log_conversion_keeps_line_count():
    blob = "\n".join(JSON_LINES + TEXT_LINES)
    assert len(amaru_trace_as_text(blob).splitlines()) == len(JSON_LINES) + len(TEXT_LINES)


# --- every parser gets the same facts from either form -----------------------


def test_block_apply_bridge_reads_json_traces():
    from scripts import runtime_block_apply_bridge as bridge

    blob = "\n".join(JSON_LINES)
    _, adopt = _json("tip.adopt")
    _, pots = _json("pots.dump")
    _, opened = _json("build.ledger_opened")
    _, handshake = _json("manager.peer.handshake_completed")
    _, close = _json("close", span="chain.select_from_block_validation")
    assert bridge.amaru_tips(blob)[-1] == {
        "slot": adopt["fields"]["slot"],
        "hash": adopt["fields"]["header_hash"],
        "block": adopt["fields"]["block_height"],
    }
    assert bridge.amaru_last_pots(blob) == {
        key: pots["fields"][key] for key in ("treasury", "reserves", "fees", "donations")
    }
    slot, header_hash, block = opened["fields"]["tip"]
    assert bridge.amaru_ledger_tip(blob) == {"slot": slot, "hash": header_hash, "block": block}
    assert bridge.amaru_foreign_peers(blob, proxy_address="203.0.113.1:1") == [
        handshake["fields"]["peer"]
    ]
    verdict = bridge.amaru_header_verdict(blob, close["fields"]["header_hash"])
    assert verdict["verdict"] == "adopt" and verdict["validated"] is True


def test_block_apply_bridge_still_reads_text_traces():
    from scripts import runtime_block_apply_bridge as bridge

    text = _text("tip.adopt")
    tips = bridge.amaru_tips(text)
    assert tips and tips[-1]["slot"] == int(text.split("slot=")[1].split()[0])


def test_epoch_boundary_probe_reads_pots_and_snapshots_from_json(monkeypatch):
    from scripts import runtime_epoch_boundary_probe as probe

    _, pots = _json("pots.dump")
    _, snapshot = _json("stake_distribution.snapshot")
    blob = "\n".join(JSON_LINES)
    monkeypatch.setattr(probe, "_amaru_log", lambda node, config: (blob, None))
    reward = probe._amaru_reward({}, {})
    assert reward["pots_samples"][-1]["treasury"] == pots["fields"]["treasury"]
    body = probe._amaru_stake_snapshot({}, {})
    assert body["measured"] is True, body
    assert str(snapshot["fields"]["epoch"]) in body["hashes_by_epoch"]


def test_redeemer_adoption_gate_reads_json_traces(tmp_path):
    from scripts.runtime_redeemer_container_live_controls import _wait_for_target_adoption

    line, adopt = _json("tip.adopt")
    log = tmp_path / "amaru1.log"
    log.write_text(line + "\n")
    fields = adopt["fields"]
    evidence = _wait_for_target_adoption(
        log_path=log,
        expected_tip={"block": fields["block_height"], "slot": fields["slot"], "hash": fields["header_hash"]},
        evidence_path=tmp_path / "evidence.json",
        timeout_seconds=2,
    )
    assert evidence["status"] == "adopted"


def test_governance_txsubmission_trace_fields_read_from_json():
    from scripts.runtime_governance_zero_denominator import _trace_connection, _trace_field

    line, record = _json("manager.peer.handshake_completed")
    rendered = amaru_trace_line_as_text(line)
    assert _trace_field(rendered, "peer") == record["fields"]["peer"]
    assert _trace_connection(rendered) == record["fields"]["conn_id"]
    accepted = json.dumps({
        "fields": {"message": "transaction.accepted", "tx_id": "a" * 64},
        "level": "INFO", "target": "amaru::mempool", "timestamp": "2026-11-08T00:00:00Z",
    })
    assert "mempool: transaction.accepted" in amaru_trace_line_as_text(accepted)
