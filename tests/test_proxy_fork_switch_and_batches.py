"""F144: the mux proxy measures per message and injects a real ChainSync fork switch.

Live 33822761: the fork-switch mode flipped the last byte of the first post-intersect RollBackward
and the step read the proxy after 6 messages; the only "rollback then forward" counted was the
normal session start. Live 3d9f5d98: the proxy classified only the first message of each segment,
so coalesced StartBatch/Block/BatchDone were undercounted and the range-pressure peak was a constant.
Live 37117ba3: the Praos header probe was a fail-closed stub.
"""
from __future__ import annotations

import hashlib

import cbor2

from scripts import byzantine_mux_proxy as proxy


def _segment(payload: bytes, protocol: int, *, responder: bool) -> bytes:
    word = protocol | (proxy.RESPONDER_PROTOCOL_BIT if responder else 0)
    return (0).to_bytes(4, "big") + word.to_bytes(2, "big") + len(payload).to_bytes(2, "big") + payload


def _payloads(segments: list[bytes]) -> list[bytes]:
    """Reassemble the per-message payloads carried by a list of mux segments (one protocol)."""
    buffer = bytearray()
    for segment in segments:
        buffer.extend(segment[8:])
    return proxy.split_messages(buffer)


def _header(block_no: int, slot: int, prev: bytes | None) -> tuple[bytes, bytes]:
    header = [[block_no, slot, prev, b"issuer", b"vrf"], b"\x07" * 32]
    raw = cbor2.dumps(header)
    return raw, hashlib.blake2b(raw, digest_size=32).digest()


def _roll_forward(block_no: int, slot: int, prev: bytes | None, tip_slot: int = 10_000) -> tuple[bytes, bytes]:
    raw, digest = _header(block_no, slot, prev)
    return cbor2.dumps([2, [6, cbor2.CBORTag(24, raw)], [[tip_slot, b"\xee" * 32], 999]]), digest


REQUEST_NEXT = cbor2.dumps([0])
AWAIT_REPLY = cbor2.dumps([1])


# --------------------------------------------------------------------------- CBOR splitting

def test_cbor_item_end_reports_complete_and_incomplete_items():
    item = cbor2.dumps([2, [6, cbor2.CBORTag(24, b"x" * 300)], [[5, b"\x01" * 32], 3]])
    assert proxy.cbor_item_end(item, 0) == len(item)
    assert proxy.cbor_item_end(item[:-1], 0) is None
    assert proxy.cbor_item_end(b"\x9f\x01\x02\xff", 0) == 4


def test_split_messages_keeps_a_partial_tail_buffered():
    a, b = cbor2.dumps([1]), cbor2.dumps([4, b"y" * 40])
    buffer = bytearray(a + b[:10])
    assert proxy.split_messages(buffer) == [a]
    buffer.extend(b[10:])
    assert proxy.split_messages(buffer) == [b]
    assert buffer == bytearray()


# --------------------------------------------------------------------------- block-fetch batches

def test_coalesced_batch_messages_are_all_counted_and_the_batch_peak_measured(tmp_path):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="pass_through", mutation_direction="both",
                             mutation_protocol="any", mutate_after_segments=999999)
    record = state.note_connection()
    request = cbor2.dumps([0, [100, b"\x01" * 32], [130, b"\x02" * 32]])
    proxy._mutate_segment(_segment(request, 3, responder=False), state=state, direction="outbound", record=record)
    block = cbor2.dumps([4, cbor2.CBORTag(24, b"B" * 200)])
    batch = cbor2.dumps([2]) + block + block + block
    proxy._mutate_segment(_segment(batch, 3, responder=True), state=state, direction="inbound", record=record)
    # The last block is split across two segments: counted once, when complete.
    tail = block + cbor2.dumps([5])
    proxy._mutate_segment(_segment(tail[:50], 3, responder=True), state=state, direction="inbound", record=record)
    assert state.stats["blocks_fetched"] == 3
    proxy._mutate_segment(_segment(tail[50:], 3, responder=True), state=state, direction="inbound", record=record)
    assert state.stats["blocks_fetched"] == 4
    assert state.stats["block_range_requests_observed"] == 1
    [measured] = state.stats["blockfetch_batches"]
    assert measured["blocks"] == 4 and (measured["from_slot"], measured["to_slot"]) == (100, 130)
    assert state.stats["peak_blocks_per_batch"] == 4
    assert state.stats["peak_outstanding_range_requests"] == 1


