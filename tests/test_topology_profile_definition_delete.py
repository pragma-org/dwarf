"""Guarded deletion of user topology-profile definitions (never topology teardown)."""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
import yaml

from profile_manager import dashboard
from profile_manager import scenario
from profile_manager.data import catalog_definitions as definitions
from profile_manager.data import health


PROFILE_ID = "codex-basic-profile-template-20261011-0736"


def _profile(profile_id: str = PROFILE_ID) -> dict:
    return {
        "id": profile_id,
        "label": "Disposable Basic template profile",
        "node_type": "mixed",
        "node_count": 1,
        "amaru_node_count": 1,
        "network_magic": 42,
        "peer_sharing": False,
        "remote_runtime_root": f"/opt/dwarf/profiles/{profile_id}",
        "compose_project": f"dwarf-{profile_id}",
        "topology_pattern": "local-mesh",
        "shared_genesis": True,
    }


def _scenario(scenario_id: str, profile_id: str) -> dict:
    return {
        "spec_version": "v1",
        "id": scenario_id,
        "title": "Uses a topology profile",
        "target": {"implementation": "amaru", "version": "any"},
        "runtime": "devnet",
        "profile": profile_id,
        "setup": [],
        "load": [],
        "faults": [],
        "probes": [],
        "assertions": [],
        "teardown": [],
    }


@pytest.fixture
def roots(tmp_path, monkeypatch):
    profiles = tmp_path / "profiles"
    scenarios = tmp_path / "scenarios"
    profiles.mkdir()
    scenarios.mkdir()
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(profiles))
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    return {"profiles": profiles, "scenarios": scenarios, "tmp": tmp_path}


def _write_profile(root: Path, profile_id: str = PROFILE_ID) -> tuple[Path, str]:
    directory = root / profile_id
    directory.mkdir()
    source = (json.dumps(_profile(profile_id), indent=2) + "\n").encode()
    path = directory / "profile.yaml"
    path.write_bytes(source)
    return path, hashlib.sha256(source).hexdigest()


def _inventory(monkeypatch, *, available=True, managed=(), owned_resources=()):
    calls = []

    def fake(**_kwargs):
        calls.append(1)
        return {
            "available": available,
            "managed": list(managed),
            "owned_resources": list(owned_resources),
            "unmanaged": [],
            "error": None if available else "controller inventory request failed",
        }

    monkeypatch.setattr(health, "controller_inventory", fake)
    return calls


def _post_delete(profile_id: str, digest: str, token: str = "secret"):
    return dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path=f"/api/catalog/profiles/{profile_id}/delete",
        body=urlencode({"token": token, "expected_sha256": digest}).encode(),
        expected_token="secret",
    )


