"""F84: a substrate checkpoint must not crash on unix sockets inside node directories."""
from __future__ import annotations

import io
import socket
import tarfile
from pathlib import Path

from scripts import runtime_substrate_checkpoint as checkpoint


def test_directory_checkpoint_skips_unix_sockets_and_records_them(tmp_path):
    root = tmp_path / "node1"
    (root / "db").mkdir(parents=True)
    (root / "db" / "state.bin").write_bytes(b"abc")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(root / "sock"))
    try:
        buffer = io.BytesIO()
        entries = []
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            count = checkpoint._add_directory_to_tar(archive, source_root=root, archive_root="node1", manifest_entries=entries)
        names = [e["path"] for e in entries]
        assert "node1/db/state.bin" in names
        skipped = [e for e in entries if e.get("kind") == "skipped"]
        assert skipped and skipped[0]["path"] == "node1/sock" and "socket" in skipped[0]["reason"]
        assert count == len([e for e in entries if e.get("kind") != "skipped"])
    finally:
        sock.close()
