"""Epoch / stake / reward / leadership producer: real per-node measurement, no forcing, no constants."""
import json
from types import SimpleNamespace

import pytest

from scripts import runtime_epoch_boundary_probe as probe

EPOCH_LENGTH = 100
SLOT_LENGTH = 0.1
SYSTEM_START = 1_000_000.0
SUPPLY = 45_000_000_000_000_000


class FakeDevnet:
    """Answers `docker exec <container> cardano-cli ...` like a tiny testnet_42 devnet.

    The ledger tip is the wall-clock slot (a block every slot) unless `tip_lag` is set; stake
    snapshots rotate at each boundary; pots conserve ADA unless `leak` is set."""

    def __init__(self, start_slot=95, nodes=("node1", "node2")):
        self.t = SYSTEM_START + start_slot * SLOT_LENGTH
        self.nodes = nodes
        self.leak = 0
        self.schedule_by_node = {}
        self.calls = []
        self.epoch_offset = {}  # node -> wrong epoch reporting
        self.missing = set()

    # clock seams
    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds

    def slot(self):
        return int((self.t - SYSTEM_START) / SLOT_LENGTH)

    def stake_snapshot(self, epoch):
        mark = 1000 + epoch
        return {"pools": {"aa": {"stakeMark": mark, "stakeSet": mark - 1, "stakeGo": mark - 2}},
                "total": {"stakeMark": mark, "stakeSet": mark - 1, "stakeGo": mark - 2}}

    def ledger_state(self, epoch):
        rewards = 10 * epoch
        reserves = 1_000_000 - 30 * epoch
        treasury = 20 * epoch
        utxo = SUPPLY - reserves - treasury - rewards - 500 - 7 + self.leak
        return {"lastEpoch": epoch, "stateBefore": {
            "esAccountState": {"reserves": reserves, "treasury": treasury},
            "esLState": {
                "utxoState": {"deposited": 500, "fees": 7, "donation": 0,
                              "utxo": {"tx#0": {"address": "a", "value": {"lovelace": utxo}}}},
                "delegationState": {"dstate": {"accounts": {"cred1": {"balance": rewards, "deposit": 2}}}},
            }}}

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        assert argv[:2] == ["docker", "exec"], argv
        container, cmd = argv[2], argv[3:]
        node = container.replace("c-", "")
        if cmd[0] == "cat":
            if cmd[1] == "/env/shelley-genesis.json":
                return self._ok(json.dumps({"epochLength": EPOCH_LENGTH, "slotLength": SLOT_LENGTH,
                                            "activeSlotsCoeff": 0.5, "securityParam": 5,
                                            "systemStart": "1970-01-12T13:46:40Z",
                                            "maxLovelaceSupply": SUPPLY}))
            if cmd[1] == "/env/configuration.yaml":
                return self._ok("TestShelleyHardForkAtEpoch: 0\n")
        assert cmd[0] == "cardano-cli", cmd
        rest = cmd[1:]
        if rest[0] == "latest":
            rest = rest[1:]
        if rest[:2] == ["stake-pool", "id"]:
            pool_dir = rest[rest.index("--cold-verification-key-file") + 1]
            return self._ok(pool_dir.split("/")[-2] + "-id\n")
        assert rest[0] == "query", rest
        name = rest[1]
        slot = self.slot()
        epoch = slot // EPOCH_LENGTH + self.epoch_offset.get(node, 0)
        if name in self.missing:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"{name} unsupported")
        if name == "tip":
            return self._ok(json.dumps({"slot": slot, "epoch": epoch, "hash": f"h{slot}", "block": slot,
                                        "slotInEpoch": slot % EPOCH_LENGTH,
                                        "slotsToEpochEnd": EPOCH_LENGTH - slot % EPOCH_LENGTH}))
        if name == "stake-snapshot":
            return self._ok(json.dumps(self.stake_snapshot(epoch)))
        if name == "ledger-state":
            return self._ok(json.dumps(self.ledger_state(epoch)))
        if name == "leadership-schedule":
            slots = self.schedule_by_node.get(node, [3, 17, 42])
            return self._ok(json.dumps([{"slotNumber": s, "slotTime": "x"} for s in slots]))
        raise AssertionError(argv)

    @staticmethod
    def _ok(stdout):
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _metadata(nodes=("node1", "node2"), amaru=()):
    haskell = [{"id": n, "impl": "cardano-node", "container_name": f"c-{n}", "host_slot_index": i + 1,
                "container_socket_path": f"/env/socket/{n}/sock"} for i, n in enumerate(nodes)]
    amaru_nodes = [{"id": n, "impl": "amaru", "log_path": str(path)} for n, path in amaru]
    return {"network_magic": 42, "nodes": haskell + amaru_nodes, "haskell_nodes": haskell, "amaru_nodes": amaru_nodes}


