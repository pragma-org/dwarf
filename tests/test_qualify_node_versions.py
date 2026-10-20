import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest

from scripts import qualify_node_versions as qualification
from scripts.qualify_node_versions import (
    CONTROL_AMARU_IMAGE,
    PROTECTED_PROJECTS,
    QualificationError,
    build_project_name,
    candidate_matrix,
    classify_log_signals,
    classify_qualification,
    classify_terminal_runtime_failure,
    exact_oci_reference,
    identity_matches,
    propose_catalog_update,
    transform_compose_model,
)


def _release(implementation, version, released_at, *, channel="stable", digest="1" * 64):
    repository = (
        "ghcr.io/intersectmbo/cardano-node"
        if implementation == "cardano-node"
        else "ghcr.io/pragma-org/amaru"
    )
    tag = version if implementation == "cardano-node" else f"v{version}"
    return {
        "implementation": implementation,
        "version": version,
        "channel": channel,
        "released_at": released_at,
        "source_revision": "a" * 40,
        "artifacts": [{
            "kind": "oci",
            "reference": f"{repository}:{tag}",
            "availability": "available",
            "digest": f"sha256:{digest}",
        }],
        "verification": {},
    }


def test_hosted_qualification_stages_trusted_bind_sources_for_docker_host(tmp_path):
    package = tmp_path / "trusted-package"
    package.mkdir()
    (package / "testnet.yaml").write_text("networkMagic: 42\n")
    (package / "amaru-runtime").mkdir()
    (package / "amaru-runtime" / "global-parameters.json").write_text("{}\n")
    local_assets = tmp_path / "state" / "qualification-assets"
    host_assets = Path("/host/runtime-state/version-qualifications/q/qualification-assets")
    model = {
        "services": {
            "configurator": {
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(package / "testnet.yaml"),
                        "target": "/testnet.yaml",
                    },
                    f"{package / 'amaru-runtime'}:/amaru-runtime:ro",
                    "p1-configs:/configs/1",
                ]
            }
        }
    }

    qualification._stage_host_bind_sources(
        model,
        base_package=package,
        local_assets=local_assets,
        docker_host_assets=host_assets,
    )

    volumes = model["services"]["configurator"]["volumes"]
    assert volumes[0]["source"] == str(host_assets / "testnet.yaml")
    assert volumes[1] == f"{host_assets / 'amaru-runtime'}:/amaru-runtime:ro"
    assert volumes[2] == "p1-configs:/configs/1"
    assert (local_assets / "testnet.yaml").read_text() == "networkMagic: 42\n"
    assert (local_assets / "amaru-runtime" / "global-parameters.json").is_file()


def _catalog():
    cardano_new = _release("cardano-node", "11.1.2", "2026-09-17T00:00:00Z", digest="1" * 64)
    cardano_old = _release("cardano-node", "10.7.1", "2026-04-15T00:00:00Z", digest="2" * 64)
    amaru_new = _release("amaru", "10.11.20260912", "2026-09-12T00:00:00Z", digest="3" * 64)
    amaru_old = _release("amaru", "10.11.0", "2026-09-03T00:00:00Z", digest="4" * 64)
    prerelease = _release("cardano-node", "12.0.0-rc1", "2026-09-18T00:00:00Z", channel="prerelease")
    return {
        "schema_version": 1,
        "updated_at": "2026-09-17T00:00:00Z",
        "releases": [cardano_old, prerelease, amaru_old, cardano_new, amaru_new],
        "compatibility_pairs": [],
    }


def _compose_model():
    cardano = {
        "image": "old-cardano",
        "container_name": "p1",
        "command": ["run"],
        "networks": {"default": None},
    }
    amaru = {
        "image": CONTROL_AMARU_IMAGE,
        "container_name": "amaru-relay-1",
        "entrypoint": "amaru-relay-bootstrap",
        "environment": {
            "RELAY_NAME": "amaru-relay-1",
            "AMARU_PEER": "p1.example:3001",
            "AMARU_NETWORK": "testnet_42",
        },
        "volumes": [
            {"type": "volume", "source": "p1-state", "target": "/live", "read_only": True},
            {"type": "volume", "source": "a1-state", "target": "/srv/amaru"},
        ],
        "networks": {"default": None, "amaru-consumer-net": None},
    }
    return {
        "services": {
            "configurator": {"image": "configurator", "container_name": "configurator"},
            "tracer": {"image": "tracer", "container_name": "tracer"},
            "tracer-sidecar": {"image": "tracer-sidecar", "container_name": "tracer-sidecar"},
            "log-tailer": {"image": "log-tailer", "container_name": "log-tailer"},
            "sidecar": {"image": "sidecar", "container_name": "sidecar"},
            "p1": dict(cardano),
            "p2": dict(cardano),
            "p3": dict(cardano),
            "relay1": dict(cardano),
            "relay2": dict(cardano),
            "amaru-consumer": {
                **cardano,
                "depends_on": {
                    "amaru-consumer-seed": {
                        "condition": "service_completed_successfully"
                    }
                },
            },
            "amaru-consumer-seed": {"image": CONTROL_AMARU_IMAGE, "container_name": "amaru-consumer-seed"},
            "amaru-relay-1": dict(amaru),
            "amaru-relay-2": {
                **amaru,
                "container_name": "amaru-relay-2",
                "environment": {
                    "RELAY_NAME": "amaru-relay-2",
                    "AMARU_PEER": "p2.example:3001",
                    "AMARU_NETWORK": "testnet_42",
                },
            },
        },
        "volumes": {"p1-state": {}, "a1-state": {}, "external": {"external": True}},
        "networks": {
            "default": {"name": "cardano-amaru-testnet", "driver": "bridge"},
            "amaru-consumer-net": {"name": "cardano-amaru-consumer-net", "driver": "bridge"},
        },
    }


