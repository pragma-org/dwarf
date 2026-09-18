from scripts import runtime_compose_substrate as compose
from scripts import runtime_amaru_bootstrap_synth as bootstrap


def test_amaru_bootstrap_assets_are_bundled_and_loader_is_digest_pinned():
    assert (bootstrap.AMARU_TESTNET_DIR / "cardano-loader.sh").is_file()
    assert (bootstrap.AMARU_TESTNET_DIR / "amaru-loader.sh").is_file()
    assert "@sha256:" in bootstrap.DEFAULT_LOADER_BASE_IMAGE


def test_versioned_compose_nodes_are_discoverable_as_dwarf_managed():
    node = {
        "id": "node1",
        "impl": "cardano-node",
        "listen_address": "127.0.0.1:33001",
        "host_slot_index": 1,
        "image": "ghcr.io/intersectmbo/cardano-node:11.1.2@sha256:" + "a" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-local",
        nodes=[node],
        network_name="testnet_42",
    )

    labels = body["services"]["node1"]["labels"]
    assert labels["ada2.managed"] == "dwarf"
    assert labels["ada2.profile"] == "dwarf-profile-versioned-local"
    assert labels["ada2.service"] == "node1"


def test_custom_testnet_bootstrap_uses_selected_amaru_artifact(monkeypatch, tmp_path):
    selected = "ghcr.io/pragma-org/amaru:v10.11.20260903@sha256:" + "b" * 64
    calls = []

    def fake_synthesize(**kwargs):
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(compose, "synthesize_amaru_bootstrap", fake_synthesize)

    result = compose._synthesize_amaru_bootstrap_for_custom_testnet(
        runtime_root=tmp_path,
        plan={"nodes": [{"id": "amaru1", "impl": "amaru", "image": selected}]},
    )

    assert result == {"ok": True}
    assert calls[0]["amaru_image"] == selected
    assert calls[0]["loader_image"].startswith("dwarf/amaru-loader:version-")


def test_bootstrap_synthesis_builds_loader_from_selected_amaru_artifact(monkeypatch, tmp_path):
    selected = "ghcr.io/pragma-org/amaru:v10.11.20260730@sha256:" + "c" * 64
    observed = {}
    (tmp_path / "amaru-bootstrap-loader").mkdir()

    def fake_ensure_loader_image(**kwargs):
        observed.update(kwargs)
        return kwargs["loader_image"]

    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "generated_root": str(tmp_path / "generated"),
        "amaru_slot_map": {},
    }
    monkeypatch.setattr(bootstrap, "ensure_loader_image", fake_ensure_loader_image)
    monkeypatch.setattr(bootstrap, "prepare_loader_workspace", lambda **_kwargs: layout)
    monkeypatch.setattr(bootstrap, "loader_commands", lambda **_kwargs: (["true"], ["true"]))
    monkeypatch.setattr(bootstrap, "run_command", lambda _command: type("Result", (), {"returncode": 0})())
    monkeypatch.setattr(bootstrap, "_apply_staged_state", lambda _layout: None)

    bootstrap.synthesize_amaru_bootstrap(
        runtime_root=tmp_path,
        plan={"nodes": []},
        loader_image="dwarf/amaru-loader:selected",
        amaru_image=selected,
    )

    assert observed["amaru_image"] == selected
