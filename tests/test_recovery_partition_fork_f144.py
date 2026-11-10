"""F144: force_rollback / chain_switch_inject drive a real fork by partitioning a node, then measure.

Live 397146f4: both were "not_applicable substrate-walled" no-ops written for an old Amaru
substrate, so k_bound_rollback_recovered saw rollback_status "" and chain_switch_consistent an
empty target tip. On a cardano-node devnet the fork is real: the partitioned node builds its own
branch, the majority a longer one, and after the heal the partitioned node rolls back.
"""
from __future__ import annotations

from scripts import runtime_recovery_fault as rf

NODES = [{"id": f"node{i}", "impl": "cardano-node", "container_name": f"c-node{i}"} for i in (1, 2, 3)]


class FakeSubstrate:
    """Three nodes on one chain; a partitioned node forges its own branch; heal -> the longer wins."""

    def __init__(self, *, isolated_forges=True, majority_forges=True, switch_logged=True, utxo_equal=True,
                 wire=True):
        self.t = 0.0
        self.chain = [("g", 0)]                  # majority chain: (hash, slot)
        self.fork = None                         # isolated node's own blocks
        self.isolated = None
        self.healed_at = None
        self.isolated_forges = isolated_forges
        self.majority_forges = majority_forges
        self.switch_logged = switch_logged
        self.utxo_equal = utxo_equal
        self.wire = wire
        self.events = []

    # ops interface ---------------------------------------------------------
    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.majority_forges and int(self.t) % 2 == 0:
            self.chain.append((f"m{len(self.chain)}", int(self.t * 10)))
        if self.isolated and self.fork is not None and self.isolated_forges and int(self.t) % 5 == 0:
            self.fork.append((f"f{len(self.fork)}", int(self.t * 10)))
        if self.healed_at is not None and self.fork is not None and self.t - self.healed_at >= 2:
            self.fork = None                     # the isolated node adopted the longer chain

    def _blocks(self, node):
        if node["id"] == self.isolated and self.fork is not None:
            return self.base + self.fork
        return self.chain

    def on_chain(self, node, tip):
        if not self.wire:
            return None
        return any(h == tip["hash"] for h, _slot in self._blocks(node))

    def tip(self, node):
        blocks = self._blocks(node)
        h, slot = blocks[-1]
        return {"ok": True, "hash": h, "slot": slot, "block": len(blocks) - 1}

    def disconnect(self, node):
        self.isolated = node["id"]
        self.base = list(self.chain)
        self.fork = []
        self.events.append(("disconnect", node["id"]))
        return {"ok": True, "network": "net", "aliases": [node["id"]], "ip": "10.0.0.3"}

    def connect(self, node, partition):
        self.healed_at = self.t
        self.events.append(("connect", node["id"], tuple(partition["aliases"])))
        return {"ok": True}

    def log_lines_since(self, node, since):
        if node["id"] == self.isolated and self.switch_logged:
            return ['{"ns":"ChainDB.AddBlockEvent.SwitchedToAFork","data":{"newtip":"x@1"}}']
        return ['{"ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":{"newtip":"y@2"}}']

    def ledger_digest(self, node):
        return {"ok": True, "digest": "same" if (self.utxo_equal or node["id"] != self.isolated) else "other",
                "utxo_count": 7}


def _run(fake, mode="force_rollback", k=10, **config):
    cfg = {"target_node": "node2", "partition_max_seconds": 60, "converge_timeout_seconds": 30,
           "requested_rollback_slots": 5, "security_parameter_k": 10, **config}
    return rf.partition_fork_and_heal(nodes=NODES, target_id=cfg["target_node"], config=cfg, ops=fake,
                                      security_parameter_k=k, mode=mode)


def test_a_partitioned_node_rolls_back_its_own_branch_within_k():
    fake = FakeSubstrate()
    out = _run(fake)
    assert out["fork_formed"] is True
    assert out["observed_rollback_depth_blocks"] >= 1
    assert out["observed_rollback_depth_blocks"] <= 10
    assert out["rollback_status"] == "recovered"
    assert out["switch_logged_by_target"] is True
    assert out["ledger_state_consistent_post_rollback"] is True
    assert out["converged"] is True
    assert ("connect", "node2", ("node2",)) in fake.events      # aliases restored on heal


def test_no_own_block_while_partitioned_is_no_rollback():
    out = _run(FakeSubstrate(isolated_forges=False), partition_max_seconds=20)
    assert out["fork_formed"] is False
    assert out["rollback_status"] == "no_fork_formed"
    assert out["observed_rollback_depth_blocks"] in (0, None)


def test_the_rollback_is_proved_on_the_wire_or_by_the_node_log():
    out = _run(FakeSubstrate(switch_logged=False))
    assert out["abandoned_fork_tip_on_target_chain"] is False and out["rollback_status"] == "recovered"
    out = _run(FakeSubstrate(wire=False))
    assert out["abandoned_fork_tip_on_target_chain"] is None and out["rollback_status"] == "recovered"
    out = _run(FakeSubstrate(switch_logged=False, wire=False))
    assert out["rollback_status"] == "unconfirmed"