def test_disposable_user_profile_deletes_only_its_definition(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    sidecar = path.parent / "notes.txt"
    sidecar.write_text("operator notes\n")
    other, _ = _write_profile(roots["profiles"], "other-user-profile")
    _inventory(monkeypatch, managed=[{"topology_id": "other-user-profile"}])

    assert definitions.is_user_definition("profiles", PROFILE_ID)
    result = _post_delete(PROFILE_ID, digest)

    assert result is not None and result[0] == 200, result
    assert json.loads(result[2]) == {"ok": True, "id": PROFILE_ID, "url": "/operate/profiles"}
    assert not path.exists()
    assert sidecar.read_text() == "operator notes\n"
    assert other.is_file()


def test_empty_profile_directory_is_removed_after_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    _inventory(monkeypatch)
    assert _post_delete(PROFILE_ID, digest)[0] == 200
    assert not path.parent.exists()
    assert roots["profiles"].is_dir()


def test_profile_delete_requires_body_token(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    calls = _inventory(monkeypatch)
    denied = dashboard.dispatch_catalog_mutating_request(
        method="POST",
        path=f"/api/catalog/profiles/{PROFILE_ID}/delete?token=secret",
        body=urlencode({"expected_sha256": digest}).encode(),
        expected_token="secret",
    )
    assert denied[0] == 403
    assert _post_delete(PROFILE_ID, digest, token="wrong")[0] == 403
    assert path.is_file() and not calls


def test_packaged_profile_cannot_be_deleted(roots, monkeypatch):
    packaged = sorted(Path("dwarf/profiles").glob("*/profile.yaml"))[0]
    profile_id = packaged.parent.name
    copy = roots["profiles"] / profile_id
    copy.mkdir()
    (copy / "profile.yaml").write_bytes(packaged.read_bytes())
    digest = hashlib.sha256(packaged.read_bytes()).hexdigest()
    _inventory(monkeypatch)

    assert not definitions.is_user_definition("profiles", profile_id)
    result = _post_delete(profile_id, digest)
    assert result[0] == 409 and "packaged" in json.loads(result[2])["error"]
    assert (copy / "profile.yaml").is_file()


def test_stale_digest_is_refused(roots, monkeypatch):
    path, _ = _write_profile(roots["profiles"])
    _inventory(monkeypatch)
    for digest in ("0" * 64, "not-a-digest", ""):
        result = _post_delete(PROFILE_ID, digest)
        assert result[0] == 409
        assert path.is_file()


def test_symlinked_profile_directory_and_file_are_refused(roots, monkeypatch):
    _inventory(monkeypatch)
    outside = roots["tmp"] / "outside"
    outside.mkdir()
    source = (json.dumps(_profile("linked-dir-profile"), indent=2) + "\n").encode()
    (outside / "profile.yaml").write_bytes(source)
    os.symlink(outside, roots["profiles"] / "linked-dir-profile")
    digest = hashlib.sha256(source).hexdigest()
    assert not definitions.is_user_definition("profiles", "linked-dir-profile")
    assert _post_delete("linked-dir-profile", digest)[0] == 400
    assert (outside / "profile.yaml").is_file()

    target = roots["tmp"] / "target.yaml"
    source = (json.dumps(_profile("linked-file-profile"), indent=2) + "\n").encode()
    target.write_bytes(source)
    (roots["profiles"] / "linked-file-profile").mkdir()
    os.symlink(target, roots["profiles"] / "linked-file-profile" / "profile.yaml")
    digest = hashlib.sha256(source).hexdigest()
    assert not definitions.is_user_definition("profiles", "linked-file-profile")
    assert _post_delete("linked-file-profile", digest)[0] == 400
    assert target.is_file()


def test_profile_directory_swap_cannot_retarget_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    outside = roots["tmp"] / "outside-swap"
    outside.mkdir()
    external = outside / "profile.yaml"
    external.write_bytes(path.read_bytes())
    moved = roots["profiles"] / f"{PROFILE_ID}-moved"
    _inventory(monkeypatch)
    original_open = definitions.os.open
    swapped = False

    def swap_before_file_open(target, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(target).endswith("profile.yaml"):
            swapped = True
            path.parent.rename(moved)
            os.symlink(outside, path.parent)
        return original_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(definitions.os, "open", swap_before_file_open)
    result = _post_delete(PROFILE_ID, digest)

    assert result[0] in {400, 409}
    assert external.is_file()
    assert (moved / "profile.yaml").is_file()


def test_profile_catalog_root_symlink_cannot_retarget_delete(tmp_path, monkeypatch):
    external = tmp_path / "external-profiles"
    external.mkdir()
    external_path, digest = _write_profile(external)
    configured = tmp_path / "configured-profiles"
    os.symlink(external, configured)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    monkeypatch.setenv("ADA2_DWARF_PROFILES_DIR", str(configured))
    monkeypatch.setenv("ADA2_DWARF_SCENARIOS_DIR", str(scenarios))
    _inventory(monkeypatch)

    result = _post_delete(PROFILE_ID, digest)

    assert result[0] in {400, 409}
    assert external_path.is_file()


def test_profile_catalog_root_swap_cannot_retarget_delete(roots, monkeypatch):
    _path, digest = _write_profile(roots["profiles"])
    moved = roots["tmp"] / "profiles-before-swap"
    external = roots["tmp"] / "external-profiles-after-swap"
    external.mkdir()
    external_path, _ = _write_profile(external)
    swapped = False

    def inventory(**_kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            roots["profiles"].rename(moved)
            os.symlink(external, roots["profiles"])
        return {
            "available": True,
            "managed": [],
            "owned_resources": [],
            "unmanaged": [],
        }

    monkeypatch.setattr(health, "controller_inventory", inventory)
    result = _post_delete(PROFILE_ID, digest)

    assert result[0] in {200, 409}
    assert external_path.is_file()


def test_path_escape_ids_are_refused(roots, monkeypatch):
    _inventory(monkeypatch)
    for bad in ("..", "../escape", "a/b"):
        with pytest.raises(definitions.UnsafeDefinitionIdError):
            definitions.delete_user_definition("profiles", bad, expected_sha256="0" * 64)


def test_scenario_reference_blocks_profile_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    (roots["scenarios"] / "uses-profile.yaml").write_text(
        json.dumps(_scenario("uses-profile", PROFILE_ID), indent=2) + "\n"
    )
    _inventory(monkeypatch)
    result = _post_delete(PROFILE_ID, digest)
    assert result[0] == 409
    assert "uses-profile" in json.loads(result[2])["error"]
    assert path.is_file()


def test_concurrent_scenario_save_cannot_bypass_reference_check(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    source = (json.dumps(_scenario("racing-scenario", PROFILE_ID), indent=2) + "\n").encode()
    entered = threading.Event()
    release = threading.Event()
    original = definitions.parse_and_validate_definition

    real_replace = os.replace

    def slow_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("racing-scenario.yaml"):
            entered.set()
            release.wait(5)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(definitions.os, "replace", slow_replace)
    saver = threading.Thread(
        target=lambda: definitions.save_definition(
            "scenarios", "racing-scenario", source, create=True
        )
    )
    saver.start()
    assert entered.wait(5)
    errors = []

    def delete():
        try:
            definitions.delete_user_definition("profiles", PROFILE_ID, expected_sha256=digest)
        except definitions.CatalogError as exc:
            errors.append(exc)

    deleter = threading.Thread(target=delete)
    deleter.start()
    time.sleep(0.3)
    assert deleter.is_alive(), "profile delete did not wait for the in-flight scenario save"
    release.set()
    saver.join(5)
    deleter.join(5)
    assert original is definitions.parse_and_validate_definition
    assert errors and isinstance(errors[0], definitions.DefinitionConflictError)
    assert path.is_file()


def test_live_scenario_saves_reject_profile_deleted_first(roots):
    _path, digest = _write_profile(roots["profiles"])
    definitions.delete_user_definition("profiles", PROFILE_ID, expected_sha256=digest)
    body = (json.dumps(_scenario("after-delete", PROFILE_ID), indent=2) + "\n").encode()

    with pytest.raises(definitions.InvalidDefinitionError, match="unknown profile"):
        definitions.save_definition("scenarios", "after-delete", body, create=True)
    legacy = scenario.save_scenario_body(body, scenarios_dir=roots["scenarios"])

    assert legacy["ok"] is False
    assert "unknown profile" in legacy["error"]
    assert not (roots["scenarios"] / "after-delete.yaml").exists()


def test_live_scenario_can_reference_a_packaged_topology_profile(roots):
    packaged = sorted(Path("dwarf/profiles").glob("*/profile.yaml"))[0]
    profile_id = packaged.parent.name
    body = (
        json.dumps(_scenario("packaged-profile-reference", profile_id), indent=2) + "\n"
    ).encode()

    saved = definitions.save_definition(
        "scenarios", "packaged-profile-reference", body, create=True
    )

    assert saved.data["profile"] == profile_id
    assert (roots["scenarios"] / "packaged-profile-reference.yaml").is_file()


def test_pending_scenario_survives_delete_but_promotion_rejects_missing_profile(roots):
    _path, digest = _write_profile(roots["profiles"])
    body = (json.dumps(_scenario("pending-after-delete", PROFILE_ID), indent=2) + "\n").encode()
    pasted = scenario.handle_paste(body, scenarios_dir=roots["scenarios"])
    assert pasted["ok"] is True

    definitions.delete_user_definition("profiles", PROFILE_ID, expected_sha256=digest)
    promoted = scenario.handle_promote(
        "pending-after-delete", scenarios_dir=roots["scenarios"]
    )

    assert promoted["ok"] is False
    assert "unknown profile" in promoted["error"]
    assert (roots["scenarios"] / "pending" / "pending-after-delete.yaml").is_file()
    assert not (roots["scenarios"] / "pending-after-delete.yaml").exists()


def test_pending_overwrite_cannot_change_reference_lock_set_during_promotion(
    roots, monkeypatch
):
    _write_profile(roots["profiles"], "promotion-topology-profile")
    measurement_root = roots["tmp"] / "measurement-profiles"
    measurement_root.mkdir()
    monkeypatch.setenv("ADA2_DWARF_MEASUREMENT_PROFILES_DIR", str(measurement_root))
    measurement_id = "promotion-measurement-profile"
    measurement = yaml.safe_load(
        Path("dwarf/measurement-profiles/amaru-security-default.yaml").read_text()
    )
    measurement["id"] = measurement_id
    measurement_source = (json.dumps(measurement, indent=2) + "\n").encode()
    measurement_path = measurement_root / f"{measurement_id}.yaml"
    measurement_path.write_bytes(measurement_source)
    measurement_digest = hashlib.sha256(measurement_source).hexdigest()

    first = _scenario("promotion-race", "promotion-topology-profile")
    second = _scenario("promotion-race", "promotion-topology-profile")
    second.pop("profile")
    second["measurement_profile"] = measurement_id
    first_source = (json.dumps(first, indent=2) + "\n").encode()
    second_source = (json.dumps(second, indent=2) + "\n").encode()
    assert scenario.handle_paste(first_source, scenarios_dir=roots["scenarios"])["ok"]

    original_parse = definitions._parse_source
    original_validate = definitions.validate_live_scenario_references
    first_parse_entered = threading.Event()
    release_first_parse = threading.Event()
    validation_entered = threading.Event()
    release_validation = threading.Event()
    parse_count = 0

    def gated_parse(source):
        nonlocal parse_count
        data = original_parse(source)
        if threading.current_thread().name == "profile-promotion":
            parse_count += 1
            if parse_count == 1:
                first_parse_entered.set()
                assert release_first_parse.wait(5)
        return data

    def gated_validate(data):
        original_validate(data)
        if threading.current_thread().name == "profile-promotion":
            validation_entered.set()
            assert release_validation.wait(5)

    monkeypatch.setattr(definitions, "_parse_source", gated_parse)
    monkeypatch.setattr(definitions, "validate_live_scenario_references", gated_validate)
    promoted = []
    promoter = threading.Thread(
        name="profile-promotion",
        target=lambda: promoted.append(
            scenario.handle_promote("promotion-race", scenarios_dir=roots["scenarios"])
        ),
    )
    promoter.start()
    assert first_parse_entered.wait(5)

    pasted = []
    writer = threading.Thread(
        target=lambda: pasted.append(
            scenario.handle_paste(second_source, scenarios_dir=roots["scenarios"])
        )
    )
    writer.start()
    time.sleep(0.2)
    writer_was_blocked = writer.is_alive()
    release_first_parse.set()
    assert validation_entered.wait(5)

    deleted = []
    deleter = threading.Thread(
        target=lambda: deleted.append(
            definitions.delete_user_definition(
                "measurement-profiles",
                measurement_id,
                expected_sha256=measurement_digest,
            )
        )
    )
    deleter.start()
    time.sleep(0.2)
    delete_was_blocked = deleter.is_alive()
    release_validation.set()
    promoter.join(5)
    writer.join(5)
    deleter.join(5)

    assert writer_was_blocked, "pending replacement escaped the scenario lock"
    assert delete_was_blocked, "profile deletion escaped promotion reference locks"
    assert promoted and promoted[0]["ok"] is True
    live = yaml.safe_load((roots["scenarios"] / "promotion-race.yaml").read_text())
    assert live["profile"] == "promotion-topology-profile"
    assert live.get("measurement_profile") is None


def test_active_topology_owning_profile_blocks_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    _inventory(monkeypatch, managed=[{"name": "node-1", "topology_id": PROFILE_ID}])
    result = _post_delete(PROFILE_ID, digest)
    assert result[0] == 409
    assert "managed" in json.loads(result[2])["error"]
    assert path.is_file()


@pytest.mark.parametrize("component", ["support", "network", "volume"])
def test_any_owned_topology_resource_blocks_delete(roots, monkeypatch, component):
    path, digest = _write_profile(roots["profiles"])
    _inventory(
        monkeypatch,
        owned_resources=[
            {
                "name": f"owned-{component}",
                "component": component,
                "topology_id": PROFILE_ID,
            }
        ],
    )

    result = _post_delete(PROFILE_ID, digest)

    assert result[0] == 409
    assert "managed topology" in json.loads(result[2])["error"]
    assert path.is_file()


def test_unavailable_controller_inventory_blocks_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])
    _inventory(monkeypatch, available=False)
    result = _post_delete(PROFILE_ID, digest)
    assert result[0] == 409
    assert "inventory" in json.loads(result[2])["error"]
    assert path.is_file()


def test_inventory_exception_blocks_delete(roots, monkeypatch):
    path, digest = _write_profile(roots["profiles"])

    def boom(**_kwargs):
        raise RuntimeError("controller exploded")

    monkeypatch.setattr(health, "controller_inventory", boom)
    assert _post_delete(PROFILE_ID, digest)[0] == 409
    assert path.is_file()


def test_user_profile_detail_offers_digest_bound_delete_only_for_user_profile(
    roots, monkeypatch
):
    _, digest = _write_profile(roots["profiles"])
    html = dashboard.render_route_html(f"/operate/profiles/{PROFILE_ID}", token="secret")
    assert "data-definition-delete" in html
    assert f'action="/api/catalog/profiles/{PROFILE_ID}/delete"' in html
    assert f'value="{digest}"' in html
    assert 'name="token" value="secret"' in html
    assert "?token=" not in html
    assert 'src="/static/js/definition-delete.js"' in html

    packaged = sorted(Path("dwarf/profiles").glob("*/profile.yaml"))[0]
    copy = roots["profiles"] / packaged.parent.name
    copy.mkdir()
    (copy / "profile.yaml").write_bytes(packaged.read_bytes())
    packaged_html = dashboard.render_route_html(
        f"/operate/profiles/{packaged.parent.name}", token="secret"
    )
    assert "data-definition-delete" not in packaged_html
