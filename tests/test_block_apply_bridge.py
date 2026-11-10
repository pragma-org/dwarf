"""Block-apply bridge: per-node verdict reading, pot alignment, forge plan, primitive contracts.

Live runs 20261109T153516Z-3cae5421 / 20261109T153907Z-8fab3645 / 20261109T153133Z-33835cf1 failed
because forge/serve were deferred stubs, the legacy scripts pointed at a host-specific forge-work folder, the
assertions returned no ``data_points_used`` (runner TypeError) and fixtures were never staged.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import cbor2
import pytest

from profile_manager import block_apply_primitives as bap
from profile_manager import submit_primitives as sp
from profile_manager.forensic import RunHandle
from scripts import praos_kes
from scripts import runtime_block_apply_bridge as bridge

FIX = Path(__file__).resolve().parent / "fixtures" / "block_apply"
FORGED = "ab" * 32
OTHER = "cd" * 32

# Line shapes copied from a live Amaru 10.11 consensus log (run 20261109T034454Z-f4000868).
AMARU_ADOPT_LOG = (
    f'2026-11-09T03:46:28.508162Z DEBUG amaru::consensus: chain.select_from_block_validation close '
    f'time.busy=2.26µs time.idle=32.5µs point="[314021, h\'{FORGED}\', 15525]" valid=true header_hash="{FORGED}" id=7\n'
    f'2026-11-09T03:46:28.508318Z DEBUG amaru::consensus: tip.adopt slot=314021 header_hash="{FORGED}" '
    f'block_height=15525 max_block_height=15525 suppressed=976\n'
)


def test_amaru_verdict_reads_validation_and_adoption_of_the_forged_hash():
    verdict = bridge.amaru_header_verdict(AMARU_ADOPT_LOG, FORGED)
    assert verdict["verdict"] == "adopt" and verdict["validated"] is True and verdict["adopted"] is True
    assert bridge.amaru_header_verdict(AMARU_ADOPT_LOG, OTHER)["verdict"] == "not-observed"


def test_amaru_verdict_reports_rejection_with_its_reason_lines():
    log = (f'... chain.select_from_block_validation close point="[1, h\'{FORGED}\', 2]" valid=false header_hash="{FORGED}"\n'
           f'... WARN amaru::consensus: block.invalid header_hash="{FORGED}" error="ledger rejected"\n')
    verdict = bridge.amaru_header_verdict(log, FORGED)
    assert verdict["verdict"] == "reject" and verdict["validated"] is False
    assert any("ledger rejected" in line for line in verdict["reason_lines"])


def test_amaru_pots_and_stable_point():
    log = "x pots.dump treasury=10 reserves=20 fees=3 donations=0\nx pots.dump treasury=11 reserves=19 fees=4 donations=5\n"
    assert bridge.amaru_last_pots(log) == {"treasury": 11, "reserves": 19, "fees": 4, "donations": 5}
    snapshot = {"tip_at_restart": {"block": 20, "slot": 400},
                "tips_before_restart": [{"block": b, "slot": b * 20} for b in range(10, 21)]}
    assert bridge.amaru_stable_slot(snapshot, 5) == 300


def test_cardano_pots_found_in_ledger_state_json():
    state = {"stateBefore": {"esChainAccountState": {"casTreasury": 7, "casReserves": 9},
                             "esLState": {"utxoState": {"fees": 2, "donation": 5, "deposited": 1}}}}
    assert bridge.cardano_pots(state) == {"treasury": 7, "reserves": 9, "fees": 2, "donations": 5}


@pytest.mark.parametrize("target,reference,expected", [
    ("adopt", "adopt", "adopt"), ("reject", "reject", "reject"),
    ("adopt", "reject", "accept_invalid"), ("reject", "adopt", "reject_valid"),
    ("adopt", "not-observed", "not-observed"),
])
def test_combine_verdicts(target, reference, expected):
    assert bridge.combine_verdicts({"verdict": target}, {"verdict": reference}) == expected


def test_combine_verdicts_crash_wins():
    assert bridge.combine_verdicts({"verdict": "not-observed", "crashed": True}, {"verdict": "adopt"}) == "crash"


def _snapshots(amaru_after_treasury, aligned=True, on_forged_chain=True):
    return {"aligned": aligned,
            "forged_chain": {"measured": on_forged_chain,
                             "reasons": [] if on_forged_chain else ["Amaru's stable ledger point is not on the forged chain"]},
            "amaru_before": {"treasury": 100, "donations": 0}, "cardano_before": {"treasury": 100, "donations": 0},
            "amaru_after": {"treasury": amaru_after_treasury, "donations": 0},
            "cardano_after": {"treasury": 130, "donations": 0}}


def test_value_creation_verdicts():
    assert bridge.value_creation_verdict(_snapshots(135), donation=5)["verdict"] == "VALUE_CREATION_DIVERGENCE"
    assert bridge.value_creation_verdict(_snapshots(130), donation=5)["verdict"] == "no_body_leak"
    assert bridge.value_creation_verdict(_snapshots(125), donation=5)["verdict"] == "REFERENCE_VALUE_CREATION"
    assert bridge.value_creation_verdict(_snapshots(131), donation=5)["verdict"] == "POT_DIVERGENCE_UNEXPLAINED"
    assert bridge.value_creation_verdict(_snapshots(135, aligned=False), donation=5)["verdict"] == "inconclusive"
    missing = _snapshots(135)
    del missing["cardano_after"]
    assert bridge.value_creation_verdict(missing, donation=5)["verdict"] == "inconclusive"


def test_repoint_replaces_every_peer_with_the_proxy_only():
    command = ('set -euo pipefail; exec "$$@" run --network "testnet_42" --peer-address "node2:3001" '
               '--peer-address "node3:3001" --listen-address 0.0.0.0:5001')
    rewritten = bridge.repoint_compose_command(command, "10.0.0.9:40000")
    assert rewritten.count("--peer-address") == 1 and '--peer-address "10.0.0.9:40000"' in rewritten
    assert "node2" not in rewritten and "0.0.0.0:5001" not in rewritten
    # Run 20261109T231115Z-ddca70a3: node2's topology dials node1, Amaru took that inbound duplex
    # connection as a second upstream and switched back to the honest chain 6 s after adopting the
    # forged block. The listener is bound to loopback (container-loopback probes keep working).
    assert "--listen-address 127.0.0.1:5001" in rewritten
    quoted = bridge.repoint_compose_command('run --peer-address "a:1" --listen-address "0.0.0.0:5001"', "p:2")
    assert '--listen-address "127.0.0.1:5001"' in quoted
    with pytest.raises(RuntimeError):
        bridge.repoint_compose_command("exec amaru run", "x:1")


# --------------------------------------------------------------------------- #
# The measured chain (false negative of run 20261109T231115Z-ddca70a3)
# --------------------------------------------------------------------------- #

ROLLED_BACK = json.loads((FIX / "donation-forged-block-rolled-back-20261109T231115Z-ddca70a3.json").read_text())


def _membership(snapshots, fixture=ROLLED_BACK):
    event = fixture["forged_event"]
    return bridge.forged_chain_membership(snapshots, forged_hashes=set(fixture["forged_hashes"]),
                                          forged_block=int(event["block_number"]), forged_slot=int(event["slot"]))


def test_amaru_stable_ledger_point_is_read_from_its_boot_log():
    text = "\n".join(ROLLED_BACK["amaru_log_excerpt"])
    assert bridge.amaru_ledger_tip(text) == {
        "slot": 2040, "hash": "06c4514398fad53a22e9ed432bac7d94ae493fd879bbfdb921aac7a76ee50ed7", "block": 412}
    assert bridge.amaru_ledger_tip("no ledger line") is None
    snapshot = {"ledger_tip": {"slot": 2040, "hash": "x", "block": 412}, "tip_at_restart": {"block": 415, "slot": 2051},
                "tips_before_restart": [{"block": 407, "slot": 2012}]}
    assert bridge.amaru_stable_slot(snapshot, 8) == 2040  # the ledger's own point, not a tip.adopt estimate


def test_amaru_inbound_peers_other_than_the_proxy_are_reported():
    text = "\n".join(ROLLED_BACK["amaru_log_excerpt"])
    assert bridge.amaru_foreign_peers(text, proxy_address=ROLLED_BACK["proxy_address"]) == ["10.0.0.3:3001"]
    loopback = 'x manager.peer.handshake_completed peer="127.0.0.1:48254" conn_id=2 full_duplex=true'
    assert bridge.amaru_foreign_peers(loopback, proxy_address="10.0.0.4:1") == []


def test_live_false_negative_is_not_measured_amaru_stable_point_off_the_forged_chain():
    snaps = dict(ROLLED_BACK["snapshots"])
    snaps["amaru_before"] = {**snaps["amaru_before"], "ledger_tip": {
        "slot": 1711, "hash": "d3d9d14265339d0b14b615d4308ec64397f85ad35d053bfc41691be87862f256", "block": 346}}
    snaps["amaru_after"] = {**snaps["amaru_after"], "ledger_tip": {
        "slot": 2040, "hash": "06c4514398fad53a22e9ed432bac7d94ae493fd879bbfdb921aac7a76ee50ed7", "block": 412}}
    membership = _membership(snaps)
    assert membership["measured"] is False
    assert any("amaru" in r.lower() and "forged chain" in r for r in membership["reasons"])
    # the reference consumer DID stay on the forged chain (its after tip ab6bd854... is a forged hash)
    assert not any(r.lower().startswith("cardano") for r in membership["reasons"])
    snaps["forged_chain"] = membership
    verdict = bridge.value_creation_verdict(snaps, donation=5000000)
    assert verdict["verdict"] == "inconclusive" and verdict["measured"] is False
    assert "forged chain" in verdict["reason"]


def test_forged_chain_membership_measured_only_when_both_stable_points_hold_the_forged_block():
    forged = sorted(ROLLED_BACK["forged_hashes"])
    event = ROLLED_BACK["forged_event"]
    on_chain = next(h for h in forged if h != event["forged_hash"])
    snaps = {
        "amaru_before": {"ledger_tip": {"slot": 1711, "hash": "aa" * 32, "block": 346}},
        "cardano_before": {"slot": 1773, "block": 357, "hash": "bb" * 32},
        "amaru_after": {"ledger_tip": {"slot": 2040, "hash": on_chain, "block": 412}},
        "cardano_after": {"slot": 2078, "block": 422, "hash": on_chain},
    }
    assert _membership(snaps) == {**_membership(snaps), "measured": True, "reasons": []}
    # a before snapshot that already holds the forged block cannot show its effect
    late = {**snaps, "cardano_before": {"slot": 1779, "block": 358, "hash": event["forged_hash"]}}
    assert _membership(late)["measured"] is False
    # no stable ledger point reported -> not measured (never assumed)
    blind = {**snaps, "amaru_after": {"tip_at_restart": {"slot": 2051, "hash": on_chain, "block": 415}}}
    assert _membership(blind)["measured"] is False
    # cardano on a forged hash but below the forged block height cannot be (guards a hash-set mixup)
    low = {**snaps, "cardano_after": {"slot": 1700, "block": 340, "hash": on_chain}}
    assert _membership(low)["measured"] is False


def test_value_creation_needs_a_forged_chain_measurement():
    missing = _snapshots(130)
    del missing["forged_chain"]
    assert bridge.value_creation_verdict(missing, donation=5)["verdict"] == "inconclusive"
    off = bridge.value_creation_verdict(_snapshots(130, on_forged_chain=False), donation=5)
    assert off["verdict"] == "inconclusive" and off["measured"] is False
    assert bridge.value_creation_verdict(_snapshots(135), donation=5)["measured"] is True


def test_epoch_of_uses_the_node_reported_anchor():
    anchor = {"anchor_slot": 1250, "anchor_epoch": 7, "anchor_slot_in_epoch": 250, "epoch_length": 500}
    assert bridge.epoch_of(1000, **anchor) == 7
    assert bridge.epoch_of(1499, **anchor) == 7
    assert bridge.epoch_of(1500, **anchor) == 8
    assert bridge.epoch_of(999, **anchor) == 6


def test_cardano_consumer_verdict():
    forged_set = {FORGED, OTHER}
    assert bridge.cardano_consumer_verdict(tip={"hash": OTHER, "block": 12}, forged_hashes=forged_set,
                                           forged_block=11, log_text="", forged_hash=FORGED)["verdict"] == "adopt"
    rejected = bridge.cardano_consumer_verdict(
        tip={"hash": "ee" * 32, "block": 10}, forged_hashes=forged_set, forged_block=11,
        log_text=f'{{"ns":"ChainDB.AddBlockEvent.AddBlockValidation.InvalidBlock","data":{{"block":"{FORGED}"}}}}',
        forged_hash=FORGED)
    assert rejected["verdict"] == "reject" and rejected["reason_lines"]
    assert bridge.cardano_consumer_verdict(tip=None, forged_hashes=forged_set, forged_block=11, log_text="",
                                           forged_hash=FORGED)["verdict"] == "not-observed"


def _runtime_root(tmp_path, *, hot_vk):
    pool = tmp_path / "env" / "pools-keys" / "pool1"
    pool.mkdir(parents=True)
    (pool / "kes.skey").write_bytes((FIX / "kes.skey").read_bytes())
    opcert = cbor2.dumps([[hot_vk, 0, 0, b"\x00" * 64], b"\x01" * 32])
    (pool / "opcert.cert").write_text(json.dumps({"type": "NodeOperationalCertificate", "cborHex": opcert.hex()}))
    (tmp_path / "env" / "shelley-genesis.json").write_text(json.dumps({
        "slotsPerKESPeriod": 129600, "epochLength": 500, "securityParam": 5, "activeSlotsCoeff": 0.05,
        "networkMagic": 42, "slotLength": 0.1}))
    return {"runtime_root": str(tmp_path), "network_magic": 42,
            "haskell_nodes": [{"id": "node2", "impl": "cardano-node", "host_slot_index": 1}]}


def test_forge_plan_uses_the_producer_pool_key_its_opcert_certifies(tmp_path):
    hot_vk = praos_kes.load_ver_key(FIX / "kes.vkey")
    runtime = _runtime_root(tmp_path / "rt", hot_vk=hot_vk)
    plan = bridge.write_forge_plan(runtime=runtime, out_dir=tmp_path / "out", producer_node="node2",
                                   transaction_files=["/x/crafted.signed"])
    assert plan["kes_skey"].endswith("pools-keys/pool1/kes.skey")
    assert plan["slots_per_kes_period"] == 129600 and plan["genesis"]["epoch_length"] == 500
    assert json.loads((tmp_path / "out" / "forge-plan.json").read_text())["arm_file"].endswith("arm.json")


def test_forge_plan_refuses_a_kes_key_the_opcert_does_not_certify(tmp_path):
    runtime = _runtime_root(tmp_path / "rt", hot_vk=b"\x02" * 32)
    with pytest.raises(RuntimeError, match="certifies"):
        bridge.write_forge_plan(runtime=runtime, out_dir=tmp_path / "out", producer_node="node2",
                                transaction_files=[])


# --------------------------------------------------------------------------- #
# Primitive / runner contract
# --------------------------------------------------------------------------- #

def _runner_accepts(result):
    signature = inspect.signature(RunHandle.assertion_result)
    signature.bind(None, **result)  # raises TypeError exactly like the live runner did


def _write_result(run_dir, rel, body):
    path = run_dir / rel / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))


def test_block_apply_outcome_found_under_a_custom_output_dir(tmp_path):
    _write_result(tmp_path, "outputs/setup-apply", {"primitive": "block_apply_differential", "verdict": "adopt",
                                                    "target": {"verdict": "adopt"}, "reference": {"verdict": "adopt"}})
    result = bap.BlockApplyOutcomeMatches(params={"expected_outcome": "adopt"}).evaluate(SimpleNamespace(run_dir=tmp_path))
    _runner_accepts(result)
    assert result["result"] == "pass" and result["data_points_used"][0]["target"] == {"verdict": "adopt"}


@pytest.mark.parametrize("cls,params", [
    (bap.BlockApplyOutcomeMatches, {"expected_outcome": "adopt"}),
    (bap.InvalidTxBodyDropped, {"expected_verdict": "VALUE_CREATION_DIVERGENCE"}),
    (bap.EpochBoundaryPotsMatch, {"expected_verdict": "POT_TRANSITION_DIVERGENCE"}),
    (sp.SubmitOutcomeMatches, {}),
])
def test_assertions_fail_visibly_without_evidence_and_satisfy_the_runner(tmp_path, cls, params):
    result = cls(params=params).evaluate(SimpleNamespace(run_dir=tmp_path))
    _runner_accepts(result)
    assert result["result"] == "fail"


def test_legacy_steps_without_bridge_state_stay_deferred(tmp_path):
    handle = SimpleNamespace(run_dir=tmp_path)
    assert bap.ServeCraftedBlock(params={"output_dir": "outputs/serve"}).run(handle, None)["outcome"] == "deferred"
    assert bap.ForgeBlock(params={"output_dir": "outputs/forge"}).run(handle, None)["outcome"] == "deferred"


def _observed_state(tmp_path, snapshots):
    bridge.save_state(tmp_path, {"craft": {"donation": 5}, "observe": {"verdict": "adopt", "snapshots": snapshots}})


def test_invalid_tx_body_differential_from_bridge_snapshots(tmp_path):
    _observed_state(tmp_path, _snapshots(135))
    handle = SimpleNamespace(run_dir=tmp_path)
    report = bap.InvalidTxBodyDifferential(params={"body_effect": "donation",
                                            "output_dir": "outputs/invalid-tx-body-differential"}).run(handle, None)
    assert report["verdict"] == "VALUE_CREATION_DIVERGENCE"
    result = bap.InvalidTxBodyDropped(params={"expected_verdict": "VALUE_CREATION_DIVERGENCE"}).evaluate(handle)
    assert result["result"] == "pass"
    _runner_accepts(result)


def test_epoch_boundary_pot_differential_from_aligned_snapshots(tmp_path):
    snaps = _snapshots(135)
    for name in ("amaru_before", "cardano_before", "amaru_after", "cardano_after"):
        snaps[name].update({"reserves": 1000, "fees": 0})
    _observed_state(tmp_path, snaps)
    handle = SimpleNamespace(run_dir=tmp_path)
    report = bap.EpochBoundaryPotDifferential(params={"output_dir": "outputs/epoch-boundary-pot-differential"}).run(handle, None)
    assert report["verdict"] == "POT_TRANSITION_DIVERGENCE"
    assert report["transition_diffs"] == {"treasury": {"amaru": 35, "cardano": 30}}
    assert bap.EpochBoundaryPotsMatch(params={"expected_verdict": "POT_TRANSITION_DIVERGENCE"}).evaluate(handle)["result"] == "pass"


def test_epoch_boundary_pot_differential_is_inconclusive_when_not_aligned(tmp_path):
    _observed_state(tmp_path, _snapshots(135, aligned=False))
    handle = SimpleNamespace(run_dir=tmp_path)
    report = bap.EpochBoundaryPotDifferential(params={"output_dir": "outputs/ebpd"}).run(handle, None)
    assert report["verdict"] == "inconclusive"
    assert bap.EpochBoundaryPotsMatch(params={"expected_verdict": "CONFORMANT"}).evaluate(handle)["result"] == "fail"


# --------------------------------------------------------------------------- #
# Fixture staging + submit assertion (gov scriptcontext run 20261109T153133Z-33835cf1)
# --------------------------------------------------------------------------- #

def test_epoch_boundary_pot_differential_is_inconclusive_off_the_forged_chain(tmp_path):
    snaps = _snapshots(135, on_forged_chain=False)
    for name in ("amaru_before", "cardano_before", "amaru_after", "cardano_after"):
        snaps[name].update({"reserves": 1000, "fees": 0})
    _observed_state(tmp_path, snaps)
    handle = SimpleNamespace(run_dir=tmp_path)
    report = bap.EpochBoundaryPotDifferential(params={"output_dir": "outputs/ebpd"}).run(handle, None)
    assert report["verdict"] == "inconclusive" and "forged chain" in report["reason"]


def test_epoch_boundary_fee_pot_is_not_compared_across_the_boundary(tmp_path):
    """Run 20261109T231944Z-8cf541e9 'passed' POT_TRANSITION_DIVERGENCE on fees alone (amaru 0 vs
    cardano 19000000) with identical treasury deltas: Amaru zeroes its stable fees pot at the epoch
    start (store/epoch_transition.rs reset_fees_and_donations) while cardano-node's utxosFees keeps the
    epoch's fees until the reward update, and Amaru's before point is its stable ledger point, not the
    tip. The fee pots are not the same quantity, so only treasury and reserves are compared."""
    snaps = {"aligned": True, "forged_chain": {"measured": True, "reasons": []},
             "amaru_before": {"treasury": 78145453587820, "reserves": 43360786902851410, "fees": 0, "donations": 0},
             "amaru_after": {"treasury": 104162265929530, "reserves": 43334236760282180, "fees": 0, "donations": 0},
             "cardano_before": {"treasury": 78145453587820, "reserves": 43360786902851410, "fees": 1000000},
             "cardano_after": {"treasury": 104162265929530, "reserves": 43334236760282180, "fees": 20000000}}
    _observed_state(tmp_path, snaps)
    handle = SimpleNamespace(run_dir=tmp_path)
    report = bap.EpochBoundaryPotDifferential(params={"output_dir": "outputs/ebpd"}).run(handle, None)
    assert report["verdict"] == "CONFORMANT" and report["transition_diffs"] == {}
    assert "fees" in report["not_compared"]
    snaps["amaru_after"]["treasury"] += 5000000  # the donation realized into Amaru's treasury
    _observed_state(tmp_path, snaps)
    report = bap.EpochBoundaryPotDifferential(params={"output_dir": "outputs/ebpd"}).run(handle, None)
    assert report["verdict"] == "POT_TRANSITION_DIVERGENCE"
    assert report["transition_diffs"] == {"treasury": {"amaru": 26016817341710, "cardano": 26016812341710}}


def test_fixture_paths_are_staged_into_the_run_dir(tmp_path):
    staged, record = sp.stage_fixture(tmp_path, "fixture/gov_scriptcontext/prop3.tx")
    assert staged == tmp_path / "fixture/gov_scriptcontext/prop3.tx" and staged.is_file()
    assert record["staged"] is True and record["source"].endswith(
        "antithesis/cardano_amaru_adversarial/fixture/gov_scriptcontext/prop3.tx")
    staged2, record2 = sp.stage_fixture(tmp_path, "antithesis/cardano_amaru_adversarial/fixture/gov_scriptcontext/vote.tx")
    assert staged2.is_file() and record2["staged"] is True
    with pytest.raises(FileNotFoundError):
        sp.stage_fixture(tmp_path, "fixture/does-not-exist.tx")


def test_submit_outcome_checks_every_submission_against_its_own_expectation(tmp_path):
    def submitted(rel, target, reference, body=""):
        _write_result(tmp_path, rel, {"primitive": "runtime_tx_submit_differential", "tx_file": rel,
                                      "expected_target": "accept", "expected_reference": "accept",
                                      "target": {"outcome": target, "body": body},
                                      "reference": {"outcome": reference, "body": body},
                                      "inputs_unknown_on_live_chain": {"target": "badinputs" in body,
                                                                       "reference": "badinputs" in body}})
    submitted("outputs/obs-proposals", "accept", "accept")
    submitted("outputs/obs-votes", "reject", "reject", body="badinputsutxo")
    result = sp.SubmitOutcomeMatches(params={}).evaluate(SimpleNamespace(run_dir=tmp_path))
    _runner_accepts(result)
    assert result["result"] == "fail" and result["evaluated_value"] == {"submissions": 2, "mismatched": 1}
    assert "stale fixture" in result["note"]


def test_a_tip_at_origin_counts_as_not_synced(monkeypatch):
    """cardano-cli omits block/slot/hash at genesis; the sync wait must keep waiting, not crash."""
    from scripts import runtime_block_apply_bridge as bridge

    origin = '{"era": "Conway", "syncProgress": "0.00"}'
    synced = '{"block": 12, "slot": 240, "hash": "ab", "epoch": 0, "slotInEpoch": 240}'
    monkeypatch.setattr(bridge, "consumer_query", lambda name, magic, *args, timeout=120: origin)
    monkeypatch.setattr(bridge, "_checked", lambda argv, cwd=None, timeout=120: origin)
    assert bridge.consumer_tip("ref", 42) is None
    assert bridge.producer_tip({"container_name": "p", "container_socket_path": "/s"}, 42) is None
    monkeypatch.setattr(bridge, "consumer_query", lambda name, magic, *args, timeout=120: synced)
    monkeypatch.setattr(bridge, "_checked", lambda argv, cwd=None, timeout=120: synced)
    assert bridge.consumer_tip("ref", 42)["block"] == 12
    assert bridge.producer_tip({"container_name": "p", "container_socket_path": "/s"}, 42)["block"] == 12
