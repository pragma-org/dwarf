"""F121: copied-state helpers on container profiles.

The helpers stopped/started the devnet through tmux and two modes demanded that node1 already be
behind node2 at baseline (a host-devnet accident; live fa691ea6: "stale source node1 is not behind
node2: 3119 >= 3119"). Container profiles now stop/start the node containers, and every mode
creates the stale snapshot itself, as the recovery mode already did. The db folders are bind
mounts the controller reads directly, so the copy itself is unchanged.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import runtime_common as rc
from scripts import runtime_profile_recovery_check as subject


def _config(tmp_path):
    (tmp_path / "runtime.json").write_text(json.dumps({"nodes": [
        {"id": f"node{i}", "name": f"node{i}", "container_name": f"dwarf-p-node{i}-1"} for i in (1, 2, 3)]}))
    return rc.RuntimeConfig(**{**rc.PROFILE_A_CONFIG.__dict__, "runtime_root": tmp_path})


def test_container_profile_stop_and_start_use_the_node_containers(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(rc.subprocess, "run", lambda argv, **k: calls.append(list(argv)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    stopped = rc.stop_session_with_details(_config(tmp_path))
    started = rc.start_session_with_details(_config(tmp_path))
    assert calls[0][:2] == ["docker", "stop"] and calls[0][-3:] == ["dwarf-p-node1-1", "dwarf-p-node2-1", "dwarf-p-node3-1"]
    assert calls[1] == ["docker", "start", "dwarf-p-node1-1", "dwarf-p-node2-1", "dwarf-p-node3-1"]
    assert stopped["mechanism"] == "container" and started["started"] is True
    assert not any(c[0] == "tmux" for c in calls)


class _Stop(Exception):
    pass


@pytest.mark.parametrize("mode", ["run_copied_state_recovery_fetch", "run_copied_state_bounded_divergence",
                                  "run_copied_state_chainsync_divergence"])
def test_every_copied_state_mode_creates_its_stale_snapshot(monkeypatch, mode):
    tips = {name: {"slot": 3119, "block": 100, "hash": "aa"} for name in subject.NODE_NAMES}
    monkeypatch.setattr(subject, "wait_for_all_tips_with_details", lambda *a, **k: (tips, {"timed_out": False}))
    monkeypatch.setattr(subject, "derive_range", lambda config: ("1.aa", "2.bb"))
    seen = {}

    def prepare(**kwargs):
        seen.update(kwargs)
        raise _Stop()

    monkeypatch.setattr(subject, "_prepare_stale_snapshot_for_recovery", prepare)
    with pytest.raises(_Stop):
        getattr(subject, mode)()
    assert seen["baseline"] is tips
