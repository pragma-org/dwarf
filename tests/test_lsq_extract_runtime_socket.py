"""F116: the LSQ extract takes its node socket and network magic from runtime metadata.

The scenario hard-coded /tmp/ada2-testnet42-socket/node1.sock from the old host setup and had no
substrate (live: eac8e61b). With runtime_metadata_path + target_node the socket comes from the
composed substrate, through the short socket link (F94) the deep paths need.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from profile_manager import primitives
from scripts import runtime_common as rc


class _Handle:
    def __init__(self, run_dir):
        self.run_dir = run_dir
        self.logs = []

    def log(self, **entry):
        self.logs.append(entry)


def test_socket_and_magic_come_from_runtime_metadata(tmp_path, monkeypatch):
    socket_path = tmp_path / ("deep" * 30) / "env" / "socket" / "node1" / "sock"
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("")
    (tmp_path / "outputs" / "substrate-compose").mkdir(parents=True)
    (tmp_path / "outputs/substrate-compose/runtime.json").write_text(json.dumps(
        {"network_magic": 42, "nodes": [{"id": "node1", "name": "node1", "socket_path": str(socket_path)}]}))
    import shutil, tempfile
    short_dir = __import__("pathlib").Path(tempfile.mkdtemp(prefix="dsk", dir="/tmp"))
    monkeypatch.setattr(rc, "_SHORT_SOCKET_DIR", short_dir)
    seen = {}

    def build(**kwargs):
        seen.update(kwargs)
        return ["true"]

    monkeypatch.setattr(primitives, "build_runtime_cardano_lsq_extract_command", build)
    monkeypatch.setattr(primitives.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    prim = primitives.RuntimeCardanoLsqExtract(params={
        "runtime_metadata_path": "outputs/substrate-compose/runtime.json", "target_node": "node1", "network_magic": 7,
        "era": 6, "output_path": "outputs/lsq/debug-epoch-state.cbor", "expect_exit": 0})
    try:
        prim.run(_Handle(tmp_path), None)
    except Exception:  # noqa: BLE001 - only the command inputs matter here
        pass
    assert seen["network_magic"] == 42
    linked = __import__("pathlib").Path(seen["socket_path"])
    try:
        assert len(str(linked)) <= rc._UNIX_SOCKET_PATH_LIMIT and linked.resolve() == socket_path.resolve()
    finally:
        shutil.rmtree(short_dir, ignore_errors=True)