def _extractor_container_command(transformed):
    command = transformed["services"]["amaru-target-extract"]["command"][0]
    assert "$$" in command
    return command.replace("$$", "$")


def test_candidate_matrix_is_newest_to_oldest_and_ignores_prereleases():
    catalog = _catalog()

    cardano = candidate_matrix(catalog, "cardano-only")
    amaru = candidate_matrix(catalog, "amaru-only")
    mixed = candidate_matrix(catalog, "mixed")

    assert [item["cardano_version"] for item in cardano] == ["11.1.2", "10.7.1"]
    assert [item["amaru_version"] for item in amaru] == ["10.11.20260912", "10.11.0"]
    assert mixed[0]["cardano_version"] == "11.1.2"
    assert mixed[0]["amaru_version"] == "10.11.20260912"
    assert mixed[-1]["cardano_version"] == "10.7.1"
    assert mixed[-1]["amaru_version"] == "10.11.0"


def test_exact_oci_reference_uses_digest_without_mutable_tag():
    release = _catalog()["releases"][-2]
    assert exact_oci_reference(release) == (
        "ghcr.io/intersectmbo/cardano-node@sha256:" + "1" * 64
    )


def test_identity_match_requires_both_node_version_and_exact_running_image():
    assert identity_matches(
        expected_version="11.1.2",
        reported_version="cardano-node 11.1.2\ngit rev " + "a" * 40,
        expected_image_id="sha256:" + "1" * 64,
        running_image_id="sha256:" + "1" * 64,
    ) is True
    assert identity_matches(
        expected_version="11.1.2",
        reported_version="cardano-node 11.1.2",
        expected_image_id="sha256:" + "1" * 64,
        running_image_id="sha256:" + "2" * 64,
    ) is False


def test_modern_amaru_identity_uses_extractor_artifact_and_target_binary(monkeypatch):
    target_image = "ghcr.io/pragma-org/amaru@sha256:" + "9" * 64
    target_image_id = "sha256:" + "a" * 64
    wrapper_image_id = "sha256:" + "b" * 64
    cardano_image_id = "sha256:" + "c" * 64
    containers = {
        "p1": "p1-container",
        "p2": "p2-container",
        "p3": "p3-container",
        "relay1": "relay1-container",
        "relay2": "relay2-container",
        "amaru-consumer": "consumer-container",
        "amaru-relay-1": "amaru-one",
        "amaru-relay-2": "amaru-two",
        "amaru-target-extract": "amaru-extractor",
    }
    monkeypatch.setattr(qualification, "_project_containers", lambda _project: containers)
    monkeypatch.setattr(
        qualification,
        "_image_id",
        lambda image: (
            target_image_id
            if image == target_image
            else wrapper_image_id
            if image == CONTROL_AMARU_IMAGE
            else cardano_image_id
        ),
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "inspect"]:
            container = args[2]
            image_id = (
                target_image_id
                if container == "amaru-extractor"
                else wrapper_image_id
                if container.startswith("amaru-")
                else cardano_image_id
            )
            return qualification.subprocess.CompletedProcess(args, 0, image_id + "\n", "")
        if args[:3] == ["docker", "exec", "amaru-one"] or args[:3] == ["docker", "exec", "amaru-two"]:
            if args[3] == "/target/amaru":
                return qualification.subprocess.CompletedProcess(
                    args, 0, "Amaru 10.11.20260912 (b159172)\n", ""
                )
            return qualification.subprocess.CompletedProcess(args, 127, "", "missing")
        if args[:2] == ["docker", "exec"]:
            return qualification.subprocess.CompletedProcess(
                args, 0, "cardano-node 10.7.1\n", ""
            )
        raise AssertionError(args)

    monkeypatch.setattr(qualification, "_run", fake_run)

    observation = qualification._identity_observation(
        "candidate",
        scope="mixed",
        cardano_version="10.7.1",
        amaru_version="10.11.20260912",
        cardano_image="cardano-image",
        amaru_image=target_image,
    )

    assert observation["matched"] is True
    amaru = observation["services"]["amaru-relay-1"]
    assert amaru["running_image_id"] == wrapper_image_id
    assert amaru["artifact_image_id"] == target_image_id
    assert amaru["artifact_container"] == "amaru-extractor"
    assert "/target/amaru" in amaru["version_command"]


