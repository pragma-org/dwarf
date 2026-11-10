"""F160: the Amaru target tip is the latest adopted tip as of the read, not only one adopted since
the probe began.

Taps campaign 20261110T041921Z: the txsubmission window probe ran 0.4 s and Amaru adopts a block
every few seconds, so ``docker logs --since <probe start>`` held no tip.adopt; tip_before and the
post-load tip were null and RuntimeTargetHealthAndProgress raised "no comparable chain positions".
"""
from __future__ import annotations

import subprocess

from profile_manager import primitives

ADOPT = ('{"timestamp":"2026-11-10T04:19:43.506969Z","level":"INFO","fields":{"block_height":337,'
         '"header_hash":"' + "9b" * 32 + '","message":"tip.adopt","slot":1743},"target":"amaru::consensus"}')
FATAL_OLD = '{"level":"ERROR","fields":{"message":"panicked at old failure"}}'


def test_amaru_tip_comes_from_a_lookback_window_and_signals_from_the_probe_window(monkeypatch):
    calls = []

    def fake_docker(command, *, timeout=60.0):
        calls.append(command)
        since = command[command.index("--since") + 1]
        text = (ADOPT + "\n" + FATAL_OLD) if since != "2026-11-10T04:19:43Z" else ""
        return subprocess.CompletedProcess(command, 0, stdout=text, stderr="")

    monkeypatch.setattr(primitives, "_protocol_docker_result", fake_docker)
    monkeypatch.setattr(primitives, "_protocol_container_state", lambda c: {"running": True})
    out = primitives._observe_protocol_target(
        {"implementation": "amaru", "container": "relay", "started_at": "2026-11-10T04:19:43Z"})
    assert out["tip"]["slot"] == 1743 and out["tip"]["block_height"] == 337
    sinces = sorted(c[c.index("--since") + 1] for c in calls)
    assert sinces == ["2026-11-10T04:14:43Z", "2026-11-10T04:19:43Z"]
    assert not out["log_signals"].get("fatal")      # an old failure before the probe is not this run's


def test_amaru_tip_is_none_when_nothing_was_adopted_in_the_lookback(monkeypatch):
    monkeypatch.setattr(primitives, "_protocol_docker_result",
                        lambda command, *, timeout=60.0: subprocess.CompletedProcess(command, 0, stdout="", stderr=""))
    monkeypatch.setattr(primitives, "_protocol_container_state", lambda c: {"running": True})
    out = primitives._observe_protocol_target(
        {"implementation": "amaru", "container": "relay", "started_at": "2026-11-10T04:19:43Z"})
    assert out["tip"] is None