def test_no_batch_measured_without_traffic(tmp_path):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="pass_through", mutation_direction="both",
                             mutation_protocol="any", mutate_after_segments=999999)
    assert state.stats["peak_blocks_per_batch"] is None
    assert state.stats["blockfetch_batches"] == []


# --------------------------------------------------------------------------- fork switch

class _Wire:
    def __init__(self):
        self.down: list[bytes] = []
        self.up: list[bytes] = []


def _fork_session(tmp_path, depth=2):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="chainsync_fork_switch_once", mutation_direction="inbound",
                             mutation_protocol="2", mutate_after_segments=depth)
    record = state.note_connection()
    wire = _Wire()
    session = proxy.ForkSwitchSession(state, record, send_downstream=wire.down.append, send_upstream=wire.up.append)
    return state, record, wire, session


def _client(session, payload):
    session.on_segment("outbound", _segment(payload, 2, responder=False))


def _server(session, payload):
    session.on_segment("inbound", _segment(payload, 2, responder=True))


def _establish(session):
    anchor = [50, b"\xaa" * 32]
    _client(session, cbor2.dumps([4, [anchor]]))
    _server(session, cbor2.dumps([5, anchor, [[60, b"\xee" * 32], 9]]))
    _client(session, REQUEST_NEXT)
    _server(session, cbor2.dumps([3, anchor, [[60, b"\xee" * 32], 9]]))
    return anchor


def test_the_fork_switch_rolls_back_to_an_earlier_point_and_replays_genuine_headers(tmp_path):
    state, record, wire, session = _fork_session(tmp_path, depth=2)
    anchor = _establish(session)
    prev = b"\xaa" * 32
    headers = []
    for i in range(3):
        _client(session, REQUEST_NEXT)
        payload, digest = _roll_forward(10 + i, 100 + 10 * i, prev)
        headers.append(payload)
        _server(session, payload)
        prev = digest
    delivered = _payloads(wire.down)
    # intersect_found, the start rollback, h1, h2 -- then the injected rollback instead of h3
    assert delivered[:4] == [
        cbor2.dumps([5, anchor, [[60, b"\xee" * 32], 9]]), cbor2.dumps([3, anchor, [[60, b"\xee" * 32], 9]]),
        headers[0], headers[1]]
    rollback = cbor2.loads(delivered[4])
    assert rollback[0] == 3 and rollback[1] == anchor          # depth 2 from h2 = back to the anchor
    assert len(delivered) == 5
    # The follower asks again: the proxy answers from the replay store, not the upstream.
    forwarded_before = len(_payloads(wire.up))
    for expected in (headers[0], headers[1], headers[2]):
        _client(session, REQUEST_NEXT)
        assert _payloads(wire.down)[-1] == expected
    assert len(_payloads(wire.up)) == forwarded_before
    # The next request goes to the upstream again and its answer is relayed.
    _client(session, REQUEST_NEXT)
    assert len(_payloads(wire.up)) == forwarded_before + 1
    nxt, _ = _roll_forward(13, 130, prev)
    _server(session, nxt)
    assert _payloads(wire.down)[-1] == nxt
    [fs] = state.stats["fork_switches"]
    assert fs["rollback_depth"] == 2 and fs["rollback_point"]["slot"] == 50
    assert fs["rollback_delivered_at"] is not None and fs["displaced_delivered_at"] is not None
    assert fs["replays_delivered"] == 2 and fs["swallowed_requests"] == 3
    assert fs["requests_after_rollback"] == 4 and fs["requests_after_displaced"] == 1
    assert fs["forwards_after_rollback"] == 4
    assert record["mutated_segments"] == 1