@pytest.fixture
def devnet(monkeypatch):
    net = FakeDevnet()
    monkeypatch.setattr(probe, "_run_cmd", net)
    monkeypatch.setattr(probe, "_now", net.now)
    monkeypatch.setattr(probe, "_sleep", net.sleep)
    return net


def _section(mode, metadata, **config):
    out = probe.apply_epoch_boundary_mode(metadata=metadata, mode=mode,
                                          config={"target_node": "node1", "sample_interval_seconds": 0.3, **config})
    return out["result"][probe.SECTION_BY_MODE[mode]]


def test_epoch_boundary_is_observed_on_every_node_with_genesis_window(devnet):
    section = _section("force_epoch_boundary", _metadata(), target_slot=1000)
    assert section["measured"] is True
    assert section["expected_window_start"] == 100
    assert 100 <= section["observed_boundary_slot"] <= section["expected_window_end"]
    assert section["all_nodes_within_bounds"] is True
    assert set(section["per_node"]) == {"node1", "node2"}
    assert section["per_node"]["node2"]["epoch_math_consistent"] is True
    assert section["requested_target_slot_is_epoch_start"] is True
    assert "cardano-cli query tip" in section["per_node"]["node1"]["source"]


def test_epoch_boundary_flags_node_whose_epoch_math_disagrees_with_genesis(devnet):
    devnet.epoch_offset["node2"] = 1
    section = _section("force_epoch_boundary", _metadata())
    assert section["per_node"]["node2"]["epoch_math_consistent"] is False
    assert section["per_node"]["node2"]["within_bounds"] is False
    assert section["all_nodes_within_bounds"] is False


def test_epoch_boundary_not_reached_within_budget_is_not_measured(devnet):
    devnet.t = SYSTEM_START + 5 * SLOT_LENGTH  # epoch 0 slot 5; boundary 9.5 s away
    section = _section("force_epoch_boundary", _metadata(), time_budget_seconds=3)
    assert section["measured"] is False
    assert section["observed_boundary_slot"] is None
    assert section["all_nodes_within_bounds"] is False
    assert "no epoch boundary observed" in section["per_node"]["node1"]["reason"]


def test_stake_snapshot_is_stable_in_epoch_and_rotates_at_boundary(devnet):
    devnet.t = SYSTEM_START + 90 * SLOT_LENGTH
    section = _section("simulate_stake_snapshot_update", _metadata())
    assert section["measured"] is True
    assert len(section["snapshot_hashes"]) >= 2 and len(set(section["snapshot_hashes"])) == 1
    assert section["rotation_consistent"] is True
    assert section["cross_node_consistent"] is True
    assert section["freeze_window_stable"] is True


def test_stake_snapshot_mutating_inside_an_epoch_is_not_stable(devnet):
    devnet.t = SYSTEM_START + 90 * SLOT_LENGTH
    original = devnet.stake_snapshot
    devnet.stake_snapshot = lambda epoch: {**original(epoch), "drift": devnet.slot()}
    section = _section("simulate_stake_snapshot_update", _metadata())
    assert len(set(section["snapshot_hashes"])) > 1
    assert section["freeze_window_stable"] is False


def test_reward_boundary_invariant_holds_when_ada_is_conserved(devnet):
    section = _section("trigger_rupd_pulse", _metadata())
    assert section["measured"] is True
    node1 = section["per_node"]["node1"]
    assert node1["conserved_before"] and node1["conserved_after"]
    assert section["expected_total_rewards"] == section["observed_total_rewards"] == 10
    assert section["boundary_invariant_holds"] is True


def test_reward_boundary_invariant_fails_when_ada_leaks(devnet):
    devnet.leak = 3
    section = _section("trigger_rupd_pulse", _metadata())
    assert section["per_node"]["node1"]["conserved_before"] is False
    assert section["boundary_invariant_holds"] is False


def test_reward_watch_moves_to_next_boundary_without_a_usable_pre_sample(devnet):
    original = devnet.ledger_state
    devnet.ledger_state = lambda epoch: {"broken": True} if epoch == 0 else original(epoch)
    section = _section("trigger_rupd_pulse", _metadata())
    node1 = section["per_node"]["node1"]
    assert node1["measured"] is True and node1["boundary_epoch"] == 2
    assert section["boundary_invariant_holds"] is True


