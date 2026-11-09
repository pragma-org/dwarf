"""F130: the mux proxy injects only the named message and records who closed each connection.

Before: every mode flipped the last byte of the first segment of ANY protocol (usually the
handshake reply), an idle second (1 s socket timeout) made the proxy itself tear the connection
down, and nothing recorded which side closed a connection. "Rejected" was then inferred from
"node healthy + any traffic".
"""
from __future__ import annotations

import json
import socket
import threading
import time

from scripts import byzantine_mux_proxy as proxy

HANDSHAKE_REPLY = bytes.fromhex("83010a84182af400f4")          # protocol 0
MSG_BLOCK = bytes.fromhex("8204") + bytes.fromhex("4401020304")  # [4, h'01020304']


def _segment(payload: bytes, protocol: int, *, responder: bool) -> bytes:
    word = protocol | (proxy.RESPONDER_PROTOCOL_BIT if responder else 0)
    return (0).to_bytes(4, "big") + word.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


def _recv_segment(sock: socket.socket) -> bytes:
    head = b""
    while len(head) < 8:
        head += sock.recv(8 - len(head))
    length = int.from_bytes(head[6:8], "big")
    body = b""
    while len(body) < length:
        body += sock.recv(length - len(body))
    return head + body


def _start(tmp_path, upstream_script, **state_kwargs):
    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def serve():
        conn, _ = upstream.accept()
        with conn:
            upstream_script(conn)

    threading.Thread(target=serve, daemon=True).start()
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=upstream.getsockname()[1], output_dir=tmp_path,
                             mutation_direction="inbound", mutate_after_segments=1, **state_kwargs)
    server = proxy.ThreadedMuxProxy(("127.0.0.1", 0), proxy.MuxProxyHandler, proxy_state=state)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return state, server, upstream


def _stats(tmp_path) -> dict:
    return json.loads((tmp_path / "proxy-stats.json").read_text())


def test_only_the_named_message_is_mutated_and_the_downstream_close_is_recorded(tmp_path):
    def upstream_script(conn):
        conn.sendall(_segment(HANDSHAKE_REPLY, 0, responder=True))
        conn.sendall(_segment(MSG_BLOCK, 3, responder=True))
        time.sleep(1.0)

    state, server, _ = _start(tmp_path, upstream_script, mutation_mode="flip_payload_byte",
                              mutation_protocol="3", mutation_message="block")
    try:
        with socket.create_connection(server.server_address) as node:
            assert _recv_segment(node)[8:] == HANDSHAKE_REPLY
            block = _recv_segment(node)[8:]
            assert block != MSG_BLOCK and block[:-1] == MSG_BLOCK[:-1]
        deadline = time.time() + 5
        while time.time() < deadline and not _stats(tmp_path).get("connections"):
            time.sleep(0.05)
    finally:
        server.shutdown()
    [record] = _stats(tmp_path)["connections"]
    assert record["mutated_segments"] == 1
    assert record["closed_by"] == "downstream"
    assert record["drop_seconds"] is not None and record["drop_seconds"] >= 0


def test_an_idle_connection_is_not_torn_down_by_the_proxy(tmp_path):
    def upstream_script(conn):
        time.sleep(2.5)
        conn.sendall(_segment(HANDSHAKE_REPLY, 0, responder=True))
        time.sleep(1.0)

    state, server, _ = _start(tmp_path, upstream_script, mutation_mode="pass_through", mutation_protocol="any")
    try:
        with socket.create_connection(server.server_address) as node:
            node.settimeout(5)
            assert _recv_segment(node)[8:] == HANDSHAKE_REPLY
    finally:
        server.shutdown()


def test_upstream_close_is_attributed_to_the_upstream(tmp_path):
    def upstream_script(conn):
        conn.sendall(_segment(HANDSHAKE_REPLY, 0, responder=True))

    state, server, _ = _start(tmp_path, upstream_script, mutation_mode="pass_through", mutation_protocol="any")
    try:
        with socket.create_connection(server.server_address) as node:
            node.settimeout(5)
            _recv_segment(node)
            assert node.recv(1) == b""
        deadline = time.time() + 5
        while time.time() < deadline and not _stats(tmp_path).get("connections"):
            time.sleep(0.05)
    finally:
        server.shutdown()
    [record] = _stats(tmp_path)["connections"]
    assert record["closed_by"] == "upstream" and record["mutated_segments"] == 0 and record["drop_seconds"] is None
