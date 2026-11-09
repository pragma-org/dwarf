"""F102: an unreadable cardano-node rejection is read with the reference image's own cardano-cli.

The raw LocalTxSubmission helper returns the node's ApplyTxErr as a Haskell term dump, which no
text marker matches. A decoder bound to cardano-node 11.1.2 positions read it for one release only.
The reference image's cardano-cli always matches the node's ledger, so its error text names the
predicate failure for any release.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import runtime_redeemer_container_live_controls as lc

TERM_DUMP = "TList [TList [TInt 6,TList [TList [TInt 1,TList [TInt 0]]]]]"
CLI_TEXT = ("Command failed: transaction submit  Error: Error while submitting tx: ShelleyTxValidationError "
            "ShelleyBasedEraConway (ApplyTxError (ConwayUtxowFailure (MissingRedeemers [(ConwayMinting ...)]) :| []))")


def _raw(outcome="node_rejected", body=TERM_DUMP):
    http = 400 if outcome == "node_rejected" else 202
    return {"http": http, "body": body, "protocol_receipt": {"outcome": outcome}, "observation_boundary": "node-submit-rejected"}


def _envelope(tmp_path):
    signed = tmp_path / "setup.tx"
    signed.write_text(json.dumps({"type": "Tx SomeFutureEra", "description": "", "cborHex": "84"}))
    return signed


def test_unreadable_node_rejection_is_classified_from_the_reference_cli(tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1, stdout="", stderr=CLI_TEXT)

    out = lc.readable_reference_response(
        _raw(), payload=b"\x84\x01", cli="/w/cardano-cli", socket_path="/env/socket/node2/sock", network_magic=42,
        envelope_from=_envelope(tmp_path), work_dir=tmp_path, run=run)
    assert out["classification"] == "ledger_reject"
    assert out["body"] == CLI_TEXT
    assert out["raw_rejection_body"] == TERM_DUMP
    assert out["reject_reason_source"] == "reference-image-cardano-cli"
    (command,) = calls
    assert command[:4] == ["/w/cardano-cli", "latest", "transaction", "submit"]
    tx_file = Path(command[command.index("--tx-file") + 1])
    envelope = json.loads(tx_file.read_text())
    assert envelope["type"] == "Tx SomeFutureEra" and envelope["cborHex"] == "8401"
    assert command[command.index("--socket-path") + 1] == "/env/socket/node2/sock"
    assert command[command.index("--testnet-magic") + 1] == "42"


def test_cli_acceptance_after_a_node_rejection_stays_inconclusive(tmp_path):
    out = lc.readable_reference_response(
        _raw(), payload=b"\x84", cli="cli", socket_path="s", network_magic=42, envelope_from=_envelope(tmp_path),
        work_dir=tmp_path, run=lambda c, **k: SimpleNamespace(returncode=0, stdout="Transaction successfully submitted.", stderr=""))
    assert out["classification"] == "inconclusive_reject"
    assert out["reject_reason_source"] == "reference-image-cardano-cli-accepted-after-node-reject"
    assert out["body"] == TERM_DUMP


@pytest.mark.parametrize("raw", [_raw("accepted", "accepted"), _raw(body="... MissingRedeemers ...")])
def test_readable_or_accepted_responses_do_not_resubmit(raw, tmp_path):
    def run(command, **kwargs):
        raise AssertionError("cli must not run")

    out = lc.readable_reference_response(raw, payload=b"\x84", cli="cli", socket_path="s", network_magic=42,
                                         envelope_from=_envelope(tmp_path), work_dir=tmp_path, run=run)
    assert "reject_reason_source" not in out


def test_no_release_pinned_reject_decoder_remains():
    source = Path(lc.__file__).read_text()
    assert "_derive_cardano_node_11_1_2_reject_term" not in source
    assert "source_bound_derivation" not in source


@pytest.mark.parametrize("module", ["runtime_redeemer_container_live_controls", "runtime_plutus_case_differential",
                                    "runtime_governance_zero_denominator", "runtime_inline_datum_tag24_boundary",
                                    "runtime_multiasset_compact_boundary", "runtime_uplc_deep_delay_stack_safety"])
def test_reference_submissions_go_through_the_cli_reader(module):
    source = (Path(lc.__file__).parent / f"{module}.py").read_text()
    body = source.replace("def readable_reference_response(", "").replace("def reference_reader(", "")
    raw_calls = body.count("_submit_reference_raw(") - body.count("def _submit_reference_raw(")
    read_calls = (body.count("readable_reference_response(") + body.count("read_reference(_submit_reference_raw(")
                  + body.count("_read_ref(read_reference, _submit_reference_raw(")
                  + body.count("submit_raw=lambda: _submit_reference_raw("))
    assert raw_calls >= 1 and read_calls >= raw_calls


def test_a_cli_side_read_failure_is_not_taken_as_the_node_reason(tmp_path):
    """A typed node rejection means the node decoded the transaction; a cli decode error is the
    cli's own failure to read the file, so the case stays inconclusive."""
    out = lc.readable_reference_response(
        _raw(), payload=b"\x84", cli="cli", socket_path="s", network_magic=42, envelope_from=_envelope(tmp_path),
        work_dir=tmp_path,
        run=lambda c, **k: SimpleNamespace(returncode=1, stdout="", stderr="TxCmdTxReadError: DeserialiseFailure"))
    assert out["classification"] == "inconclusive_reject"
    assert out["body"] == TERM_DUMP
    assert out["reject_reason_source"] == "reference-image-cardano-cli-unattributable"
    assert "DeserialiseFailure" in out["reference_cli_output"]


def test_reference_submission_correlates_a_dropped_connection_before_reading(monkeypatch, tmp_path):
    """cardano-node drops the connection on a transaction it cannot decode; the node-log
    correlation must run first, and a decode_reject it proves is not resubmitted."""
    seen = {}

    def correlate(*, submit, payload, reference):
        seen["first"] = submit()
        return {**seen["first"], "classification": "decode_reject",
                "observation_boundary": "node-log-correlated-decode-reject",
                "reference_decoder_evidence": {"classification": "decode_reject"}}

    monkeypatch.setattr(lc, "_submit_reference_with_decoder_log", correlate)
    raw = {"http": 0, "body": "", "protocol_receipt": {"outcome": "transport_unavailable"}}
    out = lc.submit_reference_readable(submit_raw=lambda: raw, payload=b"\x84", reference={"id": "node2"},
                                       read=lambda response, payload: pytest.fail("decode_reject must not be resubmitted"))
    assert seen["first"]["classification"] == "unavailable"
    assert out["classification"] == "decode_reject"


@pytest.mark.parametrize("module", ["runtime_inline_datum_tag24_boundary", "runtime_multiasset_compact_boundary",
                                    "runtime_uplc_deep_delay_stack_safety"])
def test_boundary_runners_correlate_and_classify_reference_rejections(module):
    source = (Path(lc.__file__).parent / f"{module}.py").read_text()
    assert source.count("submit_reference_readable(") >= 2
    assert "_validation_stage(" not in source.split("def run_", 1)[1]