def test_reward_unrecognised_ledger_state_is_not_measured(devnet):
    devnet.ledger_state = lambda epoch: {"something": "else"}
    section = _section("trigger_rupd_pulse", _metadata())
    assert section["measured"] is False
    assert section["boundary_invariant_holds"] is False
    assert section["expected_total_rewards"] is None
    assert "ledger-state shape not recognised" in section["per_node"]["node1"]["reason"]


def test_leadership_schedule_recomputed_twice_and_on_every_node(devnet):
    section = _section("recompute_leadership_schedule", _metadata())
    assert section["measured"] is True
    assert section["expected_schedule_hash"] == section["recomputed_schedule_hash"]
    assert section["deterministic_match"] is True
    body = section["per_node"]["node1"]
    assert body["pool_id"] == "pool1-id"
    assert set(body["cross_node_schedule_hashes"]) == {"node2"}
    vrf_paths = [c[c.index("--vrf-signing-key-file") + 1] for c in devnet.calls if "--vrf-signing-key-file" in c]
    assert vrf_paths and set(vrf_paths) == {"/env/pools-keys/pool1/vrf.skey"}


def test_empty_leader_schedule_is_not_a_deterministic_match(devnet):
    devnet.schedule_by_node = {"node1": [], "node2": []}
    section = _section("recompute_leadership_schedule", _metadata())
    assert section["measured"] is False and section["deterministic_match"] is False
    assert "empty leader schedule" in section["per_node"]["node1"]["reason"]


def test_leadership_schedule_disagreement_between_nodes_fails(devnet):
    devnet.schedule_by_node["node2"] = [3, 17]
    section = _section("recompute_leadership_schedule", _metadata())
    assert section["deterministic_match"] is False


def test_leadership_query_failure_reports_reason(devnet):
    devnet.missing.add("leadership-schedule")
    section = _section("recompute_leadership_schedule", _metadata())
    assert section["measured"] is False and section["deterministic_match"] is False
    assert "leadership-schedule" in section["per_node"]["node1"]["reason"]


def _amaru_line(ts, slot, epoch):
    return json.dumps({"timestamp": ts, "fields": {"message": "tip.update", "slot": slot, "epoch": epoch,
                                                   "slot_in_epoch": slot % EPOCH_LENGTH, "header_hash": f"a{slot}",
                                                   "block_height": slot}})


def _iso(t):
    return probe._utc(t)


def test_amaru_epoch_boundary_measured_from_tip_update_trace(devnet, tmp_path):
    log = tmp_path / "amaru.log"
    t0 = devnet.t
    log.write_text("\n".join([
        _amaru_line(_iso(t0 + 0.2), 97, 0),
        _amaru_line(_iso(t0 + 0.7), 102, 1),
    ]) + "\n")
    section = _section("force_epoch_boundary", _metadata(amaru=[("amaru1", log)]))
    body = section["per_node"]["amaru1"]
    assert body["measured"] is True
    assert body["observed_boundary_slot"] == 102
    assert body["expected_window_start"] == 100
    assert body["amaru_reported_epoch_start"] == 100
    assert body["within_bounds"] is True


def test_amaru_reward_is_reported_unmeasured_with_reason(devnet, tmp_path):
    log = tmp_path / "amaru.log"
    log.write_text("pots.dump treasury=1 reserves=2 fees=3 donations=0\n")
    section = _section("trigger_rupd_pulse", _metadata(amaru=[("amaru1", log)]))
    body = section["per_node"]["amaru1"]
    assert body["measured"] is False and body["boundary_invariant_holds"] is None
    assert body["pots_samples"] == [{"treasury": 1, "reserves": 2, "fees": 3, "donations": 0}]
    assert "UTxO total" in body["reason"]
    assert section["boundary_invariant_holds"] is False


def test_amaru_leadership_checks_adopted_slots_against_recomputed_schedules(devnet, tmp_path):
    log = tmp_path / "amaru.log"
    log.write_text("\n".join([_amaru_line(_iso(devnet.t), 17, 0), _amaru_line(_iso(devnet.t), 42, 0)]) + "\n")
    devnet.t = SYSTEM_START + 50 * SLOT_LENGTH
    section = _section("recompute_leadership_schedule", _metadata(amaru=[("amaru1", log)]))
    body = section["per_node"]["amaru1"]
    assert body["measured"] is True and body["adopted_outside_schedule"] == []
    assert body["deterministic_match"] is True
    log.write_text(_amaru_line(_iso(devnet.t), 18, 0) + "\n")
    section = _section("recompute_leadership_schedule", _metadata(amaru=[("amaru1", log)]))
    assert section["per_node"]["amaru1"]["adopted_outside_schedule"] == [18]
    assert section["deterministic_match"] is False