def test_pipelined_requests_stay_balanced(tmp_path):
    state, _record, wire, session = _fork_session(tmp_path, depth=1)
    anchor = _establish(session)
    prev = b"\xaa" * 32
    headers = []
    for i in range(5):
        payload, prev = _roll_forward(10 + i, 100 + 10 * i, prev)
        headers.append(payload)
    _client(session, REQUEST_NEXT)
    _server(session, headers[0])
    for _ in range(3):
        _client(session, REQUEST_NEXT)           # three pipelined requests
    _server(session, headers[1])                 # injection: roll back one header (h1) to the anchor
    _server(session, AWAIT_REPLY)
    _server(session, headers[2])
    _server(session, headers[3])
    delivered = _payloads(wire.down)
    rollback = cbor2.loads(delivered[3])
    assert rollback[0] == 3 and rollback[1] == anchor
    # credit 3: the rollback, the replayed h1, the displaced h2; the await and h3, h4 wait for requests
    assert delivered[4:] == [headers[0], headers[1]]
    _client(session, REQUEST_NEXT)               # swallowed: answered with the queued await + h3
    _client(session, REQUEST_NEXT)               # swallowed: answered with h4
    assert _payloads(wire.down)[-3:] == [AWAIT_REPLY, headers[2], headers[3]]
    client_requests = len([p for p in _payloads(wire.up)]) + state.stats["fork_switches"][0]["swallowed_requests"]
    terminal_replies = sum(1 for p in _payloads(wire.down)
                           if proxy.classify_segment_message(protocol=2, payload=p) != "await_reply")
    assert state.stats["fork_switches"][0]["swallowed_requests"] == 2
    assert terminal_replies == client_requests


def test_the_other_direction_of_chainsync_is_relayed_untouched(tmp_path):
    state, _record, wire, session = _fork_session(tmp_path)
    assert not session.handles("inbound", proxy.parse_mux_segment_header(_segment(REQUEST_NEXT, 2, responder=False)[:8]))
    assert session.handles("inbound", proxy.parse_mux_segment_header(_segment(REQUEST_NEXT, 2, responder=True)[:8]))
    assert not session.handles("outbound", proxy.parse_mux_segment_header(_segment(REQUEST_NEXT, 3, responder=False)[:8]))


def test_no_fork_switch_without_enough_followed_headers(tmp_path):
    state, _record, wire, session = _fork_session(tmp_path, depth=2)
    _establish(session)
    _client(session, REQUEST_NEXT)
    payload, _ = _roll_forward(10, 100, b"\xaa" * 32)
    _server(session, payload)
    assert state.stats["fork_switches"] == []
    assert _payloads(wire.down)[-1] == payload


# --------------------------------------------------------------------------- invalid Praos header

def test_header_signature_corruption_changes_only_the_header_signature(tmp_path):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="corrupt_header_signature", mutation_direction="inbound",
                             mutation_protocol="2", mutation_message="roll_forward", mutate_after_segments=2)
    record = state.note_connection()
    prev = b"\x00" * 32
    outs = []
    for i in range(3):
        payload, prev = _roll_forward(i, 10 * i, prev)
        out, mutated = proxy._mutate_segment(_segment(payload, 2, responder=True), state=state,
                                             direction="inbound", record=record)
        outs.append((payload, out[8:], mutated))
    assert [m for _, _, m in outs] == [False, True, False]
    original, sent, _ = outs[1]
    before, after = cbor2.loads(original), cbor2.loads(sent)
    assert before[2] == after[2]                                  # tip untouched
    h_before, h_after = before[1][1].value, after[1][1].value
    assert len(h_before) == len(h_after) and h_before[:-1] == h_after[:-1] and h_before[-1] != h_after[-1]
    body_before, body_after = cbor2.loads(h_before), cbor2.loads(h_after)
    assert body_before[0] == body_after[0] and body_before[1] != body_after[1]   # header body intact, signature changed
    [c] = state.stats["header_corruptions"]
    assert c["original_header_hash"] == hashlib.blake2b(h_before, digest_size=32).hexdigest()
    assert c["corrupted_header_hash"] == hashlib.blake2b(h_after, digest_size=32).hexdigest()
    assert c["connection_id"] == record["id"] and c["slot"] == 10