def test_modern_stock_amaru_identity_uses_recorded_executable_digest(monkeypatch):
    target_image = "localhost:5000/dwarf/amaru-candidate@sha256:" + "9" * 64
    target_image_id = "sha256:" + "a" * 64
    wrapper_image_id = "sha256:" + "b" * 64
    cardano_image_id = "sha256:" + "c" * 64
    executable_digest = "sha256:" + "e" * 64
    containers = {
        "p1": "p1-container",
        "p2": "p2-container",
        "p3": "p3-container",
        "relay1": "relay1-container",
        "relay2": "relay2-container",
        "amaru-consumer": "consumer-container",
        "amaru-relay-1": "amaru-one",
        "amaru-relay-2": "amaru-two",
        "amaru-target-extract": "amaru-extractor",
    }
    monkeypatch.setattr(qualification, "_project_containers", lambda _project: containers)
    monkeypatch.setattr(
        qualification,
        "_image_id",
        lambda image: (
            target_image_id
            if image == target_image
            else wrapper_image_id
            if image == CONTROL_AMARU_IMAGE
            else cardano_image_id
        ),
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "inspect"]:
            container = args[2]
            image_id = (
                target_image_id
                if container == "amaru-extractor"
                else wrapper_image_id
                if container.startswith("amaru-")
                else cardano_image_id
            )
            return qualification.subprocess.CompletedProcess(args, 0, image_id + "\n", "")
        if args[:3] in (["docker", "exec", "amaru-one"], ["docker", "exec", "amaru-two"]):
            if args[3:5] == ["/target/amaru", "--version"]:
                return qualification.subprocess.CompletedProcess(
                    args, 0, "Amaru 10.11.0 (9e17196)\n", ""
                )
            if args[3:5] == ["sha256sum", "/target/amaru"]:
                return qualification.subprocess.CompletedProcess(
                    args, 0, "e" * 64 + "  /target/amaru\n", ""
                )
            return qualification.subprocess.CompletedProcess(args, 127, "", "missing")
        if args[:2] == ["docker", "exec"]:
            return qualification.subprocess.CompletedProcess(
                args, 0, "cardano-node 11.1.2\n", ""
            )
        raise AssertionError(args)

    monkeypatch.setattr(qualification, "_run", fake_run)
    observation = qualification._identity_observation(
        "candidate",
        scope="mixed",
        cardano_version="11.1.2",
        amaru_version="10.11.0",
        cardano_image="cardano-image",
        amaru_image=target_image,
        amaru_runtime_interface="extracted-binary",
        measurement_target_identity={
            "version": "10.11.0",
            "source_revision": "d" * 40,
            "target_mode": "stock",
            "image": target_image,
            "executable_digest": executable_digest,
        },
    )

    assert observation["matched"] is True
    relay = observation["services"]["amaru-relay-1"]
    assert relay["identity_basis"] == "recorded-stock-executable"
    assert relay["executable_digest_reported"] == executable_digest
    assert relay["executable_digest_matched"] is True


