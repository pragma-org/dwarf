import json

from scripts import runtime_compose_substrate as compose
from scripts import runtime_amaru_bootstrap_synth as bootstrap


def test_amaru_bootstrap_assets_are_bundled_and_loader_is_digest_pinned():
    assert (bootstrap.AMARU_TESTNET_DIR / "cardano-loader.sh").is_file()
    assert (bootstrap.AMARU_TESTNET_DIR / "amaru-loader.sh").is_file()
    assert "@sha256:" in bootstrap.DEFAULT_LOADER_BASE_IMAGE
    assert "@sha256:" in bootstrap.DEFAULT_BOOTSTRAP_PRODUCER_IMAGE


def test_staged_legacy_loader_keeps_its_supported_header_import_contract(tmp_path):
    scripts = tmp_path / "scripts"

    bootstrap._stage_loader_scripts(scripts)

    body = (scripts / "amaru-loader.sh").read_text(encoding="utf-8")
    assert "amaru import-headers --network ${NETWORK_NAME}" in body
    assert "--config-dir ${BASEDIR}" in body
    assert "--header-file" not in body


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


def test_custom_testnet_bootstrap_uses_immutable_support_tool_not_target_binary(monkeypatch, tmp_path):
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
    assert calls[0]["loader_image"] == bootstrap.DEFAULT_LOADER_BASE_IMAGE
    assert "amaru_image" not in calls[0]


def test_versioned_amaru_service_migrates_bootstrap_state_before_run():
    node = {
        "id": "amaru1",
        "impl": "amaru",
        "listen_address": "127.0.0.1:35001",
        "host_slot_index": 2,
        "chain_dir": "/tmp/chain.testnet_42.db",
        "ledger_dir": "/tmp/ledger.testnet_42.db",
        "container_peer_addresses": ["bootstrap-cardano:3001"],
        "image": "ghcr.io/pragma-org/amaru:v10.11.20260730@sha256:" + "c" * 64,
    }

    body = compose._docker_compose_body(
        compose_project="dwarf-profile-versioned-amaru",
        nodes=[node],
        network_name="testnet_42",
    )

    service = body["services"]["amaru1"]
    assert service["environment"]["AMARU_MIGRATE_CHAIN_DB"] == "true"
    assert "exec amaru run" in service["command"][0]
    assert '--era-history "/amaru/amaru1/era-history.json"' in service["command"][0]


def test_bootstrap_state_copies_generated_era_history_to_each_amaru_target(tmp_path):
    generated = tmp_path / "generated" / "testnet_42" / "snapshots"
    generated.mkdir(parents=True)
    legacy_history = {
        "stability_window": 1200,
        "eras": [
            {
                "start": {"time_ms": index * 1000, "slot": index, "epoch": index},
                "end": None,
                "params": {"epoch_size_slots": 400, "slot_length": 500},
            }
            for index in range(7)
        ],
    }
    (generated / "history.100.abc.json").write_text(
        json.dumps(legacy_history) + "\n", encoding="utf-8"
    )
    staged = tmp_path / "staged" / "1"
    staged.mkdir(parents=True)
    (staged / "ledger.db").mkdir()
    target = tmp_path / "target" / "amaru1"

    bootstrap._apply_staged_state(
        {
            "network_name": "testnet_42",
            "generated_root": str(tmp_path / "generated"),
            "amaru_state_roots": {"1": str(staged)},
            "target_amaru_state_roots": {"1": str(target)},
        }
    )

    upgraded = json.loads((target / "era-history.json").read_text(encoding="utf-8"))
    assert [era["params"]["era_name"] for era in upgraded["eras"]] == [
        "Byron",
        "Shelley",
        "Allegra",
        "Mary",
        "Alonzo",
        "Babbage",
        "Conway",
    ]
    assert upgraded["eras"][2]["start"]["time"] == 2
    assert "time_ms" not in upgraded["eras"][2]["start"]


def test_bootstrap_producer_command_uses_synthesized_cardano_db_and_exact_image(tmp_path):
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "config_roots": {"1": str(tmp_path / "configs" / "1")},
        "cardano_state_roots": {"1": str(tmp_path / "state" / "1")},
    }

    command = bootstrap.bootstrap_producer_command(layout=layout)

    assert bootstrap.DEFAULT_BOOTSTRAP_PRODUCER_IMAGE in command
    assert command[command.index("--user") + 1] == "1000:1000"
    assert f"{layout['cardano_state_roots']['1']}:/cardano/state:ro" in command
    assert f"{layout['config_roots']['1']}:/cardano/config:ro" in command
    assert "/cardano/state" in command
    assert "/cardano/config/configs" in command
    assert "/bundle" in command
    assert "testnet_42" in command


def test_producer_bundle_maps_named_databases_into_each_runtime_target(tmp_path):
    bundle = tmp_path / "workspace" / "producer-bundle" / "testnet_42"
    (bundle / "ledger.testnet_42.db").mkdir(parents=True)
    (bundle / "chain.testnet_42.db").mkdir()
    (bundle / "ledger.testnet_42.db" / "ledger").write_text("ok", encoding="utf-8")
    (bundle / "chain.testnet_42.db" / "chain").write_text("ok", encoding="utf-8")
    (bundle / "era-history.json").write_text('{"eras": []}\n', encoding="utf-8")
    target = tmp_path / "target" / "amaru1"
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(tmp_path / "workspace"),
        "target_amaru_state_roots": {"1": str(target)},
    }

    bootstrap.apply_producer_bundle(layout)

    assert (target / "ledger.db" / "ledger").read_text(encoding="utf-8") == "ok"
    assert (target / "chain.db" / "chain").read_text(encoding="utf-8") == "ok"
    assert (target / "era-history.json").is_file()
