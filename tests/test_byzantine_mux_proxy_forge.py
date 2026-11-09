"""forge_substitute: the mux proxy serves the forged block over a real socket.

The upstream splits its RollForward across two mux segments and its MsgBlock is
larger than one SDU after substitution; the downstream must still receive whole,
rewritten messages, and the upstream must see the real points it knows.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import cbor2

from scripts import block_forge
from scripts import byzantine_mux_proxy as proxy
from test_block_forge import CRAFTED, _chain_of_two, _msg_block, _roll_forward


def _segment(payload: bytes, protocol: int, *, responder: bool) -> bytes:
    word = protocol | (proxy.RESPONDER_PROTOCOL_BIT if responder else 0)
    return (7).to_bytes(4, "big") + word.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


def _recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def _recv_message(sock, protocol):
    """Read segments of ``protocol`` until one complete CBOR message is buffered."""
    buffer = b""
    while True:
        head = _recv_exact(sock, 8)
        assert int.from_bytes(head[4:6], "big") & 0x7FFF == protocol
        buffer += _recv_exact(sock, int.from_bytes(head[6:8], "big"))
        try:
            end = block_forge.item_end(buffer)
        except block_forge.Incomplete:
            continue
        assert end == len(buffer)
        return buffer


def test_forge_mode_serves_forged_header_and_block_over_split_segments(tmp_path):
    a, _b, sk, spk = _chain_of_two()
    slot = block_forge.header_fields(a)["slot"]
    big_tx = block_forge.encode_array([cbor2.dumps({0: [], 1: [], 2: 0, 7: b"\x00" * 32}),
                                       cbor2.dumps({0: [[b"\x01" * 32, b"\x02" * 64]] * 120}), b"\xf4", b"\xf6"])
    forge = block_forge.ForgeSubstitution(kes_sign_key=sk, slots_per_kes_period=spk, transactions=[big_tx])
    arm_file = tmp_path / "arm.json"
    arm_file.write_text(json.dumps({"armed_slot": slot}))
    seen_by_upstream = []

    def upstream_script(conn):
        roll = _roll_forward(a)
        conn.sendall(_segment(roll[:40], 2, responder=True))
        time.sleep(0.05)
        conn.sendall(_segment(roll[40:], 2, responder=True))
        head = _recv_exact(conn, 8)
        seen_by_upstream.append(cbor2.loads(_recv_exact(conn, int.from_bytes(head[6:8], "big"))))
        conn.sendall(_segment(_msg_block(a, block_forge.EMPTY_BODY_SEGMENTS), 3, responder=True))
        time.sleep(1.0)

    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def serve():
        conn, _ = upstream.accept()
        with conn:
            upstream_script(conn)

    threading.Thread(target=serve, daemon=True).start()
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=upstream.getsockname()[1], output_dir=tmp_path,
                             mutation_mode="forge_substitute", mutation_direction="both", mutation_protocol="any",
                             mutate_after_segments=1, forge=forge, arm_file=arm_file)
    server = proxy.ThreadedMuxProxy(("127.0.0.1", 0), proxy.MuxProxyHandler, proxy_state=state)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        with socket.create_connection(server.server_address) as node:
            node.settimeout(5)
            forged_header = bytes(cbor2.loads(_recv_message(node, 2))[1][1].value)
            forged_hash = block_forge.header_hash(forged_header)
            assert forged_hash != block_forge.header_hash(a)
            request = block_forge.encode_array([cbor2.dumps(0)] + [
                block_forge.encode_array([cbor2.dumps(slot), cbor2.dumps(forged_hash)])] * 2)
            node.sendall(_segment(request, 3, responder=False))
            served = cbor2.loads(_recv_message(node, 3))
            header, segments = block_forge.split_block(block_forge.array_items(bytes(served[1].value))[1])
    finally:
        server.shutdown()
    assert header == forged_header
    assert block_forge.body_hash(segments).hex() == block_forge.header_fields(forged_header)["body_hash"]
    assert len(block_forge.assemble_block(header, segments)) > proxy.MAX_SDU
    assert seen_by_upstream[0][1][1] == block_forge.header_hash(a)   # upstream asked for the real block
    record = json.loads((tmp_path / "forge-record.json").read_text())
    assert record["armed_slot"] == slot
    assert record["forged_hash"] == forged_hash.hex()
    assert record["served_target_blocks"] == 1


def test_forge_mode_leaves_other_protocols_and_the_upstream_client_untouched(tmp_path):
    a, _b, sk, spk = _chain_of_two()
    forge = block_forge.ForgeSubstitution(kes_sign_key=sk, slots_per_kes_period=spk, transactions=[CRAFTED], armed_slot=0)
    state = proxy.ProxyState(upstream_host="x", upstream_port=1, output_dir=tmp_path, mutation_mode="forge_substitute",
                             mutation_direction="both", mutation_protocol="any", mutate_after_segments=1, forge=forge)
    word_responder = 2 | proxy.RESPONDER_PROTOCOL_BIT
    assert proxy._forge_rewrites(state, direction="inbound", protocol_word=word_responder)
    assert not proxy._forge_rewrites(state, direction="inbound", protocol_word=2)       # upstream's own client
    assert not proxy._forge_rewrites(state, direction="outbound", protocol_word=word_responder)
    assert proxy._forge_rewrites(state, direction="outbound", protocol_word=3)
    assert not proxy._forge_rewrites(state, direction="inbound", protocol_word=8 | proxy.RESPONDER_PROTOCOL_BIT)
