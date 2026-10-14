"""Supported transfer of already-qualified production CBOR adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from profile_manager.measurement_targets import (
    cbor_conformance_adapter_provenance,
    resolve_cbor_conformance_adapter,
)
from scripts import transfer_measurement_target as transfer


REVISION = "a" * 40
ADAPTER_SET = "b" * 64
ELF_AMD64 = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 10 + b"\x3e\x00"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qualified_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    package = tmp_path / "package" / "dwarf"
    source = package / "targets" / "amaru" / "conformance-adapters" / REVISION
    (source / "cbor" / "src").mkdir(parents=True)
    cargo = source / "cbor" / "Cargo.toml"
    rust = source / "cbor" / "src" / "main.rs"
    cargo.write_text("[package]\nname='qualified-cbor'\n", encoding="utf-8")
    rust.write_text("fn main() {}\n", encoding="utf-8")
    rows = [
        {"path": "cbor/Cargo.toml", "sha256": _sha(cargo)},
        {"path": "cbor/src/main.rs", "sha256": _sha(rust)},
    ]
    manifest = {
        "schema_version": 1,
        "kind": "production-cbor-conformance",
        "implementation": "amaru",
        "source": {"revision": REVISION},
        "measurement_boundary": "production-codec-only",
        "adapter_set_sha256": ADAPTER_SET,
        "adapter_files": rows,
    }
    (source / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    registry = tmp_path / "source-registry"
    record_dir = registry / "amaru" / "conformance" / REVISION
    record_dir.mkdir(parents=True)
    executable = tmp_path / "qualified-amaru-cbor-adapter"
    executable.write_bytes(ELF_AMD64 + b"qualified production CBOR adapter\n")
    executable.chmod(0o755)
    record = {
        **manifest,
        "source_revision": REVISION,
        "source_release": "10.11.test",
        "executable": str(executable),
        "executable_sha256": _sha(executable),
        "build_result_sha256": "c" * 64,
        "build_log_sha256": "d" * 64,
    }
    record_path = record_dir / f"{ADAPTER_SET}.json"
    record_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    return package, registry, record_path


def test_real_shape_cbor_export_import_resolves_exact_adapter(tmp_path):
    package, source_registry, source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "cbor-adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id=None,
        legacy_qualified_source=True,
        bundle_dir=bundle,
        family="cbor",
    )
    assert json.loads((bundle / "manifest.json").read_text())["kind"] == (
        "dwarf-cbor-conformance-adapter-transfer"
    )

    destination = tmp_path / "destination-registry"
    destination.mkdir()
    published = transfer.import_conformance_adapter_bundle(
        bundle_dir=bundle,
        registry_root=destination,
        package_root=package,
        family="cbor",
    )
    resolved = resolve_cbor_conformance_adapter(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=destination,
        host_os="linux",
        host_arch="amd64",
    )

    assert Path(published["record_path"]) == Path(resolved["record_path"])
    assert Path(resolved["record_path"]).parent.name == REVISION
    assert Path(resolved["record_path"]).parent.parent.name == "conformance"
    assert resolved["executable_sha256"] == json.loads(source_record.read_text())[
        "executable_sha256"
    ]
    assert resolved["transfer_source_provenance"] == "legacy-qualified"
    assert resolved["platform"] == "linux/amd64"
    assert cbor_conformance_adapter_provenance(resolved)[
        "transfer_manifest_sha256"
    ].startswith("sha256:")
    with pytest.raises(ValueError, match="requires linux/amd64"):
        resolve_cbor_conformance_adapter(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=destination,
            host_os="linux",
            host_arch="arm64",
        )


def test_cbor_import_rejects_plutus_bundle_kind(tmp_path):
    package, source_registry, _ = _qualified_fixture(tmp_path)
    bundle = tmp_path / "cbor-adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id=None,
        legacy_qualified_source=True,
        bundle_dir=bundle,
        family="cbor",
    )
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["kind"] = transfer.CONFORMANCE_BUNDLE_KIND
    (bundle / "manifest.json").write_text(json.dumps(manifest) + "\n")
    destination = tmp_path / "destination-registry"
    destination.mkdir()
    with pytest.raises(transfer.TransferError, match="schema or kind"):
        transfer.import_conformance_adapter_bundle(
            bundle_dir=bundle,
            registry_root=destination,
            package_root=package,
            family="cbor",
        )
