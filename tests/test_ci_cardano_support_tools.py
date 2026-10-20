"""Focused contracts for the checksum-pinned hosted Cardano support tools."""
import hashlib
import io
import tarfile

import pytest

from scripts import ci_cardano_support_tools as api


def archive_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, body in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(body)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(body))
    return output.getvalue()


def test_install_extracts_only_required_regular_binaries(tmp_path, monkeypatch):
    payload = archive_bytes({
        "./bin/cardano-testnet": b"testnet",
        "./bin/cardano-cli": b"cli",
        "./bin/cardano-node": b"node",
        "./share/ignored": b"ignored",
    })
    monkeypatch.setitem(api.ARCHIVE_SHA256, "amd64", hashlib.sha256(payload).hexdigest())

    destination = tmp_path / "support"
    receipt = api.install_cardano_support_tools(
        destination, architecture="amd64", fetch=lambda _url: payload
    )

    assert sorted(path.name for path in destination.iterdir()) == [
        "cardano-cli", "cardano-node", "cardano-testnet", "receipt.json"
    ]
    assert (destination / "cardano-cli").read_bytes() == b"cli"
    assert (destination / "cardano-cli").stat().st_mode & 0o777 == 0o755
    assert receipt["archive_sha256"] == hashlib.sha256(payload).hexdigest()
    assert set(receipt["binaries"]) == {"cardano-cli", "cardano-node", "cardano-testnet"}


def test_install_rejects_archive_checksum_mismatch(tmp_path):
    destination = tmp_path / "support"
    with pytest.raises(ValueError, match="checksum mismatch"):
        api.install_cardano_support_tools(
            destination, architecture="amd64", fetch=lambda _url: b"changed"
        )
    assert not destination.exists()