def test_a_range_request_for_the_corrupted_header_is_recorded(tmp_path):
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=1, output_dir=tmp_path,
                             mutation_mode="corrupt_header_signature", mutation_direction="inbound",
                             mutation_protocol="2", mutation_message="roll_forward", mutate_after_segments=1)
    record = state.note_connection()
    payload, _ = _roll_forward(1, 10, b"\x00" * 32)
    proxy._mutate_segment(_segment(payload, 2, responder=True), state=state, direction="inbound", record=record)
    [c] = state.stats["header_corruptions"]
    bad = bytes.fromhex(c["corrupted_header_hash"])
    request = cbor2.dumps([0, [10, bad], [10, bad]])
    proxy._mutate_segment(_segment(request, 3, responder=False), state=state, direction="outbound", record=record)
    assert state.stats["header_corruptions"][0]["block_requested"] is True


def test_the_fork_switch_runs_over_real_sockets(tmp_path):
    import socket
    import threading
    import time

    def recv_segment(sock):
        head = b""
        while len(head) < 8:
            head += sock.recv(8 - len(head))
        length = int.from_bytes(head[6:8], "big")
        body = b""
        while len(body) < length:
            body += sock.recv(length - len(body))
        return head + body

    anchor = [50, b"\xaa" * 32]
    tip = [[60, b"\xee" * 32], 9]
    prev, headers = b"\xaa" * 32, []
    for i in range(4):
        payload, prev = _roll_forward(10 + i, 100 + 10 * i, prev)
        headers.append(payload)
    replies = [cbor2.dumps([5, anchor, tip]), cbor2.dumps([3, anchor, tip]), *headers]
    upstream_requests = []

    upstream = socket.socket()
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)

    def serve():
        conn, _ = upstream.accept()
        with conn:
            for reply in replies:
                upstream_requests.append(recv_segment(conn)[8:])
                conn.sendall(_segment(reply, 2, responder=True))
            time.sleep(1.0)

    threading.Thread(target=serve, daemon=True).start()
    state = proxy.ProxyState(upstream_host="127.0.0.1", upstream_port=upstream.getsockname()[1], output_dir=tmp_path,
                             mutation_mode="chainsync_fork_switch_once", mutation_direction="inbound",
                             mutation_protocol="2", mutate_after_segments=2)
    server = proxy.ThreadedMuxProxy(("127.0.0.1", 0), proxy.MuxProxyHandler, proxy_state=state)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    received = []
    try:
        with socket.create_connection(server.server_address) as node:
            node.settimeout(5)
            node.sendall(_segment(cbor2.dumps([4, [anchor]]), 2, responder=False))
            received.append(recv_segment(node)[8:])
            for _ in range(8):
                node.sendall(_segment(REQUEST_NEXT, 2, responder=False))
                received.append(recv_segment(node)[8:])
    finally:
        server.shutdown()
    assert received[:4] == replies[:4]
    assert cbor2.loads(received[4])[:2] == [3, anchor]
    assert received[5:] == [headers[0], headers[1], headers[2], headers[3]]
    assert len(upstream_requests) == len(replies)            # the replayed answers never reached the upstream
    [fs] = state.stats["fork_switches"]
    assert fs["requests_after_displaced"] == 1 and fs["replays_delivered"] == 2
