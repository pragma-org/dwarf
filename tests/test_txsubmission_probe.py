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
        self.header_words: list[int] = []
        self.error: Exception | None = None

    def _recv_frame(self, conn):
        import struct
        header = probe._recv_exact(conn, 8)
        _timestamp, word = struct.unpack(">II", header)
        self.header_words.append(word)
        return probe._recv_exact(conn, word & 0xFFFF)

    def run(self):
        try:
            conn, _ = self.server.accept()
            conn.settimeout(5)
            handshake = self._recv_frame(conn)
            self.received.append(handshake)
            # MsgAcceptVersion [1, 15, params]: the node accepted the proposal.
            conn.sendall(probe._encode_mux_sdu(bytes.fromhex("83010f84182af400f4"), mini_protocol_num=0, initiator=False))
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


AMARU_OFFER = bytes.fromhex("8200a10f84182af400f4")   # the proposal Amaru 20261029 accepts (calibration helper)


def test_serve_transaction_offers_the_id_then_the_body(tmp_path):
    node = _FakeNode()
    node.start()
    report = probe.run_probe(
        runtime_metadata_path=None, target_node="amaru-relay-1", output_dir=tmp_path / "probe",
        mode="serve_transaction", target_host="127.0.0.1", target_port=node.port,
        response_timeout_seconds=2.0, tx_body=BODY, tx_id=TXID_TERM, handshake_payload=AMARU_OFFER,
    )
    node.join(5)
    assert node.error is None
    assert node.received[0] == AMARU_OFFER, "the caller's known-accepted proposal is sent verbatim"
    # Every frame we send travels in the initiator direction (mux bit 31 clear), exactly as
    # the calibration helper's accepted handshake frame 0x0000000a (review finding #1).
    assert node.header_words and all(word >> 31 == 0 for word in node.header_words), [hex(w) for w in node.header_words]
    assert report["handshake_response_kind"] == "accepted"
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


def test_request_txs_with_indefinite_length_arrays_decodes_and_yields_the_id_terms():
    """Amaru 20261029 encodes MsgRequestTxs as [2, [* txid]] with an indefinite-length
    array (live serve check, 2026-11-04: 'unsupported additional info 31')."""
    from scripts.runtime_txsubmission_accounting import decode_txsubmission_payload
    id_term = bytes([0x82, 0x06, 0x58, 0x20]) + TXID          # [era 6, h'32']
    payload = bytes([0x9F, 0x02, 0x9F]) + id_term + bytes([0xFF, 0xFF])   # [_ 2, [_ id]]
    assert decode_txsubmission_payload(payload).get("message_kind") == "request-txs"
    assert probe._parse_request_txs(payload) == [id_term]
    definite = bytes([0x82, 0x02, 0x81]) + id_term
    assert probe._parse_request_txs(definite) == [id_term]