def test_operator_registered_stock_identity_uses_validated_content_id_not_label(
    monkeypatch,
):
    target_image = "sha256:" + "a" * 64
    wrapper_image_id = "sha256:" + "b" * 64
    cardano_image_id = "sha256:" + "c" * 64
    containers = {
        "p1": "p1-container",
        "p2": "p2-container",
        "p3": "p3-container",
        "relay1": "relay1-container",
        "relay2": "relay2-container",
        "amaru-consumer": "consumer-container",
        "amaru-relay-1": "amaru-one",
        "amaru-relay-2": "amaru-two",
        "amaru-target-extract": "amaru-extractor",
    }
    monkeypatch.setattr(qualification, "_project_containers", lambda _project: containers)
    monkeypatch.setattr(
        qualification,
        "_image_id",
        lambda image: (
            target_image
            if image == target_image
            else wrapper_image_id
            if image == CONTROL_AMARU_IMAGE
            else cardano_image_id
        ),
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "inspect"]:
            container = args[2]
            image_id = (
                target_image
                if container == "amaru-extractor"
                else wrapper_image_id
                if container.startswith("amaru-")
                else cardano_image_id
            )
            return qualification.subprocess.CompletedProcess(args, 0, image_id + "\n", "")
        if args[:3] in (["docker", "exec", "amaru-one"], ["docker", "exec", "amaru-two"]):
            if args[3] == "/target/amaru":
                return qualification.subprocess.CompletedProcess(
                    args, 0, "Amaru 10.11.20260912 (b159172)\n", ""
                )
            return qualification.subprocess.CompletedProcess(args, 127, "", "missing")
        if args[:2] == ["docker", "exec"]:
            return qualification.subprocess.CompletedProcess(
                args, 0, "cardano-node 10.7.1\n", ""
            )
        raise AssertionError(args)

    monkeypatch.setattr(qualification, "_run", fake_run)

    observation = qualification._identity_observation(
        "candidate",
        scope="mixed",
        cardano_version="10.7.1",
        amaru_version="10.11.20260912-local-b892defe",
        cardano_image="cardano-image",
        amaru_image=target_image,
        amaru_runtime_interface="extracted-binary",
        operator_stock_content_id=target_image,
    )

    assert observation["matched"] is True
    relay = observation["services"]["amaru-relay-1"]
    assert relay["identity_basis"] == "operator-registration-config-id"
    assert relay["reported"] == "Amaru 10.11.20260912 (b159172)"
    assert relay["reported_source_matched"] is None
    assert relay["executable_digest_matched"] is None

    wrong_content = qualification._identity_observation(
        "candidate",
        scope="mixed",
        cardano_version="10.7.1",
        amaru_version="10.11.20260912-local-b892defe",
        cardano_image="cardano-image",
        amaru_image=target_image,
        amaru_runtime_interface="extracted-binary",
        operator_stock_content_id="sha256:" + "d" * 64,
    )
    assert wrong_content["matched"] is False

    invalidated_registration = qualification._identity_observation(
        "candidate",
        scope="mixed",
        cardano_version="10.7.1",
        amaru_version="10.11.20260912",
        cardano_image="cardano-image",
        amaru_image=target_image,
        amaru_runtime_interface="extracted-binary",
        operator_stock_registration_expected=True,
        operator_stock_content_id=None,
    )
    assert invalidated_registration["matched"] is False
    assert (
        invalidated_registration["services"]["amaru-relay-1"]["identity_basis"]
        == "operator-registration-invalid"
    )


def test_patched_amaru_identity_uses_source_patch_and_executable_not_release_text(monkeypatch):
    target_image = "dwarf/amaru-measurement@sha256:" + "9" * 64
    target_image_id = "sha256:" + "a" * 64
    wrapper_image_id = "sha256:" + "b" * 64
    cardano_image_id = "sha256:" + "c" * 64
    source_revision = "b159172f25a9c389f82f20bca4f15e3032791638"
    patch_set = "7" * 64
    executable_digest = "sha256:" + "e" * 64
    containers = {
        "p1": "p1-container",
        "p2": "p2-container",
        "p3": "p3-container",
        "relay1": "relay1-container",
        "relay2": "relay2-container",
        "amaru-consumer": "consumer-container",
        "amaru-relay-1": "amaru-one",
        "amaru-relay-2": "amaru-two",
        "amaru-target-extract": "amaru-extractor",
    }
    monkeypatch.setattr(qualification, "_project_containers", lambda _project: containers)
    monkeypatch.setattr(
        qualification,
        "_image_id",
        lambda image: (
            target_image_id
            if image == target_image
            else wrapper_image_id
            if image == CONTROL_AMARU_IMAGE
            else cardano_image_id
        ),
    )

    def fake_run(args, **_kwargs):
        if args[:3] == ["docker", "image", "inspect"]:
            return qualification.subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    {
                        "org.opencontainers.image.revision": source_revision,
                        "org.dwarf.measurement.patch-sha256": patch_set,
                    }
                )
                + "\n",
                "",
            )
        if args[:2] == ["docker", "inspect"]:
            container = args[2]
            image_id = (
                target_image_id
                if container == "amaru-extractor"
                else wrapper_image_id
                if container.startswith("amaru-")
                else cardano_image_id
            )
            return qualification.subprocess.CompletedProcess(args, 0, image_id + "\n", "")
        if args[:3] in (["docker", "exec", "amaru-one"], ["docker", "exec", "amaru-two"]):
            if args[3:5] == ["/target/amaru", "--version"]:
                return qualification.subprocess.CompletedProcess(
                    args, 0, "Amaru 10.11.0 (b159172f+dirty)\n", ""
                )
            if args[3:5] == ["sha256sum", "/target/amaru"]:
                return qualification.subprocess.CompletedProcess(
                    args, 0, "e" * 64 + "  /target/amaru\n", ""
                )
            return qualification.subprocess.CompletedProcess(args, 127, "", "missing")
        if args[:2] == ["docker", "exec"]:
            return qualification.subprocess.CompletedProcess(
                args, 0, "cardano-node 10.7.1\n", ""
            )
        raise AssertionError(args)

    monkeypatch.setattr(qualification, "_run", fake_run)
    measurement_identity = {
        "version": "10.11.20260912",
        "source_revision": source_revision,
        "patch_set_sha256": patch_set,
        "executable_digest": executable_digest,
        "image": target_image,
        "image_digest": target_image_id,
        "target_mode": "patched",
    }

    observation = qualification._identity_observation(
        "candidate",
        scope="amaru-only",
        cardano_version="10.7.1",
        amaru_version="10.11.20260912",
        cardano_image="cardano-image",
        amaru_image=target_image,
        amaru_runtime_interface="extracted-binary",
        measurement_target_identity=measurement_identity,
    )

    assert observation["matched"] is True
    assert observation["patched_target"]["labels_matched"] is True
    assert observation["services"]["amaru-relay-1"]["reported_source_matched"] is True
    assert observation["services"]["amaru-relay-1"]["executable_digest_matched"] is True
    assert observation["services"]["amaru-relay-1"]["expected_version"] == "10.11.20260912"
    assert "10.11.20260912" not in observation["services"]["amaru-relay-1"]["reported"]


