"""Randomized differential opcert-header SOAK driver.

A soak runs one *family* for a wall-clock time budget (default 3h). Each
iteration it generates a fresh seed-deterministic ``SoakCaseSpec``
(``opcert_soak_families.generate_case``), serves it through the existing opcert
forger (``serve-case --case-spec``) against an isolated consumer of each target,
reads the verdict(s) through the existing parser, evaluates the per-family
invariant, appends the iteration to ``attempts.ndjson`` and updates counters.
``result.json`` carries the counters and, for every mismatch/disagreement, the
full seed-derived spec so a finding is replayable.

This is an EXTENSION of ``runtime_opcert_header_cases`` — it imports and reuses
that module's substrate detection, forger invocation and log parser, plus the
fail-closed accept/reject/reason semantics (via ``opcert_soak_result``). It does
not reinvent any of them.

Fail-closed everywhere: no leader slot / undelivered header within the
per-iteration timeout ⇒ ``inconclusive`` (tracked separately, never ``pass``,
never ``agree``/``disagree``); a differential iteration with one side
inconclusive is inconclusive on both sides; zero conclusive iterations ⇒ the
whole run FAILS. The loop is bounded by the injected monotonic ``clock``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from scripts import opcert_soak_families as families
from scripts import opcert_soak_result as R
from scripts import runtime_opcert_header_cases as det

SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent

_OUTCOME_FOR_DIFF = {"agree": "pass", "disagree": "mismatch", "inconclusive": "inconclusive"}

# Per-node listen/consumer port bases; each node gets a distinct pair so the two
# sides of a differential iteration never collide.
_PORT_BASE = {"listen": 34081, "consumer": 34082}


# --------------------------------------------------------------------------- #
# Substrate seams (patched out in unit tests; real bodies reuse the
# deterministic driver's proven helpers for a live run).
# --------------------------------------------------------------------------- #

def _load_runtime_root(runtime_root):
    """Return ``(runtime_dict, default_implementation)`` for ``runtime_root``."""
    runtime, _path = det._load_runtime(runtime_root)
    nodes = runtime.get("haskell_nodes") or []
    implementation = (nodes[0].get("impl") if nodes else None) or "cardano-node"
    return runtime, implementation


def _node_impl(runtime, node_id):
    for node in (runtime or {}).get("haskell_nodes") or []:
        if node.get("id") == node_id:
            return node.get("impl")
    return None


def _find_node(runtime, node_id):
    for node in (runtime or {}).get("haskell_nodes") or []:
        if node.get("id") == node_id:
            return node
    return None


def _is_amaru_control(runtime):
    return (runtime or {}).get("lifecycle") == "cardano_amaru_relay_bootstrap_control"


def _open_consumers(runtime, nodes, *, peer_bin=None, output_dir=None):
    """Start one isolated fresh-DB consumer per target node, dispatching on the
    runtime shape exactly like the deterministic sibling.

    - ``cardano_amaru_relay_bootstrap_control`` runtimes (profile-zb mixed,
      profile-z amaru) → the amaru-control lifecycle: a cloned amaru consumer for
      the ``amaru-*`` leg and a fresh-DB cardano consumer for the ``node1`` leg,
      both from the shared deterministic-driver helpers (imported, not forked).
    - otherwise (profile-v generated-cardano-local) → the local fresh-DB cardano
      consumer path.

    Returns ``{node: consumer_context}`` with a uniform ctx shape so
    ``_serve_case_spec`` is substrate-agnostic.
    """
    if _is_amaru_control(runtime):
        return _open_amaru_control_consumers(runtime, nodes, output_dir=output_dir)
    return _open_local_cardano_consumers(runtime, nodes, output_dir=output_dir)


def _open_amaru_control_consumers(runtime, nodes, *, output_dir):
    """Open one isolated consumer per target on the amaru-control substrate,
    reusing the deterministic driver's proven consumer lifecycle by import."""
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    consumers = {}
    for index, node_id in enumerate(nodes):
        listen_port = _PORT_BASE["listen"] + index * 2
        consumer_port = _PORT_BASE["consumer"] + index * 2
        suffix = f"-{node_id}"
        if str(node_id).startswith("amaru"):
            ctx = det.open_amaru_consumer(runtime, work, listen_port=listen_port, name_suffix=suffix)
            _wait_amaru_consumer_ready(ctx, deadline=time.time() + 300)
        else:
            ctx = det.open_amaru_control_cardano_consumer(
                runtime, work, listen_port=listen_port, consumer_port=consumer_port,
                name_suffix=suffix,
            )
        consumers[node_id] = ctx
    return consumers


def _wait_amaru_consumer_ready(ctx, *, deadline):
    """Poll the cloned amaru consumer's logs until it has attempted to peer with
    the host forger (fail-open: returns after the deadline either way; the
    per-iteration timeout still fail-closes any never-observed header)."""
    while time.time() < deadline:
        proc = det._docker("logs", "--tail", "200", ctx["name"], check=False)
        blob = proc.stdout + "\n" + proc.stderr
        if "manager.peer.connect" in blob or "tip.adopt" in blob or "connect_failed" in blob:
            return True
        time.sleep(5)
    return False


