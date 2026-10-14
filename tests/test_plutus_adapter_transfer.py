"""Supported transfer of already-qualified Plutus conformance adapters."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from profile_manager.measurement_targets import resolve_plutus_conformance_adapter
from profile_manager.measurement_targets import plutus_conformance_adapter_provenance
from scripts import transfer_measurement_target as transfer


REVISION = "a" * 40
ADAPTER_SET = "b" * 64
ELF_AMD64 = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 10 + b"\x3e\x00"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qualified_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    package = tmp_path / "package" / "dwarf"
    source = (
        package
        / "targets"
        / "amaru"
        / "conformance-adapters"
        / REVISION
    )
    (source / "plutus" / "src").mkdir(parents=True)
    cargo = source / "plutus" / "Cargo.toml"
    rust = source / "plutus" / "src" / "main.rs"
    cargo.write_text("[package]\nname='qualified'\n", encoding="utf-8")
    rust.write_text("fn main() {}\n", encoding="utf-8")
    rows = [
        {"path": "plutus/Cargo.toml", "sha256": _sha(cargo)},
        {"path": "plutus/src/main.rs", "sha256": _sha(rust)},
    ]
    manifest = {
        "schema_version": 1,
        "kind": "production-plutus-v2-conformance",
        "implementation": "amaru",
        "source": {"revision": REVISION},
        "measurement_boundary": "production-plutus-v2-vm-only",
        "adapter_set_sha256": ADAPTER_SET,
        "adapter_files": rows,
    }
    (source / "plutus-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    registry = tmp_path / "source-registry"
    record_dir = registry / "amaru" / "plutus-conformance" / REVISION
    record_dir.mkdir(parents=True)
    executable = tmp_path / "qualified-amaru-adapter"
    executable.write_bytes(ELF_AMD64 + b"qualified production adapter\n")
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


def test_real_shape_export_import_resolves_exact_adapter(tmp_path):
    package, source_registry, source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "adapter.tar.gz"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id="install-source",
        bundle_dir=bundle,
    )

    destination = tmp_path / "destination-registry"
    destination.mkdir()
    published = transfer.import_conformance_adapter_bundle(
        bundle_dir=bundle,
        registry_root=destination,
        package_root=package,
    )
    resolved = resolve_plutus_conformance_adapter(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=destination,
        host_os="linux",
        host_arch="amd64",
    )

    assert Path(published["record_path"]) == Path(resolved["record_path"])
    assert resolved["executable_sha256"] == json.loads(
        source_record.read_text(encoding="utf-8")
    )["executable_sha256"]
    assert Path(resolved["executable"]).is_file()
    assert Path(resolved["executable"]).parent == Path(published["record_path"]).parent
    assert resolved["transfer_source_installation_id"] == "install-source"
    assert resolved["qualification_record_sha256"].startswith("sha256:")
    assert resolved["transfer_manifest_sha256"].startswith("sha256:")
    assert resolved["platform"] == "linux/amd64"
    assert not Path(
        json.loads(Path(published["record_path"]).read_text(encoding="utf-8"))[
            "executable"
        ]
    ).is_absolute()
    with pytest.raises(ValueError, match="requires linux/amd64"):
        resolve_plutus_conformance_adapter(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=destination,
            host_os="linux",
            host_arch="arm64",
        )
    qualification = Path(published["record_path"]).with_suffix(".qualification.json")
    assert qualification.read_bytes() == source_record.read_bytes()
    repeated = transfer.import_conformance_adapter_bundle(
        bundle_dir=bundle,
        registry_root=destination,
        package_root=package,
    )
    assert repeated == published

    published_record = Path(published["record_path"])
    tampered = json.loads(published_record.read_text(encoding="utf-8"))
    tampered.pop("transfer_manifest_sha256")
    published_record.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="transfer manifest sha256 is not immutable"):
        resolve_plutus_conformance_adapter(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=destination,
            host_os="linux",
            host_arch="amd64",
        )


def test_legacy_qualified_export_import_retains_explicit_provenance_without_installation_id(
    tmp_path,
):
    package, source_registry, source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "legacy-adapter-bundle"

    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id=None,
        legacy_qualified_source=True,
        bundle_dir=bundle,
    )

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == {"provenance": "legacy-qualified"}
    destination = tmp_path / "destination-registry"
    destination.mkdir()
    published = transfer.import_conformance_adapter_bundle(
        bundle_dir=bundle,
        registry_root=destination,
        package_root=package,
    )
    resolved = resolve_plutus_conformance_adapter(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=destination,
        host_os="linux",
        host_arch="amd64",
    )

    assert resolved["transfer_source_provenance"] == "legacy-qualified"
    assert "transfer_source_installation_id" not in resolved
    assert (destination / resolved["qualification_record"]).read_bytes() == (
        source_record.read_bytes()
    )
    assert resolved["qualification_record_sha256"].startswith("sha256:")
    assert resolved["transfer_manifest_sha256"].startswith("sha256:")
    assert plutus_conformance_adapter_provenance(resolved)[
        "transfer_source_provenance"
    ] == "legacy-qualified"
    assert Path(published["record_path"]) == Path(resolved["record_path"])


def test_legacy_qualified_export_rejects_any_claimed_source_installation(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)

    with pytest.raises(
        transfer.TransferError,
        match="legacy-qualified source must not claim an installation identity",
    ):
        transfer.export_conformance_adapter_bundle(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=source_registry,
            package_root=package,
            source_installation_id="install-invented",
            legacy_qualified_source=True,
            bundle_dir=tmp_path / "legacy-adapter-bundle",
        )


def test_legacy_qualified_import_rejects_mixed_installation_provenance(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "legacy-adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id=None,
        legacy_qualified_source=True,
        bundle_dir=bundle,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["installation_id"] = "install-invented"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    destination = tmp_path / "destination-registry"
    destination.mkdir()
    with pytest.raises(transfer.TransferError, match="provenance is inconsistent"):
        transfer.import_conformance_adapter_bundle(
            bundle_dir=bundle,
            registry_root=destination,
            package_root=package,
        )
    assert list(destination.iterdir()) == []


def test_installation_owned_import_rejects_extra_source_provenance_keys(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id="install-source",
        bundle_dir=bundle,
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"]["legacy_origin"] = "untrusted"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")

    destination = tmp_path / "destination-registry"
    destination.mkdir()
    with pytest.raises(transfer.TransferError, match="source provenance is malformed"):
        transfer.import_conformance_adapter_bundle(
            bundle_dir=bundle,
            registry_root=destination,
            package_root=package,
        )
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transfer_manifest_sha256", ""),
        ("transfer_source_installation_id", "install-stale"),
        ("transfer_source_provenance", "installation-owned"),
        ("qualification_record", "stale.json"),
    ],
)
def test_legacy_export_rejects_any_stale_transfer_field(tmp_path, field, value):
    package, source_registry, source_record = _qualified_fixture(tmp_path)
    record = json.loads(source_record.read_text(encoding="utf-8"))
    record[field] = value
    source_record.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(transfer.TransferError, match="re-export"):
        transfer.export_conformance_adapter_bundle(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=source_registry,
            package_root=package,
            source_installation_id=None,
            legacy_qualified_source=True,
            bundle_dir=tmp_path / "legacy-adapter-bundle",
        )


def test_import_rejects_tampered_executable_and_unsafe_member(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "adapter.tar.gz"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id="install-source",
        bundle_dir=bundle,
    )
    (bundle / "unsafe-link").symlink_to(tmp_path / "escape")

    destination = tmp_path / "destination-registry"
    destination.mkdir()
    with pytest.raises(transfer.TransferError, match="symlink|exact"):
        transfer.import_conformance_adapter_bundle(
            bundle_dir=bundle,
            registry_root=destination,
            package_root=package,
        )
    assert list(destination.iterdir()) == []


def test_export_rejects_qualification_source_drift(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)
    source = (
        package
        / "targets"
        / "amaru"
        / "conformance-adapters"
        / REVISION
        / "plutus"
        / "src"
        / "main.rs"
    )
    source.write_text("fn main() { panic!() }\n", encoding="utf-8")

    with pytest.raises(transfer.TransferError, match="adapter source digest"):
        transfer.export_conformance_adapter_bundle(
            implementation="amaru",
            source_revision=REVISION,
            adapter_set_sha256=ADAPTER_SET,
            registry_root=source_registry,
            package_root=package,
            source_installation_id="install-source",
            bundle_dir=tmp_path / "adapter-bundle",
        )


def test_import_rejects_symlinked_registry_ancestor(tmp_path):
    package, source_registry, _source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id="install-source",
        bundle_dir=bundle,
    )
    destination = tmp_path / "destination-registry"
    destination.mkdir()
    escape = tmp_path / "escape"
    escape.mkdir()
    (destination / "amaru").symlink_to(escape, target_is_directory=True)

    with pytest.raises(transfer.TransferError, match="must not be a symlink"):
        transfer.import_conformance_adapter_bundle(
            bundle_dir=bundle,
            registry_root=destination,
            package_root=package,
        )
    assert list(escape.iterdir()) == []


def test_import_publishes_only_the_private_snapshot_when_caller_bundle_changes(
    tmp_path, monkeypatch
):
    package, source_registry, source_record = _qualified_fixture(tmp_path)
    bundle = tmp_path / "adapter-bundle"
    transfer.export_conformance_adapter_bundle(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=source_registry,
        package_root=package,
        source_installation_id="install-source",
        bundle_dir=bundle,
    )
    original_validate = transfer._validate_conformance_bundle
    mutated = False

    def validate_snapshot(snapshot):
        nonlocal mutated
        if not mutated:
            mutated = True
            (bundle / "adapter-executable").write_bytes(ELF_AMD64 + b"replaced")
        return original_validate(snapshot)

    monkeypatch.setattr(transfer, "_validate_conformance_bundle", validate_snapshot)
    destination = tmp_path / "destination-registry"
    destination.mkdir()
    result = transfer.import_conformance_adapter_bundle(
        bundle_dir=bundle,
        registry_root=destination,
        package_root=package,
    )
    resolved = resolve_plutus_conformance_adapter(
        implementation="amaru",
        source_revision=REVISION,
        adapter_set_sha256=ADAPTER_SET,
        registry_root=destination,
        host_os="linux",
        host_arch="amd64",
    )
    assert result["record_path"] == resolved["record_path"]
    assert resolved["executable_sha256"] == json.loads(
        source_record.read_text(encoding="utf-8")
    )["executable_sha256"]
