"""Gov ScriptContext scenario built live on the run's own devnet (recipe ``gov-scriptcontext``).

Live run 20261109T194628Z-e3110190 failed: the fixture transactions reg/pplain/prop3/vote spend
UTxOs of the 97b0 re-bake (Amaru: "unknown (but required) transaction input"), the block-apply
steps stayed deferred, and the cardano-node reference had no submit-API, so its outcome was
``unknown``. The recipe rebuilds the same transaction shapes against the live chain; these tests pin
the shapes to the original fixtures and the per-node evidence contract.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import cbor2
import pytest

from profile_manager import block_apply_primitives as bap
from profile_manager import submit_primitives as sp
from scripts import runtime_block_apply_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "antithesis" / "cardano_amaru_adversarial" / "fixture" / "gov_scriptcontext"
SCENARIO = ROOT / "dwarf" / "scenarios" / "conway-gov-scriptcontext-proposals-votes-fidelity-amaru-cardano-node.yaml"
POOL_97B0 = "97b0f582dcf255b2c776f3540454b99fbf26e48833f275bfcd63bfcd"
STAKE_CFDF = "cfdf1e640d7b1eb39f1491496f563207fc71613a7d9c2f110431414b"
PPLAIN_97B0 = "736a51abc6ff661e14be3765b43ae192939a36314ef9492b0ba6a7bc6d28990e"
DEPOSIT = 100_000_000_000


def _tx(name: str) -> bytes:
    return bytes.fromhex(json.loads((FIXTURE / name).read_text())["cborHex"])


def _envelope(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text())


# --------------------------------------------------------------------------- #
# Scripts: the fixture observers, and ctx_votes re-parameterised with the live pool
# --------------------------------------------------------------------------- #

def test_observer_policy_ids_are_the_ones_the_fixture_transactions_mint_under():
    assert bridge.script_policy_id(_envelope("ctx_proposal_is_info.plutus")) == "241b6413760642c796d45de6633fbc914c285afb19b1520542b216dc"
    assert bridge.script_policy_id(_envelope("ctx_proposal_deposit.plutus")) == "4cbce6357fe5b6ffc333f2427c03d4bd0cb5f68ec777900cfc1b8d51"
    assert bridge.script_policy_id(_envelope("ctx_num_proposals.plutus")) == "dbd0b52f4e5b8a1ec875c352606ccc0c05b307b7e6c75e12da35e798"
    assert bridge.script_policy_id(_envelope("ctx_votes.plutus")) == "819ee9e1df015dde8cc01386653d388ee7bbebd21de16fdb0398d582"


def test_ctx_votes_is_reparameterised_with_the_live_pool_and_nothing_else():
    original = _envelope("ctx_votes.plutus")
    live = bridge.parameterize_ctx_votes(original, "aa" * 28)
    before, after = bytes.fromhex(original["cborHex"]), bytes.fromhex(live["cborHex"])
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(changed) <= 28 and after[changed[0]:changed[0] + 1] == b"\xaa"
    assert bridge.script_policy_id(live) != bridge.script_policy_id(original)
    assert bridge.parameterize_ctx_votes(live, POOL_97B0, current=("aa" * 28))["cborHex"] == original["cborHex"]
    with pytest.raises(ValueError):
        bridge.parameterize_ctx_votes(original, "aa" * 27)
    with pytest.raises(ValueError):
        bridge.parameterize_ctx_votes(_envelope("ctx_num_proposals.plutus"), "aa" * 28)


def test_key_hash_matches_the_fixture_stake_key_registered_by_reg_tx():
    assert bridge.key_hash_from_vkey(FIXTURE / "stake.vkey") == STAKE_CFDF


# --------------------------------------------------------------------------- #
# What each transaction exercises (read from the fixture CBOR itself)
# --------------------------------------------------------------------------- #

def test_describe_reads_the_four_fixture_shapes():
    reg = bridge.describe_gov_transaction(_tx("reg.tx"))
    assert reg["certificates"] == [{"tag": 7, "credential": STAKE_CFDF, "deposit": 2_000_000}]
    pplain = bridge.describe_gov_transaction(_tx("pplain.tx"))
    assert pplain["proposals"] == [{"deposit": DEPOSIT, "return_account": STAKE_CFDF, "action": 6}]
    assert pplain["mint_policies"] == []
    prop3 = bridge.describe_gov_transaction(_tx("prop3.tx"))
    assert prop3["mint_policies"] == sorted(["241b6413760642c796d45de6633fbc914c285afb19b1520542b216dc",
                                             "4cbce6357fe5b6ffc333f2427c03d4bd0cb5f68ec777900cfc1b8d51",
                                             "dbd0b52f4e5b8a1ec875c352606ccc0c05b307b7e6c75e12da35e798"])
    assert prop3["redeemers"] == {"mint:0": 1, "mint:1": DEPOSIT, "mint:2": 1}
    vote = bridge.describe_gov_transaction(_tx("vote.tx"))
    assert vote["votes"] == [{"voter_tag": 4, "voter": POOL_97B0, "action_tx": PPLAIN_97B0, "action_index": 0, "vote": 1}]
    assert all(d["is_valid"] is True for d in (reg, pplain, prop3, vote))


def test_subject_checks_accept_the_fixture_shapes_and_refuse_others():
    prop3 = bridge.describe_gov_transaction(_tx("prop3.tx"))
    assert bridge.check_prop3_subject(prop3, deposit=DEPOSIT, return_account=STAKE_CFDF) is None
    assert "policies" in bridge.check_prop3_subject(bridge.describe_gov_transaction(_tx("pplain.tx")),
                                                    deposit=DEPOSIT, return_account=STAKE_CFDF)
    assert "deposit" in bridge.check_prop3_subject(prop3, deposit=DEPOSIT + 1, return_account=STAKE_CFDF)
    vote = bridge.describe_gov_transaction(_tx("vote.tx"))
    votes_policy = bridge.script_policy_id(_envelope("ctx_votes.plutus"))
    assert bridge.check_vote_subject(vote, pool=POOL_97B0, action_tx=PPLAIN_97B0, policy=votes_policy) is None
    assert "voter" in bridge.check_vote_subject(vote, pool="aa" * 28, action_tx=PPLAIN_97B0, policy=votes_policy)
    reg = bridge.describe_gov_transaction(_tx("reg.tx"))
    assert bridge.check_setup_pair(reg, bridge.describe_gov_transaction(_tx("pplain.tx")),
                                   stake_deposit=2_000_000, deposit=DEPOSIT) is None
    assert "registers" in bridge.check_setup_pair(bridge.describe_gov_transaction(_tx("pplain.tx")), reg,
                                                  stake_deposit=2_000_000, deposit=DEPOSIT)


# --------------------------------------------------------------------------- #
# The cardano-cli commands the live run executes
# --------------------------------------------------------------------------- #

def test_split_plan_balances_and_funds_every_role():
    plan = bridge.gov_split_plan(funding_lovelace=1_000_000_000_000, stake_deposit=2_000_000, gov_deposit=DEPOSIT, fee=2_000_000)
    roles = [o["role"] for o in plan["outputs"]]
    assert roles[:7] == ["pplain_h", "prop3", "prop3_collateral", "vote", "vote_collateral", "reg_f", "pplain_f"]
    assert roles[-1] == "change"
    assert sum(o["lovelace"] for o in plan["outputs"]) + plan["fee"] + 2_000_000 == 1_000_000_000_000
    assert plan["outputs"][roles.index("prop3")]["lovelace"] > DEPOSIT
    with pytest.raises(RuntimeError):
        bridge.gov_split_plan(funding_lovelace=DEPOSIT, stake_deposit=2_000_000, gov_deposit=DEPOSIT, fee=2_000_000)


def test_prop3_build_command_mints_under_the_three_observers_with_the_live_deposit_redeemer():
    scripts = {"is_info": ("/work/is_info.plutus", "241b"), "deposit": ("/work/deposit.plutus", "4cbc"),
               "num_proposals": ("/work/num.plutus", "dbd0")}
    cmd = bridge.prop3_build_command(cli="cardano-cli", socket_path="/s", magic=42, tx_in="aa#1", collateral="aa#2",
                                     scripts=scripts, deposit=DEPOSIT, action_file="/work/info-prop3.action",
                                     change_address="addr_test1x", out_file="/work/prop3.body")
    assert cmd[:4] == ["cardano-cli", "conway", "transaction", "build"]
    mint = cmd[cmd.index("--mint") + 1]
    assert mint == "1 241b.434f4c4c + 1 4cbc.434f4c4c + 1 dbd0.434f4c4c"
    pairs = [(cmd[i + 1], cmd[i + 3]) for i, a in enumerate(cmd) if a == "--mint-script-file"]
    assert pairs == [("/work/is_info.plutus", "1"), ("/work/deposit.plutus", str(DEPOSIT)), ("/work/num.plutus", "1")]
    assert cmd[cmd.index("--proposal-file") + 1] == "/work/info-prop3.action"
    assert cmd[cmd.index("--tx-in-collateral") + 1] == "aa#2"


def test_vote_commands_use_the_producer_pool_cold_key():
    create = bridge.vote_create_command(cli="c", action_tx=PPLAIN_97B0, cold_vkey="/env/pools-keys/pool1/cold.vkey",
                                        out_file="/work/vote.file")
    assert create == ["c", "conway", "governance", "vote", "create", "--yes", "--governance-action-tx-id", PPLAIN_97B0,
                      "--governance-action-index", "0", "--cold-verification-key-file",
                      "/env/pools-keys/pool1/cold.vkey", "--out-file", "/work/vote.file"]
    build = bridge.vote_build_command(cli="c", socket_path="/s", magic=42, tx_in="bb#4", collateral="bb#5",
                                      script=("/work/ctx_votes.plutus", "819e"), vote_file="/work/vote.file",
                                      change_address="addr_test1x", out_file="/work/vote.body")
    assert build[build.index("--mint") + 1] == "1 819e.434f4c4c"
    assert build[build.index("--vote-file") + 1] == "/work/vote.file"
    assert build[build.index("--witness-override") + 1] == "2"


# --------------------------------------------------------------------------- #
# Primitive wiring
# --------------------------------------------------------------------------- #

def test_build_block_segments_dispatches_the_gov_recipe(tmp_path, monkeypatch):
    runtime = tmp_path / "outputs/substrate-compose/runtime.json"
    runtime.parent.mkdir(parents=True)
    runtime.write_text(json.dumps({"runtime_root": str(tmp_path)}))
    seen = {}

    def craft(**kwargs):
        seen.update(kwargs)
        return {"recipe": bridge.GOV_SCRIPTCONTEXT_RECIPE, "transaction_files": ["/x/reg-f.signed", "/x/pplain-f.signed"]}

    monkeypatch.setattr(bridge, "craft_gov_scriptcontext", craft)
    result = bap.BuildBlockSegments(params={"recipe": "gov-scriptcontext", "reference_node": "node2",
                                            "target_node": "node1", "output_dir": "outputs/build-segments"}
                                    ).run(SimpleNamespace(run_dir=tmp_path), None)
    assert result["outcome"] == "ok" and seen["reference_node"] == "node2" and seen["target_node"] == "node1"
    assert bridge.load_state(tmp_path)["craft"]["transaction_files"] == ["/x/reg-f.signed", "/x/pplain-f.signed"]


def test_an_unbuilt_subject_is_recorded_as_not_measured_with_its_reason(tmp_path):
    subjects = tmp_path / "outputs/build-segments/subjects"
    subjects.mkdir(parents=True)
    (subjects / "vote.tx.unbuilt.json").write_text(json.dumps({"measured": False, "reason": "build failed: X"}))
    handle = SimpleNamespace(run_dir=tmp_path)
    result = sp.RuntimeTxSubmitDifferential(params={
        "tx_file": "outputs/build-segments/subjects/vote.tx", "output_dir": "outputs/obs-votes",
        "expected_target": "accept", "expected_reference": "accept"}).run(handle, None)
    assert result["measured"] is False and "build failed: X" in result["reason"]
    assert result["target"]["outcome"] == "not-measured" and result["reference"]["outcome"] == "not-measured"
    verdict = sp.SubmitOutcomeMatches(params={}).evaluate(handle)
    assert verdict["result"] == "fail" and "build failed: X" in verdict["note"]


def _runtime_with_cardano_reference(tmp_path):
    path = tmp_path / "outputs/substrate-compose/runtime.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "runtime_root": str(tmp_path), "network_magic": 42,
        "amaru_nodes": [{"id": "node1", "impl": "amaru", "submit_api_url": "http://node1:3011/api/submit/tx"}],
        "haskell_nodes": [{"id": "node2", "impl": "cardano-node", "container_socket_path": "/env/socket/node2/sock"}],
    }))
    tx = tmp_path / "outputs/build-segments/subjects/prop3.tx"
    tx.parent.mkdir(parents=True, exist_ok=True)
    tx.write_text(json.dumps({"type": "Tx ConwayEra", "cborHex": _tx("prop3.tx").hex()}))
    return path


def test_a_cardano_node_without_submit_api_is_submitted_through_its_own_cli_after_the_target(tmp_path, monkeypatch):
    _runtime_with_cardano_reference(tmp_path)
    order = []
    monkeypatch.setattr(sp, "_submit", lambda url, payload, timeout=25: order.append(("http", url)) or
                        {"http": 202, "outcome": "accept", "body": "ok"})

    def cli_submit(*, run_dir, runtime_metadata_path, node_id, tx_path, out_dir):
        order.append(("cli", node_id))
        assert Path(tx_path).is_file()
        return {"http": 202, "outcome": "accept", "body": "accepted", "via": "cardano-cli"}

    monkeypatch.setattr(sp, "_submit_via_cardano_cli", cli_submit)
    result = sp.RuntimeTxSubmitDifferential(params={
        "tx_file": "outputs/build-segments/subjects/prop3.tx", "output_dir": "outputs/obs-proposals",
        "runtime_metadata_path": "outputs/substrate-compose/runtime.json", "target_node": "node1",
        "reference_node": "node2", "expected_target": "accept", "expected_reference": "accept"}
    ).run(SimpleNamespace(run_dir=tmp_path), None)
    assert order == [("http", "http://node1:3011/api/submit/tx"), ("cli", "node2")]
    assert result["reference"]["outcome"] == "accept" and result["reference"]["via"] == "cardano-cli"
    assert result["divergent"] is False


@pytest.mark.parametrize("response,mempool,chain,expected", [
    ({"http": 202, "body": "accepted", "node_response_observed": True}, None, None, ("accept", None)),
    ({"http": 400, "body": "ShelleyTxValidationError ... FailedUnexpectedly", "node_response_observed": True}, False, False, ("reject", None)),
    ({"http": 400, "body": "BadInputsUTxO", "node_response_observed": True}, True, False, ("accept", "mempool")),
    ({"http": 400, "body": "BadInputsUTxO", "node_response_observed": True}, False, True, ("accept", "chain")),
    ({"http": 400, "body": "BadInputsUTxO", "node_response_observed": True}, False, False, ("reject", None)),
    ({"http": 0, "body": "", "node_response_observed": False}, None, None, ("not-measured", None)),
])
def test_cli_submission_outcome_resolution(response, mempool, chain, expected):
    resolved = sp.resolve_cli_submission(response, tx_in_mempool=mempool, tx_on_chain=chain)
    assert (resolved["outcome"], resolved.get("accepted_via")) == expected


def test_amaru_unknown_input_wording_is_flagged_as_stale_inputs():
    body = ("failed to prepare transaction d2d1 for validation: failed to hydrate validation context: "
            "unknown (but required) transaction input or reference input: df53#0")
    assert sp._unknown_inputs({"body": body}) is True


# --------------------------------------------------------------------------- #
# Scenario
# --------------------------------------------------------------------------- #

def _scenario():
    return json.loads(SCENARIO.read_text())


def test_scenario_builds_live_and_measures_subjects_before_the_fork():
    scenario = _scenario()
    setup = {s["primitive"]: s for s in scenario["setup"]}
    build = setup["build_block_segments"]
    assert build["recipe"] == "gov-scriptcontext" and build["reference_node"] == "node2" and build["target_node"] == "node1"
    assert "tx_file" not in build and "extra_tx_files" not in build
    forge = setup["forge_block"]
    for stale in ("forced_nonce", "pool_keys_dir", "slot", "prev_hash", "block_height"):
        assert stale not in forge
    assert "assemble_blockjson" not in setup
    load = [s["primitive"] for s in scenario["load"]]
    submits = [i for i, p in enumerate(load) if p == "runtime_tx_submit_differential"]
    assert len(submits) == 2 and max(submits) < load.index("serve_crafted_block") < load.index("block_apply_differential")
    for step in scenario["load"]:
        if step["primitive"] == "runtime_tx_submit_differential":
            assert step["tx_file"].startswith("outputs/build-segments/subjects/")
            assert step["expected_target"] == step["expected_reference"] == "accept"
    assert "keys-97b0" not in SCENARIO.read_text() and "fixture/gov_scriptcontext" not in SCENARIO.read_text()


# --------------------------------------------------------------------------- #
# Orchestration dry run: fake cardano-cli, real control flow and real checks
# --------------------------------------------------------------------------- #

def _dry_run(tmp_path, monkeypatch, *, target_log_text):
    import contextlib

    from scripts import runtime_cardano_measurement_calibration as calibration
    from scripts import runtime_redeemer_container_live_controls as live_controls
    from scripts import runtime_transaction_array_arity as arity

    pool = tmp_path / "rt" / "env" / "pools-keys" / "pool1"
    pool.mkdir(parents=True)
    (pool / "cold.vkey").write_text((FIXTURE / "stake.vkey").read_text())  # a real vkey; its hash is cfdf..
    log = tmp_path / "node1.log"
    log.write_text(target_log_text)
    runtime = {"runtime_root": str(tmp_path / "rt"), "network_magic": 42,
               "haskell_nodes": [{"id": "node2", "impl": "cardano-node", "host_slot_index": 1,
                                  "container_socket_path": "/env/socket/node2/sock"}],
               "amaru_nodes": [{"id": "node1", "impl": "amaru", "log_path": str(log)}]}
    signed_as = {"pplain-h": "pplain.tx", "reg-f": "reg.tx", "pplain-f": "pplain.tx", "prop3": "prop3.tx",
                 "vote": "vote.tx", "split": "reg.tx"}
    submitted = []

    def fake_cli(cmd, *, cwd=None, timeout=120):
        out = Path(cmd[cmd.index("--out-file") + 1]) if "--out-file" in cmd else None
        if "protocol-parameters" in cmd:
            out.write_text(json.dumps({"stakeAddressDeposit": 2_000_000, "govActionDeposit": DEPOSIT}))
        elif "key-gen" in cmd:
            Path(cmd[cmd.index("--verification-key-file") + 1]).write_text((FIXTURE / "stake.vkey").read_text())
            Path(cmd[cmd.index("--signing-key-file") + 1]).write_text("{}")
        elif "sign" in cmd:
            out.write_text((FIXTURE / signed_as[out.stem]).read_text())
        elif out is not None:
            out.write_text("{}")
        return ""

    @contextlib.contextmanager
    def workload(**kwargs):
        yield {"support_binaries": {"cardano-cli": "cli"}}, kwargs["node"]

    def tx_id(cli, path):
        return PPLAIN_97B0 if Path(path).stem == "pplain-h" else hashlib.sha256(str(path).encode()).hexdigest()

    monkeypatch.setattr(bridge, "_checked", fake_cli)
    monkeypatch.setattr(bridge, "producer_tip", lambda node, magic: {"block": 11, "slot": 880, "hash": "dd" * 32})
    monkeypatch.setattr(calibration, "_controller_container", lambda: {})
    monkeypatch.setattr(calibration, "_containerized_cardano_workload", workload)
    monkeypatch.setattr(calibration, "_query_utxo", lambda **k: {"aa" * 32 + "#0": {"value": {"lovelace": 10 ** 15}}})
    monkeypatch.setattr(calibration, "_transaction_id", tx_id)
    monkeypatch.setattr(calibration, "_wait_for_utxo", lambda **k: ({}, 1.0))
    monkeypatch.setattr(live_controls, "_submit_reference_cli",
                        lambda cli, path, **k: submitted.append(Path(path).stem) or {"http": 202, "body": "accepted"})
    monkeypatch.setattr(arity, "_funding_credentials", lambda rt, root: [
        {"id": "utxo1", "address": "addr_test1x", "container_skey": "/env/utxo-keys/utxo1/utxo.skey"}])
    out = tmp_path / "run" / "outputs" / "build-segments"
    record = bridge.craft_gov_scriptcontext(runtime=runtime, out_dir=out, reference_node="node2",
                                            target_node="node1", adoption_timeout=0.5)
    return record, out, submitted


def test_craft_submits_only_the_honest_setup_then_builds_forged_pair_and_subjects(tmp_path, monkeypatch):
    record, out, submitted = _dry_run(tmp_path, monkeypatch, target_log_text=(
        'x tip.adopt slot=900 header_hash="' + "ee" * 32 + '" block_height=12\n'))
    assert submitted == ["split", "pplain-h"]
    assert record["target_adoption"]["adopted"] is True
    assert [Path(p).stem for p in record["transaction_files"]] == ["reg-f", "pplain-f"]
    assert record["gov_action"] == {"transaction_id": PPLAIN_97B0, "index": 0}
    # prop3 has the fixture shape for this credential and deposit -> built and staged as a subject
    assert record["subjects"]["prop3"]["measured"] is True and (out / "subjects" / "prop3.tx").is_file()
    # the fixture vote is cast by pool 97b0, not this devnet's pool -> refused, never substituted
    assert record["subjects"]["vote"]["measured"] is False and "voter" in record["subjects"]["vote"]["reason"]
    assert not (out / "subjects" / "vote.tx").exists() and (out / "subjects" / "vote.tx.unbuilt.json").is_file()
    commands = record["cardano_cli_commands"]
    assert any(c[:4] == ["cardano-cli", "conway", "transaction", "build"] and "--proposal-file" in c
               and str(DEPOSIT) in c for c in commands)
    assert any(c[:5] == ["cardano-cli", "conway", "governance", "vote", "create"]
               and "/env/pools-keys/pool1/cold.vkey" in c for c in commands)
    assert not list((out / "keys").glob("*.skey"))


def test_craft_leaves_subjects_unmeasured_when_the_target_never_adopts_the_setup(tmp_path, monkeypatch):
    record, out, _submitted = _dry_run(tmp_path, monkeypatch, target_log_text="")
    assert record["target_adoption"]["adopted"] is False
    assert record["transaction_files"]  # the forged pair is still built for the block-apply step
    for name in ("prop3", "vote"):
        assert record["subjects"][name]["measured"] is False
        reason = json.loads((out / "subjects" / f"{name}.tx.unbuilt.json").read_text())["reason"]
        assert "did not adopt the honest setup" in reason