def _open_local_cardano_consumers(runtime, nodes, *, output_dir):
    """Start one isolated fresh-DB cardano consumer per target node on a
    generated-cardano-local runtime (profile-v)."""
    consumers = {}
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    for index, node_id in enumerate(nodes):
        node = _find_node(runtime, node_id)
        if node is None:
            raise RuntimeError(f"soak target node {node_id!r} not in runtime")
        env_dir = det._host_env_dir(node["container_name"])
        image = det._container_image(node["container_name"])
        listen_port = _PORT_BASE["listen"] + index * 2
        consumer_port = _PORT_BASE["consumer"] + index * 2
        consumer_name = f"opcert-soak-consumer-{node_id}-{runtime.get('compose_project', 'devnet')}"[:100]
        consumer_volume = f"{consumer_name}-db"[:100]
        topo_path = work / f"consumer-topology-{node_id}.json"
        topo_path.write_text(json.dumps(det._consumer_topology("127.0.0.1", listen_port)), encoding="utf-8")
        det._docker("rm", "-f", consumer_name, check=False)
        det._docker("volume", "rm", "-f", consumer_volume, check=False)
        consumer_cmd = (
            "mkdir -p /consumer && exec cardano-node run "
            "--config /env/configuration.yaml "
            f"--topology {topo_path.as_posix()} "
            "--database-path /consumer/db --socket-path /consumer/sock "
            f"--port {consumer_port} --host-addr 0.0.0.0"
        )
        det._docker(
            "run", "-d", "--name", consumer_name, "--network", "host",
            "-v", f"{env_dir}:/env:ro",
            "-v", f"{topo_path.parent}:{topo_path.parent.as_posix()}:ro",
            "-v", f"{consumer_volume}:/consumer",
            "--entrypoint", "bash", image, "-lc", consumer_cmd,
        )
        pool_dir = env_dir / "pools-keys" / "pool1"
        shelley = json.loads((env_dir / "shelley-genesis.json").read_text(encoding="utf-8"))
        listen_addr = node.get("listen_address") or f"127.0.0.1:{node.get('port')}"
        consumers[node_id] = {
            "name": consumer_name, "volume": consumer_volume, "volumes": [consumer_volume],
            "work": work, "listen_port": listen_port,
            "implementation": node.get("impl") or "cardano-node",
            "kes_skey": pool_dir / "kes.skey", "cold_skey": pool_dir / "cold.skey",
            "slots_per_kes": int(shelley["slotsPerKESPeriod"]),
            "max_kes_evo": int(shelley["maxKESEvolutions"]),
            "upstream": listen_addr,
        }
    return consumers


def _close_consumers(consumers):
    for ctx in (consumers or {}).values():
        det._docker("rm", "-f", ctx["name"], check=False)
        volumes = ctx.get("volumes") or ([ctx["volume"]] if ctx.get("volume") else [])
        for vol in volumes:
            det._docker("volume", "rm", "-f", vol, check=False)


