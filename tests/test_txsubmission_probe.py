"""TxSubmission2 probe: the serve-transaction mode offers one transaction body over N2N.

Authorized defensive conformance testing: the mode exists so a node's own mempool can
evaluate (and reject) a phase-1-invalid transaction that an N2C entry would never forward.
"""
import json
import socket
import threading
from pathlib import Path

import pytest

from scripts import runtime_txsubmission_probe as probe

TXID = bytes(range(32))
BODY = bytes([0x82, 0x01, 0x58, 0x20]) + bytes(32) + b"\x00" * 300   # 336 bytes: needs a 2-byte CBOR length


TXID_TERM = probe._encode_cbor_bytes(TXID)   # the id as a CBOR term (a byte string here)


def test_single_reply_txids_payload_is_exact():
    payload = probe._build_reply_txids_payload_for(TXID_TERM, 300)
    # [1, [[<id term>, 300]]]
    assert payload == bytes([0x82, 0x01, 0x81, 0x82, 0x58, 0x20]) + TXID + bytes([0x19, 0x01, 0x2C])


def test_reply_txs_payload_embeds_each_body_as_its_cbor_term():
    payload = probe._build_reply_txs_payload([BODY])
    assert payload == bytes([0x82, 0x03, 0x81]) + BODY   # the term itself, no byte-string wrapper


def test_two_byte_cbor_byte_strings_encode():
    assert probe._encode_cbor_bytes(bytes(300))[:3] == bytes([0x59, 0x01, 0x2C])


def test_parse_request_txs_returns_the_requested_ids():
    request = bytes([0x82, 0x02, 0x81, 0x58, 0x20]) + TXID
    assert probe._parse_request_txs(request) == [TXID_TERM]
    with pytest.raises(probe.TxSubmissionDecodeError):
        probe._parse_request_txs(bytes([0x82, 0x00, 0x82, 0x00, 0x01]))   # a request-txids, not request-txs


class _FakeNode(threading.Thread):
    """Accepts one connection and walks the server side of the exchange."""

    def __init__(self):
        super().__init__(daemon=True)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received: list[bytes] = []
        self.error: Exception | None = None

    def run(self):
        try:
            conn, _ = self.server.accept()
            conn.settimeout(5)
            handshake = probe._recv_mux_payload(conn)
            self.received.append(handshake)
            conn.sendall(probe._encode_mux_sdu(handshake, mini_protocol_num=0, initiator=False))
            init = probe._recv_mux_payload(conn)                      # MsgInit from the client
            self.received.append(init)
            conn.sendall(probe._encode_mux_sdu(bytes([0x84, 0x00, 0xF5, 0x00, 0x01]), mini_protocol_num=4, initiator=False))  # request-txids blocking, ack 0, req 1
            reply_ids = probe._recv_mux_payload(conn)
            self.received.append(reply_ids)
            conn.sendall(probe._encode_mux_sdu(bytes([0x82, 0x02, 0x81, 0x58, 0x20]) + TXID, mini_protocol_num=4, initiator=False))
            reply_txs = probe._recv_mux_payload(conn)
            self.received.append(reply_txs)
            conn.sendall(probe._encode_mux_sdu(bytes([0x84, 0x00, 0xF5, 0x01, 0x01]), mini_protocol_num=4, initiator=False))  # ack 1, ask again
            conn.close()
        except Exception as exc:  # pragma: no cover - surfaced through self.error
            self.error = exc
        finally:
            self.server.close()


def test_serve_transaction_offers_the_id_then_the_body(tmp_path):
    node = _FakeNode()
    node.start()
    report = probe.run_probe(
        runtime_metadata_path=None, target_node="amaru-relay-1", output_dir=tmp_path / "probe",
        mode="serve_transaction", target_host="127.0.0.1", target_port=node.port,
        response_timeout_seconds=2.0, tx_body=BODY, tx_id=TXID_TERM,
    )
    node.join(5)
    assert node.error is None
    assert node.received[1] == bytes.fromhex(probe.TXSUBMISSION_INIT_HEX)
    assert node.received[2] == probe._build_reply_txids_payload_for(TXID_TERM, len(BODY))
    assert node.received[3] == probe._build_reply_txs_payload([BODY])
    assert report["mode"] == "serve_transaction" and report["result"]["served"] is True
    assert report["result"]["requested_body"] is True
    assert report["result"]["post_serve_response_kind"] in {"data", "eof", "timeout"}
    assert json.loads((tmp_path / "probe" / "result.json").read_text())["result"]["served"] is True


def test_amaru_workload_scenario_rejects_the_double_spend_over_n2n():
    root = Path(__file__).resolve().parents[1]
    doc = json.loads((root / "dwarf/scenarios/amaru-measurement-workload-stock-portable.yaml").read_text())
    step = next(s for s in doc["load"] if s["primitive"] == "runtime_amaru_measurement_calibration")
    assert step["reject_via_n2n"] == 1
    from profile_manager.primitives import _amaru_workload_flag_arguments
    arguments, logged = _amaru_workload_flag_arguments({"reject_via_n2n": 1})
    assert arguments == ["--reject-via-n2n", "1"] and logged == {"reject_via_n2n": 1}