def test_project_names_are_unique_safe_and_never_the_live_project():
    first = build_project_name("mixed", "11.1.2", "10.11.20260912", token="abc123")
    second = build_project_name("mixed", "11.1.2", "10.11.20260912", token="def456")
    assert first != second
    assert first.startswith("dwarf-qual-mixed-")
    assert first not in PROTECTED_PROJECTS


def test_transform_mixed_is_namespaced_and_changes_only_target_artifacts():
    modern_amaru = "ghcr.io/pragma-org/amaru@sha256:" + "9" * 64
    transformed = transform_compose_model(
        _compose_model(),
        scope="mixed",
        project="dwarf-qual-mixed-demo",
        cardano_image="new-cardano",
        amaru_image=modern_amaru,
    )

    assert all("container_name" not in service for service in transformed["services"].values())
    assert transformed["networks"]["default"]["name"] == "dwarf-qual-mixed-demo-default"
    assert transformed["services"]["p1"]["image"] == "new-cardano"
    assert transformed["services"]["amaru-consumer"]["image"] == "new-cardano"
    relay = transformed["services"]["amaru-relay-1"]
    assert relay["image"] == CONTROL_AMARU_IMAGE
    assert relay["entrypoint"] == "amaru-relay-bootstrap"
    assert relay["environment"]["AMARU_BIN"] == "/target/amaru"
    assert relay["environment"]["AMARU_MIGRATE_CHAIN_DB"] == "true"
    assert relay["environment"]["AMARU_PEER"] == "p1.example:3001"
    assert "AMARU_PEER_MIX" not in relay["environment"]
    assert "AMARU_UPSTREAM_PEERS" not in relay["environment"]
    assert transformed["services"]["amaru-relay-2"]["environment"]["AMARU_PEER"] == "p2.example:3001"
    assert any(item.get("target") == "/target" for item in relay["volumes"])
    extractor = transformed["services"]["amaru-target-extract"]
    assert extractor["image"] == modern_amaru
    extractor_command = extractor["command"][0]
    assert 'temporary=$$(mktemp /target/.amaru.XXXXXX)' in extractor_command
    assert 'status=$$?' in extractor_command
    assert 'rm -f "$$temporary" || true' in extractor_command
    assert 'cp /usr/local/bin/amaru "$$temporary"' in extractor_command
    assert extractor_command.index('chmod 0755 "$$temporary"') < extractor_command.index(
        'mv -f "$$temporary" /target/amaru'
    )
    assert 'mv -f "$$temporary" /target/amaru' in extractor_command
    assert "cp /usr/local/bin/amaru /target/amaru" not in extractor_command
    assert extractor["user"] == "0:0"
    assert "amaru-target-bin" in transformed["volumes"]
    assert "amaru-consumer-ready" not in transformed["services"]
    assert transformed["services"]["amaru-consumer"]["depends_on"][
        "amaru-consumer-seed"
    ]["condition"] == "service_completed_successfully"
    assert transformed["services"]["amaru-consumer-seed"]["image"] == CONTROL_AMARU_IMAGE
    assert transformed["volumes"]["external"].get("external") is not True