def test_probe_never_writes_observation_overrides(devnet, tmp_path):
    runtime = tmp_path / "runtime.json"
    metadata = _metadata()
    metadata["observation_overrides"] = {"keep": 1}
    runtime.write_text(json.dumps(metadata))
    report = probe.run_epoch_boundary_probe(runtime_metadata_path=runtime, output_dir=tmp_path / "out",
                                            mode="force_epoch_boundary",
                                            config={"target_node": "node1", "sample_interval_seconds": 0.3})
    assert json.loads(runtime.read_text())["observation_overrides"] == {"keep": 1}
    written = json.loads((tmp_path / "out" / "result.json").read_text())
    assert written["result"]["epoch_boundary"]["observed_boundary_slot"] == report["result"]["epoch_boundary"]["observed_boundary_slot"]


def test_stake_snapshot_window_moves_to_next_epoch_when_started_at_the_boundary(devnet):
    devnet.t = SYSTEM_START + 99.5 * SLOT_LENGTH  # one pre-boundary sample only
    section = _section("simulate_stake_snapshot_update", _metadata(), sample_interval_seconds=0.5)
    node1 = section["per_node"]["node1"]
    assert node1["freeze_window_epoch"] == 1
    assert len(section["snapshot_hashes"]) >= 2 and len(set(section["snapshot_hashes"])) == 1
    assert section["rotation_consistent"] is True
    assert section["freeze_window_stable"] is True


def test_an_account_with_balance_and_reward_counts_once():
    """cardano-node 10.x/11.x dstate accounts carry the same lovelace as both balance and reward."""
    from scripts.runtime_epoch_boundary_probe import extract_pots

    state = {"stateBefore": {
        "esAccountState": {"reserves": 10, "treasury": 5},
        "esLState": {
            "utxoState": {"deposited": 4000000, "fees": 0, "utxo": {}},
            "delegationState": {"dstate": {"accounts": {
                "keyHash-0e": {"balance": 1065935125629, "deposit": 2000000, "drep": None, "reward": 1065935125629},
                "keyHash-67": {"balance": 1223510289317, "deposit": 2000000, "drep": None, "reward": 1223510289317},
            }, "fGenDelegs": [], "genDelegs": {}, "irwd": {"deltaReserves": 0, "deltaTreasury": 0}}},
        },
    }}
    pots, error = extract_pots(state)
    assert error is None
    assert pots["rewards"] == 1065935125629 + 1223510289317
    assert pots["reward_account_fields"] == 2


def _genesis_tenth_second_slots():
    return {"epoch_length": 500, "slot_length": 0.1, "system_start_unix": 1000.0, "shelley_hard_fork_at_epoch": 0}


def _tip(slot, t, epoch):
    return {"ok": True, "t": t, "slot": slot, "epoch": epoch, "slot_in_epoch": slot % 500, "hash": "h"}


def test_a_first_block_within_the_consensus_clock_skew_is_within_bounds():
    """Live run 20261109T192710Z: slot 532 seen 0.2 s before its slot started by the controller clock.

    ouroboros-consensus defaultClockSkew = clockSkewInSeconds 2 (InFutureCheck.hs), so a node may hold
    a block up to 2 s (20 slots of 0.1 s) ahead of another clock."""
    genesis = _genesis_tenth_second_slots()
    samples = [_tip(497, 1049.8, 0), _tip(532, 1053.0, 1)]  # wall-clock slot at t=1053.0 is 530
    body = probe._cardano_epoch_boundary({}, samples, 0, genesis)
    assert body["observed_boundary_slot"] == 532
    assert body["expected_window_end"] == 550
    assert body["clock_skew_allowance_slots"] == 20
    assert body["within_bounds"] is True


def test_a_first_block_beyond_the_clock_skew_is_out_of_bounds():
    genesis = _genesis_tenth_second_slots()
    samples = [_tip(497, 1049.8, 0), _tip(551, 1053.0, 1)]
    body = probe._cardano_epoch_boundary({}, samples, 0, genesis)
    assert body["within_bounds"] is False
