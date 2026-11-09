"""Opt-in local reward-window timing for self-composed devnets with Amaru (2026-11-09).

Live run 20261109T211753Z-27b07104 (ledger-block-apply-isvalid-false-donation-value-creation):
the synthetic Amaru bootstrap pins the loader image's genesis (epochLength 86400, slotLength 1,
k 432, f 0.05), so the post-forge epoch boundary is ~24 h away and the run hit the controller's
40-minute limit (exit 126). substrate.amaru_local_reward_window_timing: true applies the
profile-c LOCAL_REWARD_WINDOW_CONTRACT (epoch 400 slots of 0.5 s, k 8, f 1/5, scale 10) to the
generated Cardano genesis and to the synthetic Amaru bootstrap inputs, and proves them identical
with the same proof profile-c uses. Off by default.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import local_reward_window as lrw
from scripts import runtime_amaru_bootstrap_synth as bootstrap
from scripts import runtime_amaru_control_substrate as control
from scripts import runtime_block_apply_bridge as bridge
from scripts import runtime_compose_substrate as compose
from scripts import runtime_substrate_common as common

KEY = "amaru_local_reward_window_timing"
DONATION_SCENARIOS = sorted([
    "ledger-block-apply-isvalid-false-donation-value-creation-amaru-cardano-node.yaml",
    "ledger-epoch-boundary-donation-treasury-realization-amaru-cardano-node.yaml",
])


# --- one shared contract ------------------------------------------------------------------------

def test_profile_c_and_self_compose_share_one_contract_and_proof():
    # profile-c holds the shared module's own objects (imported on the script path)
    assert control.LOCAL_REWARD_WINDOW_CONTRACT is control.local_reward_window.LOCAL_REWARD_WINDOW_CONTRACT
    assert control.LOCAL_REWARD_WINDOW_CONTRACT == lrw.LOCAL_REWARD_WINDOW_CONTRACT
    source = Path(control.__file__).read_text(encoding="utf-8")
    assert "local_reward_window.prove_timing_identity(" in source
    assert "consistent = (" not in source  # no second copy of the proof
    assert control.LOCAL_REWARD_WINDOW_EPOCH_FACTOR == lrw.LOCAL_REWARD_WINDOW_EPOCH_FACTOR
    contract = lrw.LOCAL_REWARD_WINDOW_CONTRACT
    # one epoch of the contract is 200 s
    assert contract["epoch_length"] * contract["slot_length_ms"] == 200_000


def _genesis_pair(**shelley_overrides):
    shelley = {
        "epochLength": 86400, "slotLength": 1, "securityParam": 432, "activeSlotsCoeff": 0.05,
        "protocolParams": {"rho": 0.003, "tau": 0.2, "a0": 0.3},
        "slotsPerKESPeriod": 129600, "maxKESEvolutions": 62, "maxLovelaceSupply": 45_000_000_000_000_000,
        "systemStart": "2026-11-09T21:00:00Z",
    }
    shelley.update(shelley_overrides)
    byron = {"protocolConsts": {"k": 432, "protocolMagic": 42}, "startTime": 1794258000}
    return shelley, byron


def _contract_pair():
    shelley, byron = _genesis_pair()
    request = compose._local_reward_window_request()
    shelley.update(request["shelley"])
    shelley["protocolParams"] = {**shelley["protocolParams"], **request["shelley"]["protocolParams"]}
    byron["protocolConsts"]["k"] = request["byron_protocol_k"]
    return shelley, byron


def test_generated_amaru_inputs_prove_identical_to_the_contract_genesis():
    shelley, byron = _contract_pair()
    globals_doc = lrw.amaru_global_parameters(shelley_genesis=shelley, byron_genesis=byron)
    era_doc = lrw.amaru_era_history()
    proof = lrw.prove_timing_identity(
        testnet=lrw.testnet_view(shelley_genesis=shelley, byron_genesis=byron),
        globals_doc=globals_doc, era_doc=era_doc)
    assert proof["verified"] is True
    assert (proof["epoch_length"], proof["slot_length_ms"], proof["security_parameter"]) == (400, 500, 8)
    assert globals_doc["slots_per_kes_period"] == 129600  # read from the genesis, not assumed
    assert globals_doc["max_kes_evolution"] == 62
    assert globals_doc["system_start"] == 1794258000 * 1000  # same source as the bootstrap producer


def test_proof_rejects_the_pinned_86400_genesis():
    shelley, byron = _genesis_pair()
    with pytest.raises(lrw.LocalRewardWindowError, match="approved tuple"):
        lrw.prove_timing_identity(
            testnet=lrw.testnet_view(shelley_genesis=shelley, byron_genesis=byron),
            globals_doc=lrw.amaru_global_parameters(shelley_genesis=shelley, byron_genesis=byron),
            era_doc=lrw.amaru_era_history())


# --- substrate key: reader, plan, loader, schema, refusals ---------------------------------------

def test_substrate_reader_defaults_off_and_rejects_non_booleans():
    assert common._amaru_local_reward_window_timing({}) is False
    assert common._amaru_local_reward_window_timing({KEY: True}) is True
    with pytest.raises(ValueError, match=KEY):
        common._amaru_local_reward_window_timing({KEY: "yes"})


def test_plan_carries_the_opt_in(tmp_path):
    substrate = {"network": "testnet_42", "compose_mode": "docker", KEY: True,
                 "nodes": [{"id": "n1", "impl": "cardano-node", "version": "11.1.2", "role": "observer"},
                           {"id": "a1", "impl": "amaru", "version": "10.11.20260925", "role": "submitter"}]}
    assert compose.allocate_node_plan(substrate, runtime_root=tmp_path, compose_project="p")[KEY] is True
    del substrate[KEY]
    assert compose.allocate_node_plan(substrate, runtime_root=tmp_path, compose_project="p")[KEY] is False


def test_unsupported_compose_paths_refuse_the_opt_in():
    amaru = [{"impl": "amaru"}, {"impl": "cardano-node"}]
    ok = {KEY: True, "network": "testnet_42", "nodes": amaru}
    compose._refuse_unsupported_local_reward_window_timing(ok, mode="docker")
    for plan, mode in (
        (ok, "multi-host"),
        (ok, "host"),
        ({**ok, "network": "preview"}, "docker"),
        ({**ok, "nodes": [{"impl": "cardano-node"}]}, "docker"),
        ({**ok, "kes_genesis_override": {"slots_per_kes_period": 100}}, "docker"),
    ):
        with pytest.raises(RuntimeError, match=KEY):
            compose._refuse_unsupported_local_reward_window_timing(plan, mode=mode)
    compose._refuse_unsupported_local_reward_window_timing({KEY: False, "network": "preview"}, mode="host")


def test_scenario_loader_keeps_and_validates_the_opt_in():
    from profile_manager import scenario as scenario_module
    path = Path("dwarf/scenarios") / DONATION_SCENARIOS[0]
    body = json.loads(path.read_text(encoding="utf-8"))
    assert scenario_module.scenario_from_body(json.dumps(body).encode()).substrate[KEY] is True
    body["substrate"][KEY] = "yes"
    with pytest.raises(scenario_module.ScenarioValidationError, match=KEY):
        scenario_module.scenario_from_body(json.dumps(body).encode())


def test_schema_documents_the_opt_in():
    schema = json.loads(Path("dwarf/spec/v1/schema.json").read_text(encoding="utf-8"))
    text = json.dumps(schema)
    assert f'"{KEY}"' in text


def test_only_the_two_donation_scenarios_opt_in():
    opted = sorted(p.name for p in Path("dwarf/scenarios").glob("*.yaml") if KEY in p.read_text(encoding="utf-8"))
    assert opted == DONATION_SCENARIOS


# --- synthetic bootstrap: request, hook, timing -----------------------------------------------

def _layout(tmp_path, *, request: bool):
    workspace = tmp_path / "workspace"
    layout = {
        "network_name": "testnet_42",
        "workspace_root": str(workspace),
        "generated_root": str(workspace / "generated"),
        "workload_funding_request": str(workspace / "workload-funding.json"),
        "governance_guardrails_request": None,
        "config_roots": {"1": str(tmp_path / "configs" / "1")},
        "cardano_state_roots": {"1": str(tmp_path / "state" / "1")},
        "amaru_state_roots": {"1": str(tmp_path / "amaru-state" / "1")},
    }
    if request:
        layout["local_reward_window_request"] = str(workspace / "local-reward-window.json")
    return layout


def test_loader_mounts_the_hook_and_request_only_when_opted_in(tmp_path):
    default_cmd, _ = bootstrap.loader_commands(layout=_layout(tmp_path, request=False))
    assert not any("local-reward-window" in part for part in default_cmd)
    opted_cmd, _ = bootstrap.loader_commands(layout=_layout(tmp_path, request=True))
    mounts = [opted_cmd[i + 1] for i, part in enumerate(opted_cmd) if part == "-v"]
    assert any(m.endswith(":/data/local-reward-window.json:ro") for m in mounts)
    assert any(m.endswith(":/data/apply-local-reward-window.sh:ro") for m in mounts)


def test_hook_applies_the_contract_to_every_pinned_genesis(tmp_path):
    scripts = tmp_path / "scripts"
    bootstrap._stage_loader_scripts(scripts)
    shelley, byron = _genesis_pair()
    pool = tmp_path / "configs" / "1"
    (pool / "configs").mkdir(parents=True)
    (pool / "configs" / "shelley-genesis.json").write_text(json.dumps(shelley), encoding="utf-8")
    (pool / "configs" / "byron-genesis.json").write_text(json.dumps(byron), encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps(compose._local_reward_window_request()), encoding="utf-8")
    subprocess.run(["bash", str(scripts / "apply-local-reward-window.sh"), str(request), str(pool)],
                   check=True, capture_output=True, text=True)
    patched_shelley = json.loads((pool / "configs" / "shelley-genesis.json").read_text(encoding="utf-8"))
    patched_byron = json.loads((pool / "configs" / "byron-genesis.json").read_text(encoding="utf-8"))
    proof = lrw.prove_timing_identity(
        testnet=lrw.testnet_view(shelley_genesis=patched_shelley, byron_genesis=patched_byron),
        globals_doc=lrw.amaru_global_parameters(shelley_genesis=patched_shelley, byron_genesis=patched_byron),
        era_doc=lrw.amaru_era_history())
    assert proof["verified"] is True
    assert patched_shelley["protocolParams"]["a0"] == 0.3  # untouched fields stay
    assert patched_byron["protocolConsts"]["protocolMagic"] == 42


def test_loader_applies_the_hook_before_the_start_time_and_scales_it_by_slot_length(tmp_path):
    scripts = tmp_path / "scripts"
    bootstrap._stage_loader_scripts(scripts)
    loader = (scripts / "cardano-loader.sh").read_text(encoding="utf-8")
    hook = loader.index("/data/apply-local-reward-window.sh")
    assert loader.index('copy_config "$pool_ix"') < hook < loader.index('set_start_time "$pool"')
    assert hook < loader.index("db-synthesizer --config")
    assert "local-reward-window-applied.json" in loader
    # the default (no request) start-time path is unchanged: 1 s slots, minute-aligned
    assert "SYSTEM_START_UNIX=$(($(date +%s) - SYNTHESIS_SLOT))" in loader
    assert "'+%Y-%m-%dT%H:%M:00Z'" in loader


def _run_set_start_time(tmp_path, *, slot_length, request: bool):
    """Run the staged loader's set_start_time with a fixed clock (the real function, sourced)."""
    scripts = tmp_path / "scripts"
    bootstrap._stage_loader_scripts(scripts)
    loader = (scripts / "cardano-loader.sh").read_text(encoding="utf-8")
    functions = loader[loader.index("write_file() {"):loader.index("pools=$(ls -d /configs/*)")]
    pool = tmp_path / "pool"
    (pool / "configs").mkdir(parents=True)
    shelley, byron = _genesis_pair(epochLength=400, slotLength=slot_length)
    (pool / "configs" / "shelley-genesis.json").write_text(json.dumps(shelley), encoding="utf-8")
    (pool / "configs" / "byron-genesis.json").write_text(json.dumps(byron), encoding="utf-8")
    req = tmp_path / "request.json"
    req.write_text(json.dumps({"start_lead_seconds": 180}), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # a fixed clock: `date +%s` -> 1800000000; other date calls go to GNU date if present
    real_date = subprocess.run(["bash", "-c", "command -v gdate || command -v date"], capture_output=True,
                               text=True).stdout.strip()
    (fake_bin / "date").write_text(
        f'#!/usr/bin/env bash\nif [[ "$1" == "+%s" ]]; then echo 1800000000; else exec {real_date} "$@"; fi\n')
    (fake_bin / "date").chmod(0o755)
    script = (
        "set -euo pipefail\n"
        f"export PATH={fake_bin}:$PATH\n"
        "BOOTSTRAP_TARGET_EPOCH=3\nBOOTSTRAP_TARGET_EPOCH_FRACTION_NUMERATOR=3\n"
        "BOOTSTRAP_TARGET_EPOCH_FRACTION_DENOMINATOR=4\nSYNTHESIS_SLOT=\"\"\n"
        f"LOCAL_REWARD_WINDOW_REQUEST={req if request else tmp_path / 'absent.json'}\n"
        "LOCAL_REWARD_WINDOW_SYSTEM_START=\"\"\n"
        + functions
        # write_file uses GNU mv/tr; it is not under test here
        + '\nwrite_file() { cat >"$1.tmp"; mv -f "$1.tmp" "$1"; }\n'
        + f'\nset_start_time "{pool}"\necho "$SYSTEM_START_UNIX $SYNTHESIS_SLOT"\n'
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return done, json.loads((pool / "configs" / "shelley-genesis.json").read_text()), \
        json.loads((pool / "configs" / "byron-genesis.json").read_text())


def test_opted_start_time_puts_the_synthesized_tip_at_now_plus_lead(tmp_path):
    if subprocess.run(["bash", "-c", "date -u -d @0 +%s"], capture_output=True).returncode != 0 and \
            subprocess.run(["bash", "-c", "command -v gdate"], capture_output=True).returncode != 0:
        pytest.skip("GNU date is required by the loader")
    done, shelley, byron = _run_set_start_time(tmp_path, slot_length=0.5, request=True)
    assert done.returncode == 0, done.stderr
    start, slot = (int(x) for x in done.stdout.split())
    assert slot == 400 * 3 + 300
    # tip wall time = start + 1500 slots * 0.5 s = now + lead
    assert start + slot // 2 == 1800000000 + 180
    assert byron["startTime"] == start
    assert shelley["systemStart"] == datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_opted_start_time_refuses_slot_lengths_that_are_not_whole_ms(tmp_path):
    if subprocess.run(["bash", "-c", "command -v gdate || date -u -d @0 +%s"], capture_output=True).returncode != 0:
        pytest.skip("GNU date is required by the loader")
    done, _, _ = _run_set_start_time(tmp_path, slot_length=0.0005, request=True)
    assert done.returncode != 0


def test_loader_evidence_is_validated_against_the_staged_genesis(tmp_path):
    layout = _layout(tmp_path, request=True)
    generated = Path(layout["generated_root"])
    generated.mkdir(parents=True)
    shelley, byron = _contract_pair()
    start = 1800000000 + 180 - 750
    shelley["systemStart"] = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    byron["startTime"] = start
    configs = Path(layout["config_roots"]["1"]) / "configs"
    configs.mkdir(parents=True)
    (configs / "shelley-genesis.json").write_text(json.dumps(shelley), encoding="utf-8")
    (configs / "byron-genesis.json").write_text(json.dumps(byron), encoding="utf-8")
    Path(layout["local_reward_window_request"]).parent.mkdir(parents=True, exist_ok=True)
    Path(layout["local_reward_window_request"]).write_text(
        json.dumps(compose._local_reward_window_request()), encoding="utf-8")
    evidence = {"system_start_unix": start, "synthesis_slot": 1500, "slot_length_ms": 500,
                "start_lead_seconds": 180, "synthesis_tip_unix": start + 750}
    (generated / "local-reward-window-applied.json").write_text(json.dumps(evidence), encoding="utf-8")
    assert bootstrap._validated_local_reward_window_evidence(layout)["synthesis_tip_unix"] == start + 750
    evidence["synthesis_tip_unix"] += 1
    (generated / "local-reward-window-applied.json").write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(RuntimeError, match="local reward-window"):
        bootstrap._validated_local_reward_window_evidence(layout)
    assert bootstrap._validated_local_reward_window_evidence(_layout(tmp_path, request=False)) is None


# --- after the bundle: Amaru inputs, proof, environment, start ----------------------------------

def _runtime(tmp_path, *, contract=True):
    env_root = tmp_path / "env"
    env_root.mkdir()
    shelley, byron = _contract_pair() if contract else _genesis_pair()
    (env_root / "shelley-genesis.json").write_text(json.dumps(shelley), encoding="utf-8")
    (env_root / "byron-genesis.json").write_text(json.dumps(byron), encoding="utf-8")
    state_root = tmp_path / "amaru" / "node1"
    state_root.mkdir(parents=True)
    producer_history = {"stability_window": 1200, "eras": [{"start": {"time": 0, "slot": 0, "epoch": 0},
                        "end": None, "params": {"epoch_size_slots": 400, "slot_length": 500, "era_name": "Conway"}}]}
    (state_root / "era-history.json").write_text(json.dumps(producer_history), encoding="utf-8")
    plan = {KEY: True, "nodes": [{"id": "node1", "impl": "amaru", "state_root": str(state_root)},
                                 {"id": "node2", "impl": "cardano-node"}]}
    loader = {"synthesis_tip_unix": 1800000000, "slot_length_ms": 500}
    return plan, loader, state_root


def test_amaru_inputs_follow_the_contract_and_the_proof_is_recorded(tmp_path):
    plan, loader, state_root = _runtime(tmp_path)
    evidence = compose._apply_local_reward_window_to_amaru_bootstrap(
        runtime_root=tmp_path, plan=plan, loader_evidence=loader)
    assert evidence["proof"]["verified"] is True
    env = evidence["amaru_environment"]
    assert env["AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM"] == "8"
    assert env["AMARU_GLOBAL_ACTIVE_SLOT_COEFF_INVERSE"] == "5"
    assert env["AMARU_GLOBAL_EPOCH_LENGTH_SCALE_FACTOR"] == "10"
    assert env["AMARU_GLOBAL_SLOTS_PER_KES_PERIOD"] == "129600"
    era = json.loads((state_root / "era-history.json").read_text(encoding="utf-8"))
    assert era == lrw.amaru_era_history()
    assert evidence["amaru_era_history"]["node1"]["producer_stability_window"] == 1200
    receipt = json.loads((tmp_path / "local-reward-window" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["proof"] == evidence["proof"]
    assert (tmp_path / "local-reward-window" / "global-parameters.json").is_file()


def test_amaru_inputs_refuse_a_genesis_the_hook_did_not_patch(tmp_path):
    plan, loader, _ = _runtime(tmp_path, contract=False)
    with pytest.raises(RuntimeError, match="approved tuple"):
        compose._apply_local_reward_window_to_amaru_bootstrap(runtime_root=tmp_path, plan=plan, loader_evidence=loader)


def test_compose_body_gives_amaru_the_contract_environment_only_when_opted_in():
    nodes = [{"id": "node1", "impl": "amaru", "listen_address": "127.0.0.1:34001", "chain_dir": "/x/chain.db",
              "ledger_dir": "/x/ledger.db", "container_peer_addresses": ["node2:3001"], "version": "10.11.20260925",
              "resolved_binary": "docker-image:ghcr.io/pragma-org/amaru:v1", "image_ref": "img"}]
    default = compose._docker_compose_body(compose_project="p", nodes=nodes, network_name="testnet_42")
    assert not any(k.startswith("AMARU_GLOBAL_") for k in default["services"]["node1"]["environment"])
    env = {"AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM": "8"}
    opted = compose._docker_compose_body(compose_project="p", nodes=nodes, network_name="testnet_42",
                                         amaru_environment=env)
    assert opted["services"]["node1"]["environment"]["AMARU_GLOBAL_CONSENSUS_SECURITY_PARAM"] == "8"


def test_start_waits_for_the_synthesized_tip_and_refuses_a_stale_one():
    slept = []
    record = compose._await_local_reward_window_start(
        {"synthesis_tip_unix": 1000, "slot_length_ms": 500}, now=lambda: 900.0, sleep=slept.append)
    # after the tip: Amaru rejects headers more than 2 s ahead of its clock (MAX_HEADER_CLOCK_SKEW)
    assert compose.LOCAL_REWARD_WINDOW_START_AFTER_TIP_SECONDS >= 2
    assert slept and abs(sum(slept) - (1000 + compose.LOCAL_REWARD_WINDOW_START_AFTER_TIP_SECONDS - 900)) < 1e-6
    assert record["waited_seconds"] == pytest.approx(sum(slept))
    # stability window 120 slots * 0.5 s = 60 s: past half of it, cardano-node could not forecast
    with pytest.raises(RuntimeError, match="stability window"):
        compose._await_local_reward_window_start(
            {"synthesis_tip_unix": 1000, "slot_length_ms": 500}, now=lambda: 1031.0, sleep=slept.append)
    assert compose._await_local_reward_window_start(
        {"synthesis_tip_unix": 1000, "slot_length_ms": 500}, now=lambda: 1010.0, sleep=slept.append)["waited_seconds"] == 0


def test_compose_orders_request_bootstrap_inputs_and_start():
    source = Path(compose.__file__).read_text(encoding="utf-8")
    fn = source[source.index("def _compose_substrate_docker("):source.index("def _compose_substrate_docker_multihost(")]
    request = fn.index("generated-local-reward-window.json")
    synth = fn.index("_synthesize_amaru_bootstrap_for_custom_testnet(")
    inputs = fn.index("_apply_local_reward_window_to_amaru_bootstrap(")
    body = fn.index("compose_body = _docker_compose_body(")
    start = fn.index("_await_local_reward_window_start(")
    up = fn.index('"docker", "compose", "--project-name", docker_project, "up", "-d"')
    assert request < synth < inputs < body < start < up
    assert '"local_reward_window": local_reward_window' in fn


# --- bridge: the forge must stay in the snapshot's epoch ----------------------------------------

def test_arm_is_refused_when_the_snapshot_spilled_past_the_deadline():
    before = {"epoch": 7, "slotInEpoch": 150}
    assert bridge.armable_after_snapshot(before, {"epoch": 7, "slotInEpoch": 300}, epoch_length=400) is True
    assert bridge.armable_after_snapshot(before, {"epoch": 7, "slotInEpoch": 380}, epoch_length=400) is False
    assert bridge.armable_after_snapshot(before, {"epoch": 8, "slotInEpoch": 10}, epoch_length=400) is False


def test_default_start_time_is_unchanged_without_the_request(tmp_path):
    if subprocess.run(["bash", "-c", "command -v gdate || date -u -d @0 +%s"], capture_output=True).returncode != 0:
        pytest.skip("GNU date is required by the loader")
    done, shelley, byron = _run_set_start_time(tmp_path, slot_length=1, request=False)
    assert done.returncode == 0, done.stderr
    start, slot = (int(x) for x in done.stdout.split())
    assert start == 1800000000 - slot  # 1 s per slot, no lead
    assert byron["startTime"] == start
    assert shelley["systemStart"].endswith(":00Z")  # minute-aligned as before


def test_staged_loader_and_hook_are_valid_bash(tmp_path):
    scripts = tmp_path / "scripts"
    bootstrap._stage_loader_scripts(scripts)
    for name in ("cardano-loader.sh", "apply-local-reward-window.sh"):
        assert subprocess.run(["bash", "-n", str(scripts / name)], capture_output=True).returncode == 0


def test_serve_and_arm_retakes_a_snapshot_that_ended_past_the_deadline():
    source = Path(bridge.__file__).read_text(encoding="utf-8")
    serve = source[source.index("def serve_and_arm("):source.index("def _forge_record(")]
    check = serve.index("armable_after_snapshot(tip, after, epoch_length=genesis[\"epoch_length\"])")
    assert serve.index("cardano_before = consumer_pots(") < check < serve.index('before = {"amaru": amaru_before')
    assert "discarded_snapshots" in serve