def _serve_case_spec(spec, ctx, *, peer_bin, per_iteration_timeout, output_dir, iteration,
                     adopt_hash=None, adopt_impl=None, label=None):
    """Serve one generated spec through the forger and read the verdict.

    Writes ``case-spec-<n>.json``, runs ``serve-case --case-spec`` against the
    node's consumer, and returns ``(served_hash | None, observed | None)``.
    Fail-closed: no served hash or no observed verdict within the timeout ⇒
    ``(served_hash_or_None, None)``.

    Adopt gate (family C): when ``adopt_hash`` is given it is the header hash of
    the just-forged counter-N producer block. The loop then also watches the
    isolated consumer for adoption of that block (its hash observed ``accepted``
    in the consumer's own log — the same by-hash confirmation the deterministic
    driver uses). With the gate on, the serve does not conclude on an *accepted*
    replay until the consumer has actually adopted counter-N (so a stale-view
    accept never terminates early); a *rejected* replay is conclusive
    immediately. The return is a 3-tuple ``(served_hash, observed, adopted)``.
    Gate off (``adopt_hash is None``) keeps the 2-tuple contract every other
    caller relies on.
    """
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    tag = f"-{label}" if label else ""
    spec_path = work / f"case-spec-{spec['family']}-{iteration:06d}{tag}.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    evidence = work / f"evidence-{spec['family']}-{iteration:06d}{tag}.ndjson"
    evidence.write_text("", encoding="utf-8")
    peer_path = Path(peer_bin or det.DEFAULT_PEER_BIN)
    up_host, up_port = ctx["upstream"].split(":")
    since = det._now_docker_ts()
    peer_cmd = [
        str(peer_path), "serve-case",
        "--case", spec["base_case"],
        "--case-spec", str(spec_path),
        "--upstream", f"{up_host}:{up_port}",
        "--listen-port", str(ctx["listen_port"]),
        "--kes-skey", str(ctx["kes_skey"]),
        "--cold-skey", str(ctx["cold_skey"]),
        "--slots-per-kes", str(ctx["slots_per_kes"]),
        "--max-kes-evo", str(ctx["max_kes_evo"]),
        "--evidence", str(evidence),
    ]
    proc = subprocess.Popen(peer_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    served_hash = None
    observed = None
    adopted = False
    adopt_key = str(adopt_hash).lower() if adopt_hash else None
    deadline = time.time() + per_iteration_timeout
    try:
        while time.time() < deadline:
            time.sleep(3)
            if evidence.is_file():
                served = det.served_hash_by_case(evidence.read_text(encoding="utf-8").splitlines())
                if spec["base_case"] in served:
                    served_hash = served[spec["base_case"]]
            if served_hash is not None or adopt_key is not None:
                events = det._read_consumer_events(ctx["name"], since, ctx["implementation"])
                vmap = det.verdict_by_hash(events)
                if served_hash is not None and served_hash in vmap:
                    observed = vmap[served_hash]
                if adopt_key is not None and not adopted:
                    # Confirm the isolated consumer actually adopted the rotated
                    # counter-N block (its hash observed accepted in the
                    # consumer's own AddedToCurrentChain / tip.adopt trace).
                    ev = vmap.get(adopt_key) or vmap.get(str(adopt_hash))
                    if ev is not None and ev.get("verdict") == "accepted":
                        adopted = True
                if observed is not None:
                    if adopt_key is None:
                        break
                    # Gate on: an accepted replay is only conclusive once the
                    # consumer has adopted counter-N; a rejected replay is
                    # conclusive immediately (a stale consumer would accept, not
                    # reject, so a reject already implies it knows counter-N).
                    if adopted or observed.get("verdict") != "accepted":
                        break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    if adopt_hash is not None:
        return served_hash, observed, adopted
    return served_hash, observed


def _serve_one(spec, *, consumer=None, consumers=None, node=None, peer_bin=None,
               per_iteration_timeout=240, output_dir=None, iteration=0, **_kw):
    """Single-target serve. For ``restart-persistence`` the rotation+restart is
    performed by the operator's pre-rotated profile; here we serve the replay
    counter and read the verdict."""
    ctx = consumer if isinstance(consumer, dict) else (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration)


def _serve_one_node(spec, *, node, consumers=None, peer_bin=None,
                    per_iteration_timeout=240, output_dir=None, iteration=0, **_kw):
    """Differential serve to one of the two compared nodes."""
    ctx = (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration)


def _serve_one_gated(spec, *, consumer=None, consumers=None, node=None, adopt_hash=None,
                     adopt_impl=None, peer_bin=None, per_iteration_timeout=240,
                     output_dir=None, iteration=0, **_kw):
    """Family-C serve with the consumer-adopt gate armed. Returns the 3-tuple
    ``(served_hash, observed, adopted)`` (see ``_serve_case_spec``). This is the
    seam family-C substrate tests patch."""
    ctx = consumer if isinstance(consumer, dict) else (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration,
                            adopt_hash=adopt_hash, adopt_impl=adopt_impl)


def _serve_probe(spec, *, consumer=None, consumers=None, node=None, peer_bin=None,
                 per_iteration_timeout=60, output_dir=None, iteration=0, probe_index=0, **_kw):
    """Serve ONE throwaway positive-control probe header and read its verdict.

    The probe reuses the family-C ``counter-behind`` case, so it carries the
    same replay counter the real replay carries: ``recorded-1`` = ``N-1`` (one
    below the just-rotated counter ``N``, since the producer's chain records the
    forged counter-N block). No adopt gate is involved; the probe's verdict IS
    the readiness signal (see ``_amaru_probe_gate``). Distinct on-disk artifacts
    (``label='probe-<k>'``) keep the real replay's evidence pristine and
    replayable. Returns the 2-tuple ``(served_hash | None, observed | None)``.
    This is the seam the amaru probe-gate tests patch."""
    ctx = consumer if isinstance(consumer, dict) else (consumers or {}).get(node)
    return _serve_case_spec(spec, ctx, peer_bin=peer_bin,
                            per_iteration_timeout=per_iteration_timeout,
                            output_dir=output_dir, iteration=iteration,
                            label=f"probe-{probe_index}")


# Probe-gate pacing (overridable in tests). ``_PROBE_POLL_INTERVAL`` is the wait
# between successive probe re-serves; ``_PROBE_PER_SERVE_TIMEOUT`` bounds a single
# probe serve so many polls fit inside the per-iteration budget.
_PROBE_POLL_INTERVAL = 3.0
_PROBE_PER_SERVE_TIMEOUT = 60


def _amaru_probe_gate(spec, consumer_ctx, node, implementation, *, peer_bin,
                      per_iteration_timeout, output_dir, iteration,
                      clock=time.monotonic, sleep=time.sleep):
    """Log-independent readiness gate for the amaru family-C path.

    amaru's ``tip.adopt`` fires BEFORE it commits the rotated opcert sequence
    number into its ledger state, and it emits no distinct opcert-commit log
    signal, so the block-adopt gate is insufficient: a replay served right after
    adopt is judged against stale (pre-N) state and is falsely ACCEPTED. This
    gate instead uses amaru's OWN verdict as the readiness signal, via opcert
    counter monotonicity:

    - probe (counter ``N-1``) ACCEPTED  ⇒ recorded counter still ``N-1``
      (``N-1 == recorded`` accepts) ⇒ commit not yet applied ⇒ NOT ready ⇒ wait
      and re-serve the probe;
    - probe (counter ``N-1``) REJECTED  ⇒ recorded counter advanced to ``N``
      (``N-1 < recorded`` rejects as too-small) ⇒ state committed ⇒ READY.

    Bounded by ``per_iteration_timeout``. If the probe never rejects within the
    budget the gate reports NOT ready and the caller scores the iteration
    fail-closed inconclusive (never accept, never a finding). Returns
    ``(ready: bool, polls: int)``."""
    deadline = clock() + per_iteration_timeout
    per_serve = min(per_iteration_timeout, _PROBE_PER_SERVE_TIMEOUT)
    polls = 0
    while clock() < deadline:
        polls += 1
        served_hash, observed = _serve_probe(
            spec, consumer=consumer_ctx, node=node, peer_bin=peer_bin,
            per_iteration_timeout=per_serve, output_dir=output_dir,
            iteration=iteration, probe_index=polls)
        if (served_hash is not None and observed is not None
                and R.conclusive_verdict(observed) == "rejected"):
            # Recorded counter has advanced to N (probe N-1 rejected too-small):
            # the opcert commit is applied. Ready.
            return True, polls
        # Probe accepted (recorded still N-1) or not observed: not ready yet.
        if clock() < deadline:
            sleep(_PROBE_POLL_INTERVAL)
    return False, polls


# --------------------------------------------------------------------------- #
# Persistent differential serving.
#
# The differential (encoding-form / kes-period) families are served by ONE
# long-lived forger per compared node instead of a fresh reconnect-and-wait
# forger per iteration. A per-iteration forger must, on every iteration,
# re-follow the chain from genesis and wait for its pool to be elected leader
# *again* before it can inject at the live tip; the isolated amaru consumer,
# bootstrapped once to a high snapshot tip, sees each freshly-reconnected forger
# lag its tip and pauses (``blocks.paused``), so the case almost never reaches it
# in the window ("started, never served"). A persistent forger stays caught up
# and injects a FRESH seed-derived case at every one of its pool's live leader
# slots, converting throughput to the pool's natural leader rate (many
# conclusive iterations per connection). The driver pre-generates the same
# seed-deterministic spec stream both forgers consume (``spec-<n>.json``) and
# correlates the two sides by served index — the encoding deviation is a pure
# function of the spec, so index (= same spec) is the correct differential key.
#
# Fail-closed is preserved: an index one side never serves, or serves but whose
# verdict is never observed within the window, is inconclusive on both sides
# (never agree); zero conclusive over the whole budget ⇒ the run FAILS.
# --------------------------------------------------------------------------- #

_SPEC_LOOKAHEAD = 512


def _ensure_specs(family, seed, spec_dir, upto, *, restart_k=4, _state={}):
    """Materialise ``spec-<n>.json`` for every ``n`` in ``0..upto`` (inclusive)
    that is not present yet, each ``families.generate_case(family, seed, n)`` —
    the single seed-deterministic source of truth (same stream the driver's
    attempt records use), so a persistent-mode finding is replayable from
    ``(family, seed, index)``."""
    spec_dir = Path(spec_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)
    key = (str(spec_dir), family, seed)
    have = _state.get(key, -1)
    for n in range(have + 1, upto + 1):
        spec = families.generate_case(family, seed, n, restart_k=restart_k)
        (spec_dir / f"spec-{n:06d}.json").write_text(json.dumps(spec), encoding="utf-8")
    _state[key] = max(have, upto)


def _launch_persistent_forger(spec_dir, ctx, *, peer_bin, output_dir, node):
    """Start ONE long-lived ``serve-case --case-spec-dir`` forger for ``node``,
    streaming the shared spec dir to that node's isolated consumer. Returns a
    handle carrying the process, its evidence path and the docker ``since``
    stamp the consumer verdicts are read from."""
    work = Path(output_dir) / "harness"
    work.mkdir(parents=True, exist_ok=True)
    evidence = work / f"evidence-persistent-{node}.ndjson"
    evidence.write_text("", encoding="utf-8")
    peer_path = Path(peer_bin or det.DEFAULT_PEER_BIN)
    up_host, up_port = ctx["upstream"].split(":")
    since = det._now_docker_ts()
    peer_cmd = [
        str(peer_path), "serve-case",
        "--case", "valid-control",
        "--case-spec-dir", str(spec_dir),
        "--upstream", f"{up_host}:{up_port}",
        "--listen-port", str(ctx["listen_port"]),
        "--kes-skey", str(ctx["kes_skey"]),
        "--cold-skey", str(ctx["cold_skey"]),
        "--slots-per-kes", str(ctx["slots_per_kes"]),
        "--max-kes-evo", str(ctx["max_kes_evo"]),
        "--evidence", str(evidence),
    ]
    # The forger is long-lived and chatty (a header line per RollForward during
    # the consumer's catch-up). Its stdout MUST go to a file, never an unread
    # PIPE: an unread OS pipe fills at ~64 KiB and blocks the forger's next
    # write, wedging it so it stops serving. Redirect to a per-node log.
    log_path = work / f"forger-{node}.log"
    log_fh = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 (closed in _stop_forger)
    proc = subprocess.Popen(peer_cmd, stdout=log_fh, stderr=subprocess.STDOUT, text=True)
    return {"proc": proc, "evidence": evidence, "since": since, "ctx": ctx,
            "node": node, "log": log_path, "log_fh": log_fh}


def _stop_forger(handle):
    if not handle:
        return
    proc = handle.get("proc")
    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    fh = handle.get("log_fh")
    if fh is not None:
        try:
            fh.close()
        except OSError:
            pass


def _served_records_by_index(handle):
    """Map ``served_index -> opcert_case_served record`` from a forger's
    evidence stream (persistent mode tags every served line with its index)."""
    out = {}
    ev = handle.get("evidence")
    try:
        lines = Path(ev).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("kind") == "opcert_case_served" \
                and rec.get("served_index") is not None and rec.get("header_hash"):
            out[int(rec["served_index"])] = rec
    return out


def _ts_ago(seconds):
    """A docker ``--since`` timestamp ``seconds`` in the past (small overlap so
    an incremental read never drops a line straddling the poll boundary)."""
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")


def _refresh_vmap(handle):
    """Incrementally fold this consumer's NEW verdict events into the handle's
    accumulated ``verdict_by_hash`` map. Reads only the log lines since the last
    refresh (``docker logs --since <last>``), never the whole window — so each
    poll stays cheap for the full multi-hour run even against a debug-verbose
    amaru consumer, and the map still holds every verdict ever observed (so a
    correlation that briefly falls behind the forger never loses old verdicts).

    Reject-sticky (merge_verdicts_sticky): a reject on a served header hash
    is terminal and is NEVER overwritten by a later accept on that same
    hash. This closes the deviant-header masking -- a strict decoder that
    rejects a served deviant then accepts a canonical re-serve of the same
    hash (both lines can land in one lagging poll) stays recorded as
    rejected, so a one-node-reject/other-accept case scores as a
    disagreement, never agree."""
    ctx = handle["ctx"]
    vmap = handle.setdefault("vmap", {})
    since = handle.get("log_since") or handle["since"]
    # Fetch new lines first, THEN advance the cursor (with a 3s overlap) so a
    # line written between fetch and cursor-set is re-read next time, not lost.
    events = det._read_consumer_events(ctx["name"], since, ctx["implementation"])
    handle["log_since"] = _ts_ago(3)
    det.merge_verdicts_sticky(vmap, events)
    return vmap


def _await_indexed_verdict(handle, ix, *, timeout, poll=3.0):
    """Wait until the forger has served index ``ix`` AND that node's isolated
    consumer has emitted a verdict for the served header. Returns
    ``(served_hash | None, observed | None)``. Fail-closed: never served, or
    served but never observed within ``timeout`` seconds, ⇒ ``None`` on the
    missing part. This is the seam the differential unit tests patch."""
    served_hash = None
    observed = None
    end = time.monotonic() + timeout
    while True:
        if served_hash is None:
            rec = _served_records_by_index(handle).get(ix)
            if rec is not None:
                served_hash = str(rec["header_hash"])
        if served_hash is not None:
            vmap = handle.get("vmap")
            if vmap is None or served_hash not in vmap:
                vmap = _refresh_vmap(handle)
            if served_hash in vmap:
                observed = vmap[served_hash]
                break
        if time.monotonic() >= end:
            break
        time.sleep(poll)
    return served_hash, observed


def _consumer_alive(handle):
    """Liveness gate for an isolated consumer during a differential iteration.

    A wedged consumer -- its container exited / OOM-killed, i.e. its verdict
    stream has gone dark -- must not have a (possibly reason-less) reject scored
    as a hard rejection (the iter-243 false divergence). Returns ``False`` ONLY
    when the consumer is *positively* determined dead: the container reports
    not-running or was OOM-killed. An indeterminate probe (no docker / container
    absent -> ``running is None``) returns ``True`` -- the reason-attribution gate
    in ``R.conclusive_verdict`` still fails a reason-less reject closed. Substrate
    seam: patched out in unit tests.
    """
    ctx = handle.get("ctx") if isinstance(handle, dict) else None
    name = (ctx or {}).get("name") if isinstance(ctx, dict) else None
    if not name:
        return True
    try:
        state = det._container_state(name)
    except Exception:
        return True
    if state.get("running") is False:
        return False
    if state.get("oom_killed") is True:
        return False
    return True


def _amaru_control_producer_tip(project, magic):
    proc = det._docker(
        "exec", f"{project}-p1-1", "cardano-cli", "query", "tip",
        "--testnet-magic", str(magic), "--socket-path", "/state/node.socket", check=False,
    )
    try:
        body = json.loads(proc.stdout)
        h = body.get("block")
        if isinstance(h, int) and not isinstance(h, bool):
            return {"block_height": h, "hash": body.get("hash"), "slot": body.get("slot")}
    except (ValueError, TypeError):
        pass
    return {}


def _amaru_relay_tip(project):
    proc = det._docker("logs", "--tail", "4000", f"{project}-amaru-relay-1-1", check=False)
    best = None
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        try:
            doc = json.loads(line.strip())
        except (ValueError, TypeError):
            continue
        f = (doc or {}).get("fields") or {}
        if f.get("message") == "tip.adopt" and f.get("block_height") is not None:
            best = {"block_height": int(f["block_height"]), "slot": f.get("slot")}
    return best or {}


def _node_tip(runtime, node_id):
    if _is_amaru_control(runtime):
        project = runtime.get("compose_project")
        try:
            if str(node_id).startswith("amaru"):
                return _amaru_relay_tip(project)
            return _amaru_control_producer_tip(project, int(runtime.get("network_magic") or 42))
        except Exception:
            return {}
    node = _find_node(runtime, node_id)
    if not node:
        return {}
    try:
        magic = int(runtime.get("network_magic") or 42)
        socket = node.get("container_socket_path") or f"/env/socket/{node_id}/sock"
        return det._cardano_tip(node["container_name"], socket, magic) or {}
    except Exception:
        return {}


# --------------------------------------------------------------------------- #
# Family C (restart-persistence): legitimate opcert rotation + producer restart.
#
# Each iteration issues a fresh cold-key-signed opcert that raises the pool's
# on-chain counter by one (the same issuance mechanism the aged profile used —
# cardano-cli conway node issue-op-cert), installs it on the producer, restarts
# the producer, waits for it to forge a block under the new counter, then serves
# a replay header (base case ``counter-behind`` serves recorded-1) to the target's
# isolated consumer. REJECT (counter-too-small) is the pass; accept-after-restart
# is the finding — but ONLY once the consumer has adopted the rotated counter-N
# block (the adopt gate): an accept from a consumer still on its stale
# pre-rotation view is a false positive, scored inconclusive, never a finding. A
# cycle that never completes the rotation/restart/forge, or whose consumer never
# adopts counter-N before the replay accept is read, is fail-closed inconclusive
# (never a pass). The original opcert is backed up and restored, leaving the
# devnet consistent.
# --------------------------------------------------------------------------- #

_OPCERT_FILES = ("opcert.cert", "opcert.counter")


def _read_counter_next(counter_json_text):
    try:
        doc = json.loads(counter_json_text)
        for tok in str(doc.get("description") or "").replace(":", " ").split():
            if tok.isdigit():
                return int(tok)
    except (ValueError, TypeError, AttributeError):
        pass
    return None


def _rotation_context(runtime, node_id):
    """Everything needed to rotate the pool opcert counter and restart the
    producer that forges the chain the target validates."""
    magic = int(runtime.get("network_magic") or 42)
    if _is_amaru_control(runtime):
        project = runtime.get("compose_project")
        image = det._container_image(f"{project}-p1-1")
        shelley = json.loads(det._docker(
            "run", "--rm", "-v", f"{project}_p1-configs:/c:ro", "busybox",
            "cat", "/c/configs/shelley-genesis.json").stdout)
        return {
            "mode": "volume", "project": project, "producer": f"{project}-p1-1",
            "image": image, "keys_mount": (f"{project}_p1-configs", "/vol"),
            "keys_in": "/vol/keys", "socket_in": "/state/node.socket", "magic": magic,
            "slots_per_kes": int(shelley["slotsPerKESPeriod"]),
            "restart_targets": [f"{project}-p1-1"],
        }
    node = _find_node(runtime, node_id)
    if node is None:
        raise RuntimeError(f"family-C rotation: node {node_id!r} not in runtime")
    container = node["container_name"]
    env_dir = det._host_env_dir(container)
    pool_dir = env_dir / "pools-keys" / "pool1"
    image = det._container_image(container)
    shelley = json.loads((env_dir / "shelley-genesis.json").read_text(encoding="utf-8"))
    socket = node.get("container_socket_path") or f"/env/socket/{node_id}/sock"
    return {
        "mode": "hostdir", "producer": container, "image": image, "pool_dir": pool_dir,
        "keys_mount": (str(pool_dir), "/keys"), "keys_in": "/keys",
        "socket_in": socket, "magic": magic,
        "slots_per_kes": int(shelley["slotsPerKESPeriod"]),
        "restart_targets": [container],
    }


def _backup_opcert(rot):
    if rot["mode"] == "hostdir":
        pd = rot["pool_dir"]
        for f in _OPCERT_FILES:
            src, bak = pd / f, pd / (f + ".soak-orig")
            if src.is_file() and not bak.is_file():
                shutil.copy2(src, bak)
    else:
        vol, _ = rot["keys_mount"]
        det._docker("run", "--rm", "-v", f"{vol}:/vol", "busybox", "sh", "-c",
                    "for f in opcert.cert opcert.counter; do "
                    "[ -f /vol/keys/$f ] && [ ! -f /vol/keys/$f.soak-orig ] && "
                    "cp /vol/keys/$f /vol/keys/$f.soak-orig; done; true", check=False)


def _restore_opcert(rot):
    try:
        if rot["mode"] == "hostdir":
            pd = rot["pool_dir"]
            for f in _OPCERT_FILES:
                bak = pd / (f + ".soak-orig")
                if bak.is_file():
                    shutil.copy2(bak, pd / f)
        else:
            vol, _ = rot["keys_mount"]
            det._docker("run", "--rm", "-v", f"{vol}:/vol", "busybox", "sh", "-c",
                        "for f in opcert.cert opcert.counter; do "
                        "[ -f /vol/keys/$f.soak-orig ] && cp /vol/keys/$f.soak-orig /vol/keys/$f; "
                        "done; true", check=False)
        _restart_producer(rot)
    except Exception:
        pass


def _producer_tip(rot):
    """Return ``(block_height, slot, block_hash)`` for the producer that forges
    the chain the target validates, or ``(None, None, None)``."""
    proc = det._docker("exec", rot["producer"], "cardano-cli", "query", "tip",
                       "--testnet-magic", str(rot["magic"]),
                       "--socket-path", rot["socket_in"], check=False)
    try:
        body = json.loads(proc.stdout)
        return body.get("block"), body.get("slot"), body.get("hash")
    except (ValueError, TypeError):
        return None, None, None


def _producer_tip_slot(rot):
    height, slot, _hash = _producer_tip(rot)
    return height, slot


def _restart_producer(rot):
    for c in rot["restart_targets"]:
        det._docker("restart", c, check=False)


def _wait_for_forge(rot, baseline_height, deadline):
    """Wait until the producer forges a block past ``baseline_height`` under the
    freshly rotated counter. Returns ``(forged_height, forged_hash)`` (the hash
    is what the isolated consumer's adopt gate later confirms), or
    ``(None, None)`` if no block was forged before the deadline."""
    while time.time() < deadline:
        height, _slot, block_hash = _producer_tip(rot)
        if isinstance(height, int) and not isinstance(height, bool):
            if baseline_height is None or height > baseline_height:
                return height, block_hash
        time.sleep(5)
    return None, None


def _rotate_producer(rot):
    """Issue one fresh cold-key-signed opcert (counter += 1), installing it in
    place. Returns the counter the just-issued cert carries, or ``None`` if the
    producer tip / issuance could not be driven (fail-closed upstream)."""
    _height, slot = _producer_tip_slot(rot)
    if slot is None:
        return None
    kes_period = int(slot) // int(rot["slots_per_kes"])
    src, dst = rot["keys_mount"]
    kdir = rot["keys_in"]
    cmd = (
        "set -e; cd " + kdir + "; "
        "if [ ! -f kes.vkey ]; then cardano-cli key verification-key "
        "--signing-key-file kes.skey --verification-key-file kes.vkey; fi; "
        "cardano-cli conway node issue-op-cert "
        "--kes-verification-key-file kes.vkey "
        "--cold-signing-key-file cold.skey "
        "--operational-certificate-issue-counter-file opcert.counter "
        f"--kes-period {kes_period} "
        "--out-file opcert.cert; "
        "cat opcert.counter"
    )
    proc = det._docker("run", "--rm", "-v", f"{src}:{dst}", "--entrypoint", "bash",
                       rot["image"], "-lc", cmd, check=False)
    if proc.returncode != 0:
        return None
    nxt = _read_counter_next(proc.stdout)
    return (nxt - 1) if isinstance(nxt, int) and nxt >= 1 else None


def _family_c_iteration(rot, spec, consumer_ctx, node, implementation, *, peer_bin,
                        per_iteration_timeout, output_dir, iteration,
                        restart_forge_timeout=300):
    """One real rotate→restart→forge→replay cycle with the readiness gate.

    Fail-closed inconclusive if the cycle does not complete the rotation,
    restart, and a forged block.

    Readiness gate — two paths:

    - cardano (``cardano-node``): the existing block-adopt gate. cardano's
      adoption of the counter-N block implies its opcert state is updated, so an
      accept is only a finding once the isolated consumer has adopted counter-N;
      an accept from a stale pre-rotation view is inconclusive.
    - amaru: the positive-control PROBE gate (``_amaru_probe_gate``). amaru's
      ``tip.adopt`` fires BEFORE it commits the rotated opcert sequence number
      and it emits no opcert-commit log, so block-adopt is insufficient. The
      gate serves a throwaway counter-(N-1) probe and waits until amaru's OWN
      verdict flips from accept to reject (recorded advanced N-1→N) before the
      real replay is served and scored. If the probe never rejects within the
      per-iteration budget the iteration is fail-closed inconclusive."""
    spec = dict(spec)
    spec["params"] = dict(spec.get("params") or {})
    baseline_height, _slot = _producer_tip_slot(rot)
    achieved = _rotate_producer(rot)
    forged_height = None
    forged_hash = None
    if achieved is not None:
        _restart_producer(rot)
        forged_height, forged_hash = _wait_for_forge(
            rot, baseline_height, time.time() + restart_forge_timeout)
    spec["params"]["rotated_to_counter_actual"] = achieved
    cycle = {"rotated_to_counter": achieved, "forged_height": forged_height,
             "forged_hash": forged_hash, "baseline_height": baseline_height,
             "consumer_adopted_counter_n": None, "probe_ready": None, "probe_polls": None}
    if achieved is None or forged_height is None:
        return {"outcome": "inconclusive", "spec": spec, "served_hash": None,
                "observed_verdict": None, "observed_reason": None, "cycle": cycle}

    if str(implementation) == "amaru":
        # Positive-control probe gate: use amaru's own verdict as the readiness
        # signal (log-independent). Only once the probe rejects (recorded==N) do
        # we serve + score the real replay; an accept then is a genuine
        # accept-after-restart finding (the state IS committed). A probe that
        # never rejects within the budget is fail-closed inconclusive.
        ready, polls = _amaru_probe_gate(
            spec, consumer_ctx, node, implementation, peer_bin=peer_bin,
            per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
            iteration=iteration)
        cycle["probe_ready"] = ready
        cycle["probe_polls"] = polls
        if not ready:
            return {"outcome": "inconclusive", "spec": spec, "served_hash": None,
                    "observed_verdict": None, "observed_reason": None, "cycle": cycle}
        served_hash, observed, adopted = _serve_one_gated(
            spec, consumer=consumer_ctx, node=node, adopt_hash=forged_hash,
            adopt_impl=implementation, peer_bin=peer_bin,
            per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
            iteration=iteration)
        cycle["consumer_adopted_counter_n"] = adopted
        # Readiness already proven by the probe: score the real replay directly
        # (never/reason-less => inconclusive via classify_iteration; accepted =>
        # the accept-after-restart finding; reject-with-reason => pass).
        if served_hash is None or observed is None:
            outcome = "inconclusive"
        else:
            outcome = R.classify_iteration(spec, served_hash, observed, implementation)
        return {"outcome": outcome, "spec": spec, "served_hash": served_hash,
                "observed_verdict": (observed or {}).get("verdict"),
                "observed_reason": (observed or {}).get("reason"), "cycle": cycle}

    served_hash, observed, adopted = _serve_one_gated(
        spec, consumer=consumer_ctx, node=node, adopt_hash=forged_hash,
        adopt_impl=implementation, peer_bin=peer_bin,
        per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
        iteration=iteration)
    cycle["consumer_adopted_counter_n"] = adopted
    # Adopt gate (cardano): fail-closed. A served-and-observed *accept* is only a
    # real accept-after-restart finding once the consumer has adopted counter-N;
    # an accept from a consumer still on its stale pre-rotation view is
    # inconclusive (never a pass, never a finding). Reject / never-observed keep
    # the deterministic driver's semantics.
    if served_hash is None or observed is None:
        outcome = "inconclusive"
    elif observed.get("verdict") == "accepted" and not adopted:
        outcome = "inconclusive"
    else:
        outcome = R.classify_iteration(spec, served_hash, observed, implementation)
    return {"outcome": outcome, "spec": spec, "served_hash": served_hash,
            "observed_verdict": (observed or {}).get("verdict"),
            "observed_reason": (observed or {}).get("reason"), "cycle": cycle}


# --------------------------------------------------------------------------- #
# The soak loop.
# --------------------------------------------------------------------------- #

def run_opcert_header_soak(runtime_root, family, seed, output_dir, *, target_node=None,
                           target_nodes=None, time_budget_seconds=5400, peer_bin=None,
                           per_iteration_timeout=240, restart_k=4, clock=time.monotonic):
    if family not in families.FAMILIES:
        raise ValueError(f"unknown family: {family!r}")
    differential = family in families.DIFFERENTIAL_FAMILIES
    if differential:
        if not target_nodes or len(target_nodes) != 2:
            raise ValueError(f"family {family} is differential; needs target_nodes of length 2")
        nodes = list(target_nodes)
    else:
        if not target_node:
            raise ValueError(f"family {family} is single-target; needs target_node")
        nodes = [target_node]

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    attempts_path = output_dir / "attempts.ndjson"
    result_path = output_dir / "result.json"

    runtime, implementation = _load_runtime_root(runtime_root)
    compose_project = (runtime or {}).get("compose_project")

    consumers = _open_consumers(runtime, nodes, peer_bin=peer_bin, output_dir=output_dir)

    if not differential:
        # Prefer the opened consumer's implementation: it is the robust source
        # across substrates (haskell_nodes is absent on the amaru-control
        # lifecycle, so _node_impl would return None there).
        ctx0 = consumers.get(nodes[0])
        implementation = ((ctx0.get("implementation") if isinstance(ctx0, dict) else None)
                          or _node_impl(runtime, nodes[0]) or implementation)

    family_c = family == "restart-persistence"
    rot = None
    if family_c:
        rot = _rotation_context(runtime, nodes[0])
        _backup_opcert(rot)

    # Differential families are served by one PERSISTENT forger per node that
    # streams the shared seed-deterministic spec dir; the loop below correlates
    # the two sides by served index rather than spawning a forger per iteration.
    spec_dir = None
    forgers = None
    if differential:
        spec_dir = output_dir / "harness" / "specs"
        _ensure_specs(family, seed, spec_dir,
                      min(20000, max(_SPEC_LOOKAHEAD, int(time_budget_seconds * 0.5)) + _SPEC_LOOKAHEAD),
                      restart_k=restart_k)
        forgers = {
            node: _launch_persistent_forger(spec_dir, consumers[node], peer_bin=peer_bin,
                                            output_dir=output_dir, node=node)
            for node in nodes
        }

    tip_before = _node_tip(runtime, nodes[0])
    records = []
    start = clock()
    iteration = 0
    try:
        while clock() - start < time_budget_seconds:
            spec = families.generate_case(family, seed, iteration, restart_k=restart_k)
            if differential:
                # Keep the spec stream ahead of both forgers, then correlate this
                # index's served case + verdict on each side (fail-closed: a side
                # that never served/observed index ``iteration`` is None ⇒ the
                # iteration is inconclusive, never agree).
                _ensure_specs(family, seed, spec_dir, iteration + _SPEC_LOOKAHEAD, restart_k=restart_k)
                verdicts = {}
                served = {}
                for node in nodes:
                    served_hash, observed = _await_indexed_verdict(
                        forgers[node], iteration, timeout=per_iteration_timeout)
                    # Fail-closed: a wedged (dark) consumer, or a reason-less
                    # reject (transport wedge), is inconclusive on that side --
                    # never a false hard reject / disagreement (iter-243 fix).
                    if _consumer_alive(forgers[node]):
                        verdicts[node] = R.conclusive_verdict(observed)
                    else:
                        verdicts[node] = None
                    served[node] = served_hash
                diff = R.classify_differential(verdicts[nodes[0]], verdicts[nodes[1]])
                record = {
                    "outcome": _OUTCOME_FOR_DIFF[diff], "differential": diff,
                    "spec": spec, "verdicts": verdicts, "served_hashes": served,
                }
            elif family_c:
                record = _family_c_iteration(
                    rot, spec, consumers.get(nodes[0]), nodes[0], implementation,
                    peer_bin=peer_bin, per_iteration_timeout=per_iteration_timeout,
                    output_dir=output_dir, iteration=iteration)
            else:
                served_hash, observed = _serve_one(
                    spec, consumer=consumers.get(nodes[0]), node=nodes[0], peer_bin=peer_bin,
                    per_iteration_timeout=per_iteration_timeout, output_dir=output_dir,
                    iteration=iteration)
                outcome = R.classify_iteration(spec, served_hash, observed, implementation)
                record = {
                    "outcome": outcome, "spec": spec, "served_hash": served_hash,
                    "observed_verdict": (observed or {}).get("verdict"),
                    "observed_reason": (observed or {}).get("reason"),
                }
            with attempts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            records.append(record)
            iteration += 1
    finally:
        if forgers is not None:
            for handle in forgers.values():
                _stop_forger(handle)
        if rot is not None:
            _restore_opcert(rot)
        _close_consumers(consumers)

    tip_after = _node_tip(runtime, nodes[0])
    duration = clock() - start
    target_health = {
        "before": {}, "after": {},
        "tip_before": tip_before or {}, "tip_after": tip_after or {},
        "log_signals": {},
    }
    result = R.build_soak_result(
        family=family, seed=seed, differential=differential, target_nodes=nodes,
        records=records, duration_seconds=duration, runtime_root=str(runtime_root),
        compose_project=compose_project, target_health=target_health)
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a randomized opcert-header soak against a target.")
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--family", required=True, choices=list(families.FAMILIES))
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--target-node", default="")
    parser.add_argument("--target-nodes", default="")
    parser.add_argument("--time-budget-seconds", type=int, default=5400)
    parser.add_argument("--peer-bin", default="")
    parser.add_argument("--per-iteration-timeout", type=int, default=240)
    parser.add_argument("--restart-k", type=int, default=4)
    args = parser.parse_args(argv)
    target_nodes = [n for n in args.target_nodes.split(",") if n] or None
    result = run_opcert_header_soak(
        runtime_root=args.runtime_root, family=args.family, seed=args.seed,
        output_dir=args.output_dir, target_node=args.target_node or None,
        target_nodes=target_nodes, time_budget_seconds=args.time_budget_seconds,
        peer_bin=args.peer_bin or None, per_iteration_timeout=args.per_iteration_timeout,
        restart_k=args.restart_k)
    print(json.dumps({k: result[k] for k in ("family", "seed", "iterations", "conclusive",
                                             "inconclusive", "pass", "counters")}, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
