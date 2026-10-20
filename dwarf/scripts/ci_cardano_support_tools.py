#!/usr/bin/env python3
"""Install checksum-pinned Cardano tools needed by hosted Docker devnets."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
from pathlib import Path
import tarfile
import urllib.request


VERSION = "11.1.2"
ARCHIVE_SHA256 = {
    "amd64": "fd872fbeb9cc663e67088ca47ed01aa01d18a77d7dbee694e2444bb23d833759",
    "arm64": "24565d796441111b509a8ddd8be2ebf7d5d6ff363bb9fc447af1f4e9e71a3246",
}
REQUIRED_BINARIES = ("cardano-testnet", "cardano-cli", "cardano-node")


def _architecture(value: str | None = None) -> str:
    name = (value or platform.machine()).lower()
    aliases = {"x86_64": "amd64", "aarch64": "arm64"}
    name = aliases.get(name, name)
    if name not in ARCHIVE_SHA256:
        raise ValueError(f"unsupported Cardano support architecture: {name}")
    return name


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def install_cardano_support_tools(
    directory: Path | str, *, architecture: str | None = None, fetch=_download
) -> dict:
    arch = _architecture(architecture)
    archive_name = f"cardano-node-{VERSION}-linux-{arch}.tar.gz"
    url = f"https://github.com/IntersectMBO/cardano-node/releases/download/{VERSION}/{archive_name}"
    archive = fetch(url)
    observed = hashlib.sha256(archive).hexdigest()
    expected = ARCHIVE_SHA256[arch]
    if observed != expected:
        raise ValueError(
            f"Cardano support archive checksum mismatch: expected {expected}, observed {observed}"
        )

    extracted: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        for binary in REQUIRED_BINARIES:
            matches = [
                member for member in bundle.getmembers()
                if member.name.lstrip("./") == f"bin/{binary}"
            ]
            if len(matches) != 1 or not matches[0].isfile():
                raise ValueError(f"archive lacks one regular support binary: {binary}")
            stream = bundle.extractfile(matches[0])
            if stream is None:
                raise ValueError(f"archive support binary is unreadable: {binary}")
            extracted[binary] = stream.read()

    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=False)
    binaries = {}
    for binary, body in extracted.items():
        path = destination / binary
        path.write_bytes(body)
        path.chmod(0o755)
        binaries[binary] = {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    receipt = {
        "schema_version": "v1",
        "kind": "cardano-hosted-support-tools",
        "version": VERSION,
        "architecture": arch,
        "archive": archive_name,
        "archive_sha256": observed,
        "binaries": binaries,
    }
    (destination / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--architecture", choices=sorted(ARCHIVE_SHA256))
    args = parser.parse_args(argv)
    receipt = install_cardano_support_tools(args.directory, architecture=args.architecture)
    print(f"installed Cardano support {receipt['version']} for {receipt['architecture']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
