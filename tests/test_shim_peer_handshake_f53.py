"""F53: the shim-peer malformed-frame primitives negotiate a real session first.
A refused handshake (the node's answer to the old version-10 proposal) used to
count as a data response, so the node's close after the refusal looked like a
rejection of the malformed frame: a vacuous pass."""
import socket
import struct
import threading
from pathlib import Path

import pytest

from profile_manager import primitives


class _Node(threading.Thread):
    def __init__(self, reply_hex):
        super().__init__(daemon=True)
        self.server = socket.socket(); self.server.bind(("127.0.0.1", 0)); self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.reply = bytes.fromhex(reply_hex)
        self.header_words = []; self.payloads = []

    def run(self):
        try:
            conn, _ = self.server.accept(); conn.settimeout(5)
            header = conn.recv(8); _ts, word = struct.unpack(">II", header)
            self.header_words.append(word); self.payloads.append(conn.recv(word & 0xFFFF))
            conn.sendall(struct.pack(">II", 0, (1 << 31) | len(self.reply)) + self.reply)
            try:
                header = conn.recv(8)
                if len(header) == 8:
                    _ts, word = struct.unpack(">II", header)
                    self.header_words.append(word); self.payloads.append(conn.recv(word & 0xFFFF))
            except OSError:
                pass
            conn.close()
        finally:
            self.server.close()


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir; self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


@pytest.mark.parametrize("cls,case", [(primitives.ShimPeerMalformedTxsubmission, "bad-requesttxids-shape"),
                                      (primitives.ShimPeerMalformedBlockfetch, "invalid-point-hash-short")])
def test_shim_peer_primitives_stop_at_a_refused_handshake(tmp_path, monkeypatch, cls, case):
    node = _Node("82028200820e0f")   # MsgRefuse VersionMismatch [14, 15]
    node.start()
    prim = cls(params={"malformation_id": case, "response_timeout_seconds": 2})
    monkeypatch.setattr(prim, "_runtime_node", lambda handle=None: (Path("/tmp/runtime.json"), "node1", {"port": node.port}))
    with pytest.raises(RuntimeError, match="not accepted; got refused"):
        prim.run(_Handle(tmp_path), None)
    node.join(5)
    assert node.payloads[0].hex() == "8200a30d84182af400f40e84182af400f40f84182af400f4"
    assert node.header_words[0] >> 31 == 0


def test_shim_peer_txsubmission_proceeds_after_an_accepted_handshake(tmp_path, monkeypatch):
    node = _Node("83010e84182af400f4")   # MsgAccept 14
    node.start()
    prim = primitives.ShimPeerMalformedTxsubmission(params={"malformation_id": "bad-requesttxids-shape", "response_timeout_seconds": 2})
    monkeypatch.setattr(prim, "_runtime_node", lambda handle=None: (Path("/tmp/runtime.json"), "node1", {"port": node.port}))
    prim.run(_Handle(tmp_path), None)
    node.join(5)
    assert len(node.payloads) == 2 and all(word >> 31 == 0 for word in node.header_words)