def test_ledger_mismatch_after_rollback_is_reported():
    out = _run(FakeSubstrate(utxo_equal=False))
    assert out["ledger_state_consistent_post_rollback"] is False


def test_chain_switch_records_the_majority_tip_as_target_and_who_reached_it():
    fake = FakeSubstrate()
    out = _run(fake, mode="chain_switch_inject", target_node="node3")
    assert out["target_tip_hash"] and out["target_tip_slot"] is not None
    assert sorted(out["nodes_on_target_chain"]) == ["node1", "node2", "node3"]
    assert out["chain_switch_status"] == "switched"


def test_genesis_k_is_read_from_the_node_config(tmp_path):
    (tmp_path / "shelley-genesis.json").write_text('{"securityParam": 7, "activeSlotsCoeff": 0.1}')
    (tmp_path / "configuration.yaml").write_text('ShelleyGenesisFile: shelley-genesis.json\n')
    assert rf.genesis_security_parameter({"config_path": str(tmp_path / "configuration.yaml")}) == 7
    (tmp_path / "configuration.yaml").write_text('{"ShelleyGenesisFile": "shelley-genesis.json"}')
    assert rf.genesis_security_parameter({"config_path": str(tmp_path / "configuration.yaml")}) == 7
    assert rf.genesis_security_parameter({}) is None


def test_switch_lines_are_found_in_node_logs():
    lines = ['x', '{"ns":"ChainDB.AddBlockEvent.SwitchedToAFork","data":{}}']
    assert rf.count_fork_switches(lines) == 1


def test_modes_are_no_longer_not_applicable():
    import inspect
    source = inspect.getsource(rf.apply_recovery_mode)
    assert "substrate-walled" not in source


class MinorityWinsFirst(FakeSubstrate):
    """Taps campaign 20261110T045824Z: node3 alone forged 4 blocks while the majority forged 1, so the
    partitioned branch hit the depth cap first and won on heal: no fork in the measured direction. The
    leader schedule is random; a second partition forms the fork the scenario needs."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.partitions = 0

    def disconnect(self, node):
        self.partitions += 1
        return super().disconnect(node)

    def sleep(self, seconds):
        if self.partitions == 1 and self.healed_at is None and self.fork is not None:
            self.t += seconds
            self.fork.append((f"w{len(self.fork)}", int(self.t * 10)))   # the minority outruns the majority
            return
        super().sleep(seconds)

    def connect(self, node, partition):
        if self.partitions == 1 and self.fork and len(self.fork) > len(self.chain) - len(self.base):
            self.chain = self.base + self.fork                             # the longer minority branch wins
            self.fork = None
            self.events.append(("connect", node["id"], tuple(partition["aliases"])))
            return {"ok": True}
        return super().connect(node, partition)


def test_a_minority_branch_that_wins_is_healed_and_the_partition_retried():
    fake = MinorityWinsFirst()
    out = _run(fake, mode="chain_switch_inject", target_node="node3", k=5)
    assert fake.partitions == 2
    assert [a["outcome"] for a in out["partition_attempts"]] == ["isolated_branch_reached_depth_cap", "fork_formed"]
    assert out["fork_formed"] is True
    assert out["chain_switch_status"] == "switched"


def test_partition_attempts_are_bounded():
    out = _run(FakeSubstrate(isolated_forges=False), partition_max_seconds=5, partition_attempts=2)
    assert len(out["partition_attempts"]) == 2
    assert out["rollback_status"] == "no_fork_formed"


def test_the_partition_heals_before_the_majority_nears_k():
    """Run 20261110T101845Z: k=5, the heal came when the majority was 4 blocks past the fork, the
    majority forged 4 more before the peers reconnected, and the 8-deep fork could never resolve.
    The partition must heal while the majority still leaves room for the reconnect."""
    out = _run(FakeSubstrate(isolated_forges=False), k=5, partition_max_seconds=60, partition_attempts=2)
    assert [a["outcome"] for a in out["partition_attempts"]] == ["majority_reached_depth_cap"] * 2
    assert all(a["majority_blocks_since_fork"] <= 3 for a in out["partition_attempts"])
    assert out["rollback_status"] == "no_fork_formed"


class NeverHeals(FakeSubstrate):
    def connect(self, node, partition):
        self.events.append(("connect", node["id"], tuple(partition["aliases"])))
        return {"ok": True}                       # the peers never take the isolated node back


def test_a_fork_left_deeper_than_k_after_heal_is_named():
    out = _run(NeverHeals(), k=5)
    assert out["fork_formed"] is True and out["converged"] is False
    assert out["rollback_status"] == "fork_deeper_than_k_after_heal"
    assert out["post_heal_majority_blocks_since_fork"] > 5