def test_modern_amaru_extractor_atomically_replaces_an_executing_binary(tmp_path):
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-running",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    command = _extractor_container_command(transformed)
    target = tmp_path / "amaru"
    replacement = Path("/bin/echo")
    shutil.copyfile(sys.executable, target)
    target.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_cp = fake_bin / "cp"
    fake_cp.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "$DWARF_BUSY_TARGET" ]; then\n'
        "  echo 'Text file busy' >&2\n"
        "  exit 1\n"
        "fi\n"
        'exec /bin/cp "$@"\n',
        encoding="utf-8",
    )
    fake_cp.chmod(0o755)
    running = subprocess.Popen(
        [str(target), "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert running.poll() is None
        result = subprocess.run(
            [
                "/bin/sh",
                "-ec",
                command.replace("/usr/local/bin/amaru", str(replacement)).replace(
                    "/target", str(tmp_path)
                ),
            ],
            env={
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "DWARF_BUSY_TARGET": str(target),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert running.poll() is None
        assert target.read_bytes() == replacement.read_bytes()
        assert stat.S_IMODE(target.stat().st_mode) == 0o755
        assert list(tmp_path.glob(".amaru.*")) == []
    finally:
        running.terminate()
        running.wait(timeout=5)


def test_modern_amaru_extractor_survives_compose_config_round_trip(tmp_path):
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-compose",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(json.dumps(transformed), encoding="utf-8")
    rendered = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(compose_path),
            "config",
            "--format",
            "json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert rendered.returncode == 0, rendered.stderr
    materialized = json.loads(rendered.stdout)
    source_command = transformed["services"]["amaru-target-extract"]["command"][0]
    assert materialized["services"]["amaru-target-extract"]["command"][0] == source_command
    assert "$$" in source_command
    assert "variable is not set" not in rendered.stderr


@pytest.mark.skipif(
    not os.environ.get("DWARF_EXTRACTOR_COMPOSE_TEST_IMAGE"),
    reason=(
        "set DWARF_EXTRACTOR_COMPOSE_TEST_IMAGE to a locally present, "
        "immutable image with POSIX shell utilities"
    ),
)
def test_modern_amaru_extractor_reaches_actual_container_shell(tmp_path):
    image = os.environ["DWARF_EXTRACTOR_COMPOSE_TEST_IMAGE"]
    assert image.startswith("sha256:") or "@sha256:" in image
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-container",
        cardano_image="known-good-cardano",
        amaru_image=image,
        amaru_runtime_interface="extracted-binary",
    )
    extractor = transformed["services"]["amaru-target-extract"]
    extractor["command"][0] = extractor["command"][0].replace(
        "/usr/local/bin/amaru", "/bin/echo"
    )
    extractor["command"][0] += (
        "; cmp -s /bin/echo /target/amaru"
        "; test -x /target/amaru"
        "; test -z \"$$(find /target -name '.amaru.*' -print -quit)\""
    )
    extractor["volumes"] = [{"type": "tmpfs", "target": "/target"}]
    compose_path = tmp_path / "compose.json"
    compose_path.write_text(
        json.dumps({"services": {"amaru-target-extract": extractor}}),
        encoding="utf-8",
    )
    project = f"dwarf-test-extractor-{os.getpid()}-{tmp_path.name[-8:]}"
    delivered_command = ""
    try:
        executed = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                project,
                "--file",
                str(compose_path),
                "up",
                "--abort-on-container-exit",
                "--exit-code-from",
                "amaru-target-extract",
                "--pull",
                "never",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        located = subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                project,
                "--file",
                str(compose_path),
                "ps",
                "--all",
                "--quiet",
                "amaru-target-extract",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert located.returncode == 0, located.stderr
        container_id = located.stdout.strip()
        assert container_id
        inspected = subprocess.run(
            ["docker", "inspect", container_id],
            text=True,
            capture_output=True,
            check=False,
        )
        assert inspected.returncode == 0, inspected.stderr
        delivered_command = json.loads(inspected.stdout)[0]["Config"]["Cmd"][0]
    finally:
        subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                project,
                "--file",
                str(compose_path),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    assert executed.returncode == 0, executed.stderr
    assert "$$" not in delivered_command
    assert "temporary=$(mktemp /target/.amaru.XXXXXX)" in delivered_command
    assert "cmp -s /bin/echo /target/amaru" in delivered_command


def test_modern_amaru_extractor_failure_preserves_target_and_cleans_temp(tmp_path):
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-failure",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    command = _extractor_container_command(transformed)
    target = tmp_path / "amaru"
    replacement = Path("/bin/echo")
    original = b"preserved target\n"
    target.write_bytes(original)
    target.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_chmod = fake_bin / "chmod"
    fake_chmod.write_text("#!/bin/sh\nexit 37\n", encoding="utf-8")
    fake_chmod.chmod(0o755)

    result = subprocess.run(
        [
            "/bin/sh",
            "-ec",
            command.replace("/usr/local/bin/amaru", str(replacement)).replace(
                "/target", str(tmp_path)
            ),
        ],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 37
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".amaru.*")) == []


def test_modern_amaru_extractor_signal_is_nonzero_and_cleans_temp(tmp_path):
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-signal",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    command = _extractor_container_command(transformed)
    target = tmp_path / "amaru"
    replacement = Path("/bin/echo")
    original = b"preserved target\n"
    target.write_bytes(original)
    target.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_chmod = fake_bin / "chmod"
    fake_chmod.write_text(
        "#!/bin/sh\n"
        'kill -TERM "$PPID"\n'
        "sleep 0.1\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_chmod.chmod(0o755)

    result = subprocess.run(
        [
            "/bin/sh",
            "-ec",
            command.replace("/usr/local/bin/amaru", str(replacement)).replace(
                "/target", str(tmp_path)
            ),
        ],
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 143
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".amaru.*")) == []


def test_modern_amaru_extractor_is_safe_for_concurrent_and_repeated_invocation(
    tmp_path,
):
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-extractor-concurrent",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    command = _extractor_container_command(transformed)
    target = tmp_path / "amaru"
    replacement = Path("/bin/echo")
    target.write_bytes(b"old target\n")
    target.chmod(0o755)
    argv = [
        "/bin/sh",
        "-ec",
        command.replace("/usr/local/bin/amaru", str(replacement)).replace(
            "/target", str(tmp_path)
        ),
    ]

    concurrent = [
        subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(4)
    ]
    results = [process.communicate(timeout=5) for process in concurrent]
    assert [process.returncode for process in concurrent] == [0, 0, 0, 0], results

    repeated = subprocess.run(argv, text=True, capture_output=True, check=False)
    assert repeated.returncode == 0, repeated.stderr
    assert target.read_bytes() == replacement.read_bytes()
    assert stat.S_IMODE(target.stat().st_mode) == 0o755
    assert list(tmp_path.glob(".amaru.*")) == []


def test_transform_retained_amaru_control_keeps_legacy_runtime_interface():
    legacy_amaru = (
        "ghcr.io/lambdasistemi/amaru-bootstrap-producer@sha256:" + "8" * 64
    )

    transformed = transform_compose_model(
        _compose_model(),
        scope="mixed",
        project="dwarf-qual-mixed-control",
        cardano_image="known-good-cardano",
        amaru_image=legacy_amaru,
    )

    relay = transformed["services"]["amaru-relay-1"]
    assert relay["image"] == legacy_amaru
    assert relay["entrypoint"] == "amaru-relay-bootstrap"
    assert relay["environment"]["AMARU_PEER"] == "p1.example:3001"
    assert "AMARU_MIGRATE_CHAIN_DB" not in relay["environment"]
    assert "AMARU_BIN" not in relay["environment"]
    assert "amaru-target-extract" not in transformed["services"]


def test_cardano_only_transform_removes_amaru_paths_but_keeps_real_producers():
    transformed = transform_compose_model(
        _compose_model(),
        scope="cardano-only",
        project="dwarf-qual-cardano-demo",
        cardano_image="new-cardano",
        amaru_image=None,
    )
    assert set(transformed["services"]) == {
        "configurator", "tracer", "tracer-sidecar", "log-tailer",
        "p1", "p2", "p3", "relay1", "relay2",
    }
    assert transformed["services"]["p3"]["image"] == "new-cardano"


def test_amaru_only_contract_keeps_fixed_honest_source_and_marks_target_scope():
    transformed = transform_compose_model(
        _compose_model(),
        scope="amaru-only",
        project="dwarf-qual-amaru-demo",
        cardano_image="known-good-cardano",
        amaru_image="ghcr.io/pragma-org/amaru@sha256:" + "9" * 64,
    )
    assert transformed["services"]["p1"]["image"] == "known-good-cardano"
    assert transformed["services"]["amaru-relay-2"]["image"] == CONTROL_AMARU_IMAGE
    assert transformed["services"]["amaru-target-extract"]["image"].startswith(
        "ghcr.io/pragma-org/amaru@sha256:"
    )
    assert transformed["x-dwarf-qualification"]["contract"] == "amaru-relay-consumer"


def test_classification_requires_every_non_vacuous_runtime_gate():
    gates = {
        "fresh_state": True,
        "exact_identity": True,
        "required_services": True,
        "chain_progress": True,
        "peer_formation": True,
        "consumer_amaru_only_path": True,
        "consumer_converged": True,
        "no_fatal_signatures": True,
        "no_restart_loop": True,
        "clean_teardown": True,
    }
    assert classify_qualification("mixed", gates)["passed"] is True
    gates["consumer_amaru_only_path"] = False
    result = classify_qualification("mixed", gates)
    assert result["passed"] is False
    assert "consumer_amaru_only_path" in result["failed_gates"]


def test_terminal_store_and_cli_failures_are_classified_for_fast_stop():
    assert classify_terminal_runtime_failure(
        "chain database cannot be migrated to version 5 automatically"
    ) == "amaru-bootstrap-store-incompatible"
    assert classify_terminal_runtime_failure(
        "error: unexpected argument '--migrate-chain-db' found"
    ) == "amaru-runtime-interface-incompatible"
    assert classify_terminal_runtime_failure(
        "/target/amaru: cannot execute: required file not found"
    ) == "amaru-runtime-executable-incompatible"
    assert classify_terminal_runtime_failure("waiting for chain progress") is None


def test_known_vrf_bootstrap_warning_is_background_not_a_fatal_termination():
    signals = classify_log_signals(
        "ChainSync.Client.Exception HeaderProtocolError VRFKeyBadProof (SlotNo 5)"
    )

    assert signals["fatal"] == []
    assert signals["background"] == ["known-vrf-key-bad-proof"]


def test_actual_process_termination_signatures_remain_fatal():
    signals = classify_log_signals("thread panicked at listener EADDRINUSE")

    assert "panic" in signals["fatal"]
    assert "listener-address-in-use" in signals["fatal"]


def test_amaru_runtime_logs_fall_back_to_direct_service_logs(monkeypatch):
    monkeypatch.setattr(
        qualification,
        "_project_containers",
        lambda _project: {
            "tracer-sidecar": "trace",
            "amaru-relay-1": "relay-one",
            "amaru-relay-2": "relay-two",
        },
    )

    def fake_run(args, **_kwargs):
        if args[:2] == ["docker", "exec"]:
            return qualification.subprocess.CompletedProcess(args, 126, "", "no shell")
        if args[:2] == ["docker", "logs"] and args[-1] == "relay-one":
            return qualification.subprocess.CompletedProcess(
                args,
                0,
                "chain database cannot be migrated to version 5 automatically\n",
                "",
            )
        return qualification.subprocess.CompletedProcess(args, 0, "relay healthy\n", "")

    monkeypatch.setattr(qualification, "_run", fake_run)

    logs = qualification._amaru_log_text("candidate")

    assert "cannot be migrated" in logs
    assert classify_terminal_runtime_failure(logs) == "amaru-bootstrap-store-incompatible"


def test_catalog_proposal_never_mutates_or_auto_confirms(tmp_path):
    result = {
        "scope": "mixed",
        "passed": True,
        "candidate": {"cardano_version": "11.1.2", "amaru_version": "10.11.20260912"},
        "completed_at": "2026-09-17T23:00:00Z",
        "evidence_root": str(tmp_path),
        "classification": "passed-all-gates",
    }
    proposal = propose_catalog_update(result)
    assert proposal["apply_automatically"] is False
    assert proposal["proposed_status"] == "confirmed"
    assert proposal["review_required"] is True


def test_terminal_compatibility_failure_proposes_incompatible_not_unknown(tmp_path):
    result = {
        "scope": "amaru-only",
        "passed": False,
        "candidate": {"cardano_version": "10.7.1", "amaru_version": "10.11.20260903"},
        "completed_at": "2026-09-18T04:30:00Z",
        "evidence_root": str(tmp_path),
        "classification": "runtime-contract-failed",
        "terminal_failure": "amaru-bootstrap-store-incompatible",
    }

    proposal = propose_catalog_update(result)

    assert proposal["proposed_status"] == "incompatible"
    assert proposal["reason"] == "amaru-bootstrap-store-incompatible"


@pytest.mark.parametrize("project", sorted(PROTECTED_PROJECTS))
def test_transform_refuses_protected_projects(project):
    with pytest.raises(QualificationError, match="protected"):
        transform_compose_model(
            _compose_model(),
            scope="mixed",
            project=project,
            cardano_image="cardano",
            amaru_image="amaru",
        )


@pytest.mark.parametrize(
    ("scope", "cardano_version", "expected"),
    [
        ("mixed", "11.1.2", False),
        ("amaru-only", "11.1.0", False),
        ("mixed", "12.0.0", False),
        ("mixed", "10.7.1", None),
        ("mixed", "11.0.1", None),
        ("cardano-only", "11.1.2", None),
        ("mixed", None, None),
    ],
)
def test_experimental_protocols_policy_pins_false_only_for_amaru_with_node_11_1(
    scope, cardano_version, expected
):
    assert (
        qualification.cardano_experimental_protocols_policy(scope, cardano_version)
        is expected
    )


def _compose_model_with_configurator_command():
    model = _compose_model()
    model["services"]["configurator"]["command"] = ["set -euo pipefail\n/configurator.sh\n"]
    return model


def test_transform_pins_experimental_protocols_in_every_generated_config():
    transformed = transform_compose_model(
        _compose_model_with_configurator_command(),
        scope="mixed",
        project="dwarf-qual-mixed-demo",
        cardano_image="new-cardano",
        amaru_image=None,
        cardano_experimental_protocols=False,
    )

    command = transformed["services"]["configurator"]["command"]
    assert len(command) == 1
    assert command[0].startswith("set -euo pipefail\n/configurator.sh")
    assert "jq '.ExperimentalProtocolsEnabled = false'" in command[0]
    assert "jq -e '.ExperimentalProtocolsEnabled == false'" in command[0]
    assert "/configs/[123]" in command[0]
    assert transformed["x-dwarf-qualification"]["cardano_experimental_protocols"] is False


def test_transform_leaves_configurator_untouched_without_a_pin():
    model = _compose_model_with_configurator_command()
    transformed = transform_compose_model(
        model,
        scope="mixed",
        project="dwarf-qual-mixed-demo",
        cardano_image="new-cardano",
        amaru_image=None,
    )

    assert transformed["services"]["configurator"]["command"] == [
        "set -euo pipefail\n/configurator.sh\n"
    ]
    assert transformed["x-dwarf-qualification"]["cardano_experimental_protocols"] is None
