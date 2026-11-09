"""Live block-apply bridge on a self-composed docker devnet (Amaru target, cardano-node reference).

Steps (each driven by one existing block-apply primitive, sharing ``bridge-state.json``):

1. ``craft``   (build_block_segments, ``recipe``): build the crafted transaction live against
   the devnet's own genesis funding — a setup transaction is submitted honestly to the
   cardano-node producer and confirmed; the crafted transaction is only ever forged.
2. ``plan``    (forge_block): resolve the producer pool's KES key and KES period length from
   the substrate, prove the key is the one the pool's opcert certifies, write the forge plan.
3. ``serve``   (serve_crafted_block): start the mux proxy in ``forge_substitute`` mode in front
   of the producer, repoint the Amaru target to it, start an isolated cardano-node reference
   consumer that only follows the proxy, wait until both follow the producer tip, take the
   pre-forge pot snapshots, then arm the forge.
4. ``observe`` (block_apply_differential): read each node's own verdict on the forged block
   (Amaru: its consensus log for the forged header hash; cardano-node: its tip and its log),
   then (optionally) the post-boundary pot snapshots.

Nothing is inferred: a node whose verdict is not observed is ``not-observed``; a snapshot whose
epoch cannot be aligned is recorded with ``aligned: false`` and the differentials fail closed.
"""
from __future__ import annotations

import contextlib
import uuid
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable

STATE_FILE = "bridge-state.json"
DONATION_RECIPE = "isvalid-false-donation"
SCRIPT_ROOT = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_ROOT.parent
ALWAYS_FAILS_SCRIPT = DWARF_ROOT / "block_apply" / "fixtures" / "always-fails-v3.plutus"

_AMARU_VALIDATION = re.compile(r'chain\.select_from_block_validation\b.*?\bvalid=(true|false)\b.*?header_hash="([0-9a-f]{64})"')
_AMARU_ADOPT = re.compile(r'tip\.adopt slot=(\d+) header_hash="([0-9a-f]{64})" block_height=(\d+)')
_AMARU_POTS = re.compile(r"pots\.dump treasury=(\d+) reserves=(\d+) fees=(\d+) donations=(\d+)")
_AMARU_PANIC = re.compile(r"panicked at|thread '.*' panicked|unreachable code", re.I)


# --------------------------------------------------------------------------- #
# State shared by the bridge primitives
# --------------------------------------------------------------------------- #

def bridge_dir(run_dir: Path) -> Path:
    path = Path(run_dir) / "outputs" / "block-apply-bridge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_state(run_dir: Path) -> dict[str, Any]:
    path = bridge_dir(run_dir) / STATE_FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    path = bridge_dir(run_dir) / STATE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Substrate facts (read from the devnet itself, never assumed)
# --------------------------------------------------------------------------- #

def find_node(runtime: dict, node_id: str) -> dict:
    for node in list(runtime.get("haskell_nodes") or []) + list(runtime.get("amaru_nodes") or []):
        if node.get("id") == node_id:
            return node
    raise RuntimeError(f"node {node_id!r} is not in the runtime metadata")


def genesis_params(runtime_root: Path) -> dict[str, Any]:
    shelley = json.loads((Path(runtime_root) / "env" / "shelley-genesis.json").read_text(encoding="utf-8"))
    return {
        "slots_per_kes_period": int(shelley["slotsPerKESPeriod"]),
        "epoch_length": int(shelley["epochLength"]),
        "security_param": int(shelley["securityParam"]),
        "active_slots_coeff": float(shelley["activeSlotsCoeff"]),
        "network_magic": int(shelley.get("networkMagic") or 0),
        "slot_length": float(shelley["slotLength"]),
    }


def producer_pool_dir(runtime_root: Path, producer: dict) -> Path:
    index = producer.get("host_slot_index")
    if index is None:
        raise RuntimeError(f"producer {producer.get('id')} has no host_slot_index (pool key slot)")
    return Path(runtime_root) / "env" / "pools-keys" / f"pool{int(index)}"


def opcert_hot_key(opcert_path: Path) -> tuple[bytes, int]:
    """(KES hot verification key, opcert start KES period) from a cardano-cli opcert envelope."""
    import cbor2  # noqa: PLC0415

    body = json.loads(Path(opcert_path).read_text(encoding="utf-8"))
    cert = cbor2.loads(bytes.fromhex(body["cborHex"]))
    hot_vk, _counter, kes_period, _sigma = cert[0]
    return bytes(hot_vk), int(kes_period)


def epoch_of(slot: int, *, anchor_slot: int, anchor_epoch: int, anchor_slot_in_epoch: int, epoch_length: int) -> int:
    """Epoch of ``slot`` from a node-reported anchor (constant-length Shelley-based epochs)."""
    epoch_start = anchor_slot - anchor_slot_in_epoch
    return anchor_epoch + (int(slot) - epoch_start) // int(epoch_length)


# --------------------------------------------------------------------------- #
# Amaru: verdicts and pots from its own log
# --------------------------------------------------------------------------- #

def amaru_header_verdict(log_text: str, header_hash: str) -> dict[str, Any]:
    """Amaru's own verdict on one header hash, from its consensus log lines."""
    validated = None
    adopted = False
    reason_lines = []
    for line in log_text.splitlines():
        if header_hash not in line:
            continue
        match = _AMARU_VALIDATION.search(line)
        if match and match.group(2) == header_hash:
            validated = match.group(1) == "true"
        adopt = _AMARU_ADOPT.search(line)
        if adopt and adopt.group(2) == header_hash:
            adopted = True
        lowered = line.lower()
        if any(word in lowered for word in ("invalid", "error", "fail", "reject")) and "valid=true" not in lowered:
            reason_lines.append(line.strip()[-600:])
    if validated is True or adopted:
        verdict = "adopt"
    elif validated is False or reason_lines:
        verdict = "reject"
    else:
        verdict = "not-observed"
    return {"verdict": verdict, "validated": validated, "adopted": adopted, "reason_lines": reason_lines[-5:]}


def amaru_tips(log_text: str) -> list[dict[str, Any]]:
    return [
        {"slot": int(m.group(1)), "hash": m.group(2), "block": int(m.group(3))}
        for m in _AMARU_ADOPT.finditer(log_text)
    ]


def amaru_last_pots(log_text: str) -> dict[str, int] | None:
    hits = _AMARU_POTS.findall(log_text)
    if not hits:
        return None
    t, r, f, d = hits[-1]
    return {"treasury": int(t), "reserves": int(r), "fees": int(f), "donations": int(d)}


def amaru_crashed(log_text: str) -> bool:
    return bool(_AMARU_PANIC.search(log_text))


# --------------------------------------------------------------------------- #
# cardano-node reference: pots from its ledger state
# --------------------------------------------------------------------------- #

_POT_KEYS = {
    "treasury": "treasury", "castreasury": "treasury",
    "reserves": "reserves", "casreserves": "reserves",
    "fees": "fees", "donation": "donations", "donations": "donations", "utxosdonation": "donations",
}


def cardano_pots(ledger_state: Any) -> dict[str, int]:
    """First treasury/reserves/fees/donation found in a ``query ledger-state`` JSON."""
    found: dict[str, int] = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                name = _POT_KEYS.get(str(key).lower())
                if name and isinstance(value, int) and not isinstance(value, bool):
                    found.setdefault(name, int(value))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ledger_state)
    return found


# --------------------------------------------------------------------------- #
# Per-node verdicts -> one block-apply outcome
# --------------------------------------------------------------------------- #

def combine_verdicts(target: dict, reference: dict) -> str:
    t, r = target.get("verdict"), reference.get("verdict")
    if target.get("crashed"):
        return "crash"
    if "not-observed" in (t, r) or t is None or r is None:
        return "not-observed"
    if t == r:
        return t
    if t == "adopt" and r == "reject":
        return "accept_invalid"
    if t == "reject" and r == "adopt":
        return "reject_valid"
    return f"target={t},reference={r}"


def pot_total(pots: dict | None) -> int | None:
    if not pots or "treasury" not in pots:
        return None
    return int(pots.get("treasury", 0)) + int(pots.get("donations", 0))


def value_creation_verdict(snapshots: dict, *, donation: int) -> dict[str, Any]:
    """Classify the is_valid=false donation: value the target created that the reference did not.

    Both nodes follow the same forked chain; both before snapshots are in the forged block's
    epoch and both after snapshots in the next epoch (``aligned``). Treasury and donations
    can then differ only by what each node did with the dropped-body donation.
    """
    if not snapshots.get("aligned"):
        return {"verdict": "inconclusive", "reason": snapshots.get("alignment_note") or "snapshots not epoch-aligned"}
    deltas = {}
    for node in ("amaru", "cardano"):
        before, after = pot_total(snapshots.get(f"{node}_before")), pot_total(snapshots.get(f"{node}_after"))
        if before is None or after is None:
            return {"verdict": "inconclusive", "reason": f"{node} pots not measured"}
        deltas[node] = after - before
    created = deltas["amaru"] - deltas["cardano"]
    if created == 0:
        verdict = "no_body_leak"
    elif created == donation:
        verdict = "VALUE_CREATION_DIVERGENCE"
    elif created == -donation:
        verdict = "REFERENCE_VALUE_CREATION"
    else:
        verdict = "POT_DIVERGENCE_UNEXPLAINED"
    return {
        "verdict": verdict,
        "amaru_leaked": created == donation,
        "cardano_leaked": created == -donation,
        "evidence": {"amaru_treasury_plus_donations_delta": deltas["amaru"],
                     "cardano_treasury_plus_donations_delta": deltas["cardano"],
                     "value_created_by_target": created, "crafted_donation": donation},
    }


# --------------------------------------------------------------------------- #
# Amaru repoint (docker compose)
# --------------------------------------------------------------------------- #

_PEER_ARG = re.compile(r'--peer-address\s+("[^"]*"|\S+)')


def repoint_compose_command(command: str, proxy_address: str) -> str:
    """Replace every ``--peer-address`` of an Amaru compose command with the proxy only."""
    if not _PEER_ARG.search(command):
        raise RuntimeError("Amaru compose command has no --peer-address to repoint")
    first = [True]

    def swap(_match):
        if first[0]:
            first[0] = False
            return f"--peer-address {json.dumps(proxy_address)}"
        return ""

    return _PEER_ARG.sub(swap, command)


# --------------------------------------------------------------------------- #
# Live orchestration (controller + docker); seams kept small for tests
# --------------------------------------------------------------------------- #

def _run(command: list[str], *, cwd: Path | None = None, timeout: float = 120):
    from scripts.runtime_substrate_common import run_command  # noqa: PLC0415

    return run_command(command, cwd=cwd, timeout=timeout)


def _checked(command: list[str], *, cwd: Path | None = None, timeout: float = 120) -> str:
    result = _run(command, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command[:4])} failed: {(result.stderr or result.stdout).strip()[:800]}")
    return result.stdout


def _read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _wait(predicate: Callable[[], Any], *, timeout: float, interval: float = 2.0, what: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise TimeoutError(f"timed out after {timeout:.0f}s waiting for {what}")


def craft_isvalid_false_donation(*, runtime: dict, out_dir: Path, reference_node: str, donation: int) -> dict[str, Any]:
    """Setup (honest, confirmed) + crafted is_valid=false donation (never submitted).

    Mirrors the confirmed finding's transaction shape: a key-locked input, a separate
    collateral input, one token minted under an always-failing Plutus V3 policy (so phase-2
    fails and is_valid=false is the CORRECT flag), and a treasury donation in the body.
    A conformant ledger consumes only the collateral and drops the whole body.
    """
    try:
        from scripts import block_forge  # noqa: PLC0415
    except ImportError:  # script mode
        import block_forge  # noqa: PLC0415
    from scripts.runtime_cardano_measurement_calibration import (  # noqa: PLC0415
        _containerized_cardano_workload, _controller_container, _query_utxo, _transaction_id, _wait_for_utxo,
    )
    from scripts.runtime_redeemer_container_live_controls import (  # noqa: PLC0415
        _select_funding_input, _submit_reference_cli,
    )
    from scripts.runtime_transaction_array_arity import _funding_credentials  # noqa: PLC0415
    import cbor2  # noqa: PLC0415

    controller = _controller_container()
    if controller is None:
        raise RuntimeError("the live block-apply bridge requires the installed Docker controller")
    runtime_root = Path(str(runtime["runtime_root"]))
    reference = find_node(runtime, reference_node)
    magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    credential = _funding_credentials(runtime, runtime_root)[0]
    address = credential["address"]
    out_dir.mkdir(parents=True, exist_ok=True)
    script = out_dir / "always-fails.plutus"
    shutil.copyfile(ALWAYS_FAILS_SCRIPT, script)
    record: dict[str, Any] = {"recipe": DONATION_RECIPE, "donation": donation, "funding_credential": credential["id"],
                              "script_sha256": digest(script)}
    spend, collateral, setup_fee, fee = 10_000_000, 20_000_000, 1_000_000, 2_000_000
    if spend - fee - donation < 1_500_000:
        raise RuntimeError(f"donation {donation} leaves no min-UTxO output from the {spend} input")
    with _containerized_cardano_workload(runtime_root=runtime_root, runtime=runtime, node=reference,
                                         output_dir=out_dir, controller=controller) as (workload, _node):
        cli = str(workload["support_binaries"]["cardano-cli"])
        policy = _checked([cli, "conway", "transaction", "policyid", "--script-file", str(script)]).strip()
        pparams = out_dir / "protocol-parameters.json"
        _checked([cli, "conway", "query", "protocol-parameters", "--socket-path", socket_path,
                  "--testnet-magic", str(magic), "--out-file", str(pparams)])
        utxos = _query_utxo(cli=cli, socket_path=socket_path, network_magic=magic,
                            address=address, destination=out_dir / "funding-before.json")
        funding_input, funding_lovelace = _select_funding_input(utxos, minimum_lovelace=100_000_000)
        setup_body, setup_signed = out_dir / "setup.body", out_dir / "setup.signed"
        _checked([cli, "conway", "transaction", "build-raw", "--tx-in", funding_input,
                  "--tx-out", f"{address}+{spend}", "--tx-out", f"{address}+{collateral}",
                  "--tx-out", f"{address}+{funding_lovelace - spend - collateral - setup_fee}",
                  "--fee", str(setup_fee), "--out-file", str(setup_body)])
        _checked([cli, "conway", "transaction", "sign", "--tx-body-file", str(setup_body),
                  "--signing-key-file", credential["container_skey"], "--testnet-magic", str(magic),
                  "--out-file", str(setup_signed)])
        setup_id = _transaction_id(cli, setup_signed)
        submitted = _submit_reference_cli(cli, setup_signed, socket_path=socket_path, network_magic=magic)
        record["setup"] = {"transaction_id": setup_id, "funding_input": funding_input, "submit": submitted}
        if submitted.get("http") != 202:
            raise RuntimeError(f"setup transaction was not accepted by the reference producer: {submitted.get('body')}")
        _, inclusion_micros = _wait_for_utxo(cli=cli, socket_path=socket_path, network_magic=magic, address=address,
                                             destination=out_dir / "setup-included-utxo.json",
                                             transaction_id=setup_id, present=True, timeout_seconds=180)
        record["setup"]["included_after_micros"] = inclusion_micros
        asset = f"{policy}.4c33"  # "L3", as in the confirmed finding's transaction
        crafted_body, crafted_signed = out_dir / "crafted.body", out_dir / "crafted.signed"
        _checked([cli, "conway", "transaction", "build-raw", "--script-invalid",
                  "--tx-in", f"{setup_id}#0", "--tx-in-collateral", f"{setup_id}#1",
                  "--mint", f"1 {asset}", "--mint-script-file", str(script),
                  "--mint-redeemer-value", "1", "--mint-execution-units", "(500000000, 2000000)",
                  "--tx-out", f"{address}+{spend - fee - donation}+1 {asset}",
                  "--treasury-donation", str(donation), "--fee", str(fee),
                  "--protocol-params-file", str(pparams), "--out-file", str(crafted_body)])
        _checked([cli, "conway", "transaction", "sign", "--tx-body-file", str(crafted_body),
                  "--signing-key-file", credential["container_skey"], "--testnet-magic", str(magic),
                  "--out-file", str(crafted_signed)])
        record["crafted"] = {"transaction_id": _transaction_id(cli, crafted_signed), "file": str(crafted_signed),
                             "spent_input": f"{setup_id}#0", "collateral_input": f"{setup_id}#1",
                             "policy_id": policy, "fee": fee}
    raw = bytes.fromhex(json.loads(crafted_signed.read_text(encoding="utf-8"))["cborHex"])
    parts = block_forge.split_transaction(raw)
    body = cbor2.loads(parts.body)
    if parts.is_valid is not False or body.get(22) != donation:
        raise RuntimeError("crafted transaction is not an is_valid=false donation of the requested amount")
    record["crafted"].update({"is_valid": parts.is_valid, "donation_field": body.get(22),
                              "sha256": hashlib.sha256(raw).hexdigest()})
    record["transaction_files"] = [str(crafted_signed)]
    return record


# --------------------------------------------------------------------------- #
# Recipe gov-scriptcontext: the governance ScriptContext sequence, built live
# --------------------------------------------------------------------------- #
#
# The fixture set (antithesis/.../fixture/gov_scriptcontext) encodes, on the 97b0 re-bake:
#   reg.tx    Conway stake registration (cert 7, deposit) of the proposal return credential
#   pplain.tx a plain Info proposal (gov action 6) returning its deposit to that credential
#   prop3.tx  an Info proposal plus three Plutus V3 MINT observers whose ScriptContext reads
#             tx.proposal_procedures: ctx_proposal_is_info (redeemer 1: the single proposal is
#             NicePoll), ctx_proposal_deposit (redeemer = deposit) and ctx_num_proposals (redeemer 1)
#   vote.tx   a StakePool Yes vote on pplain's action plus the ctx_votes MINT observer, whose
#             ScriptContext must hold exactly that pool -> {action: Yes} entry in tx.votes
# Live, the same shapes are rebuilt from the run's genesis funding: an honest split (with the
# return-credential registration) and an honest plain proposal are submitted to the producer and
# confirmed, prop3 and vote are built against that live state as the submit SUBJECTS, and a
# second registration + plain proposal pair (never submitted) is the forged block body.

GOV_SCRIPTCONTEXT_RECIPE = "gov-scriptcontext"
GOV_FIXTURE_DIR = DWARF_ROOT.parent / "antithesis" / "cardano_amaru_adversarial" / "fixture" / "gov_scriptcontext"
GOV_FIXTURE_POOL = "97b0f582dcf255b2c776f3540454b99fbf26e48833f275bfcd63bfcd"
GOV_ANCHOR = ("https://dwarf.test/a", "249d3389a61084339bdd2c55fd42b8dcc35357e0705b515ecc3a2a4e50babc11")
GOV_TOKEN = "434f4c4c"  # "COLL", the token name every fixture observer mints
PROP3_OBSERVERS = {  # role -> fixture script; the order is the fixture's --mint order
    "is_info": "ctx_proposal_is_info.plutus",
    "deposit": "ctx_proposal_deposit.plutus",
    "num_proposals": "ctx_num_proposals.plutus",
}
VOTES_OBSERVER = "ctx_votes.plutus"
ADA = 1_000_000
INFO_ACTION_TAG = 6
STAKE_REGISTRATION_TAG = 7
STAKE_POOL_VOTER_TAG = 4
VOTE_YES = 1


def _blake2b224(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=28).hexdigest()


def script_policy_id(envelope: dict) -> str:
    """Plutus V3 script hash (= minting policy id) of a cardano-cli script envelope."""
    import cbor2  # noqa: PLC0415

    return _blake2b224(b"\x03" + cbor2.loads(bytes.fromhex(envelope["cborHex"])))


def key_hash_from_vkey(path: Path) -> str:
    """Blake2b-224 key hash of a cardano-cli verification key envelope (stake / pool cold key)."""
    import cbor2  # noqa: PLC0415

    key = cbor2.loads(bytes.fromhex(json.loads(Path(path).read_text(encoding="utf-8"))["cborHex"]))
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError(f"{path} is not an Ed25519 verification key envelope")
    return _blake2b224(key)


def parameterize_ctx_votes(envelope: dict, pool: str, *, current: str = GOV_FIXTURE_POOL) -> dict:
    """ctx_votes with its pool constant swapped for ``pool`` (same length, nothing else changes).

    The Aiken source (ctxvotes.ak) compares the voter with a 28-byte pool hash literal. In the
    compiled flat program that literal is a byte-aligned bytestring chunk ``01 1c <28 bytes> 00``,
    so the live pool's hash replaces it in place without changing any length or other byte.
    """
    if len(pool) != 56 or any(c not in "0123456789abcdef" for c in pool):
        raise ValueError(f"pool key hash must be 28 bytes of lowercase hex, got {pool!r}")
    raw = str(envelope["cborHex"])
    old, new = f"011c{current}00", f"011c{pool}00"
    if raw.count(old) != 1:
        raise ValueError("script does not hold exactly one ctx_votes pool literal")
    return {**envelope, "cborHex": raw.replace(old, new),
            "description": f"ctx_votes (ctxvotes.ak) for StakePool {pool}"}


def _items(value) -> list:
    import cbor2  # noqa: PLC0415

    if isinstance(value, cbor2.CBORTag):
        value = value.value
    return list(value or [])


def _redeemer_value(data):
    return data if isinstance(data, int) else repr(data)


def describe_gov_transaction(raw: bytes) -> dict[str, Any]:
    """What a Conway transaction carries for this recipe: certs, proposals, votes, mints, redeemers."""
    import cbor2  # noqa: PLC0415

    tx = cbor2.loads(raw)
    body, witnesses, is_valid = tx[0], tx[1], tx[2]
    certificates = []
    for cert in _items(body.get(4)):
        entry: dict[str, Any] = {"tag": int(cert[0])}
        if int(cert[0]) in (0, 1, 7, 8):
            entry["credential"] = bytes(cert[1][1]).hex()
        if int(cert[0]) in (7, 8):
            entry["deposit"] = int(cert[2])
        certificates.append(entry)
    proposals = [{"deposit": int(p[0]), "return_account": bytes(p[1])[1:].hex(), "action": int(p[2][0])}
                 for p in _items(body.get(20))]
    votes = []
    for voter, actions in (body.get(19) or {}).items():
        for action, procedure in actions.items():
            votes.append({"voter_tag": int(voter[0]), "voter": bytes(voter[1]).hex(),
                          "action_tx": bytes(action[0]).hex(), "action_index": int(action[1]),
                          "vote": int(procedure[0])})
    redeemers: dict[str, Any] = {}
    raw_redeemers = witnesses.get(5) if isinstance(witnesses, dict) else None
    if isinstance(raw_redeemers, dict):
        for (tag, index), (data, _units) in raw_redeemers.items():
            redeemers[f"{['spend', 'mint', 'cert', 'reward', 'vote', 'propose'][tag]}:{index}"] = _redeemer_value(data)
    elif raw_redeemers:
        for tag, index, data, _units in raw_redeemers:
            redeemers[f"{['spend', 'mint', 'cert', 'reward', 'vote', 'propose'][tag]}:{index}"] = _redeemer_value(data)
    return {
        "is_valid": is_valid,
        "inputs": sorted(f"{bytes(i[0]).hex()}#{int(i[1])}" for i in _items(body.get(0))),
        "certificates": certificates,
        "proposals": proposals,
        "votes": votes,
        "mint_policies": sorted(bytes(p).hex() for p in (body.get(9) or {})),
        "redeemers": redeemers,
    }


def _prop3_policies() -> dict[str, str]:
    return {role: script_policy_id(json.loads((GOV_FIXTURE_DIR / name).read_text(encoding="utf-8")))
            for role, name in PROP3_OBSERVERS.items()}


def check_prop3_subject(desc: dict, *, deposit: int, return_account: str) -> str | None:
    """None when ``desc`` is prop3's shape: one Info proposal + the three proposal observers."""
    if desc.get("is_valid") is not True:
        return "prop3 is not flagged is_valid=true"
    expected = [{"deposit": deposit, "return_account": return_account, "action": INFO_ACTION_TAG}]
    if desc.get("proposals") != expected:
        return f"prop3 proposal/deposit/return account is {desc.get('proposals')}, expected {expected}"
    policies = _prop3_policies()
    if desc.get("mint_policies") != sorted(policies.values()):
        return f"prop3 mint policies are {desc.get('mint_policies')}, expected the observers {sorted(policies.values())}"
    order = sorted(policies.values())
    wanted = {f"mint:{order.index(policy)}": (deposit if role == "deposit" else 1) for role, policy in policies.items()}
    if desc.get("redeemers") != wanted:
        return f"prop3 redeemers are {desc.get('redeemers')}, expected {wanted} (deposit observer gets the deposit)"
    return None


def check_vote_subject(desc: dict, *, pool: str, action_tx: str, policy: str) -> str | None:
    """None when ``desc`` is vote's shape: one StakePool Yes vote on (action_tx, 0) + ctx_votes."""
    if desc.get("is_valid") is not True:
        return "vote is not flagged is_valid=true"
    expected = [{"voter_tag": STAKE_POOL_VOTER_TAG, "voter": pool, "action_tx": action_tx, "action_index": 0,
                 "vote": VOTE_YES}]
    if desc.get("votes") != expected:
        return f"vote voter/action/vote is {desc.get('votes')}, expected {expected}"
    if desc.get("mint_policies") != [policy]:
        return f"vote mint policies are {desc.get('mint_policies')}, expected the ctx_votes observer {policy}"
    return None


def check_setup_pair(reg: dict, pplain: dict, *, stake_deposit: int, deposit: int) -> str | None:
    """None when the forged pair is reg.tx + pplain.tx's shape (registration, then a plain Info
    proposal returning to the credential registered in the same block)."""
    certs = reg.get("certificates") or []
    if len(certs) != 1 or certs[0].get("tag") != STAKE_REGISTRATION_TAG or certs[0].get("deposit") != stake_deposit:
        return f"first forged transaction registers no stake credential with deposit {stake_deposit}: {certs}"
    expected = [{"deposit": deposit, "return_account": certs[0]["credential"], "action": INFO_ACTION_TAG}]
    if pplain.get("proposals") != expected or pplain.get("mint_policies") or pplain.get("certificates"):
        return f"second forged transaction is not a plain Info proposal returning to it: {pplain.get('proposals')}"
    if reg.get("is_valid") is not True or pplain.get("is_valid") is not True:
        return "forged setup transactions must be is_valid=true"
    return None


def gov_split_plan(*, funding_lovelace: int, stake_deposit: int, gov_deposit: int, fee: int) -> dict[str, Any]:
    """Outputs of the honest split (which also registers the return credential, paying its deposit)."""
    outputs = [
        {"role": "pplain_h", "lovelace": gov_deposit + 10 * ADA},
        {"role": "prop3", "lovelace": gov_deposit + 20 * ADA},
        {"role": "prop3_collateral", "lovelace": 10 * ADA},
        {"role": "vote", "lovelace": 20 * ADA},
        {"role": "vote_collateral", "lovelace": 10 * ADA},
        {"role": "reg_f", "lovelace": 10 * ADA},
        {"role": "pplain_f", "lovelace": gov_deposit + 10 * ADA},
    ]
    change = funding_lovelace - sum(o["lovelace"] for o in outputs) - fee - stake_deposit
    if change < 10 * ADA:
        raise RuntimeError(f"funding UTxO of {funding_lovelace} lovelace cannot fund the gov sequence "
                           f"(three {gov_deposit} proposal deposits)")
    outputs.append({"role": "change", "lovelace": change})
    return {"outputs": outputs, "fee": fee, "stake_deposit": stake_deposit}


def _mint_value(policies: list[str]) -> str:
    return " + ".join(f"1 {policy}.{GOV_TOKEN}" for policy in policies)


def prop3_build_command(*, cli: str, socket_path: str, magic: int, tx_in: str, collateral: str,
                        scripts: dict[str, tuple[str, str]], deposit: int, action_file: str,
                        change_address: str, out_file: str) -> list[str]:
    """``transaction build`` for prop3: the cardano-node producer balances it and sizes the budgets."""
    cmd = [cli, "conway", "transaction", "build", "--socket-path", socket_path, "--testnet-magic", str(magic),
           "--tx-in", tx_in, "--tx-in-collateral", collateral,
           "--mint", _mint_value([policy for _path, policy in scripts.values()])]
    for role, (path, _policy) in scripts.items():
        cmd += ["--mint-script-file", path, "--mint-redeemer-value", str(deposit) if role == "deposit" else "1"]
    return cmd + ["--proposal-file", action_file, "--change-address", change_address, "--out-file", out_file]


def vote_create_command(*, cli: str, action_tx: str, cold_vkey: str, out_file: str) -> list[str]:
    return [cli, "conway", "governance", "vote", "create", "--yes", "--governance-action-tx-id", action_tx,
            "--governance-action-index", "0", "--cold-verification-key-file", cold_vkey, "--out-file", out_file]


def vote_build_command(*, cli: str, socket_path: str, magic: int, tx_in: str, collateral: str,
                       script: tuple[str, str], vote_file: str, change_address: str, out_file: str) -> list[str]:
    path, policy = script
    return [cli, "conway", "transaction", "build", "--socket-path", socket_path, "--testnet-magic", str(magic),
            "--tx-in", tx_in, "--tx-in-collateral", collateral, "--mint", _mint_value([policy]),
            "--mint-script-file", path, "--mint-redeemer-value", "1", "--vote-file", vote_file,
            "--witness-override", "2", "--change-address", change_address, "--out-file", out_file]


GOV_ANCHOR_DOCUMENT = b'{"title": "DWARF governance ScriptContext fidelity proposal"}\n'


def write_gov_anchor(out_dir: Path) -> tuple[str, str]:
    """A proposal anchor ``transaction build`` can verify offline (F151).

    cardano-cli 11.x downloads the anchor during ``transaction build``; the build folder is mounted
    for the cardano-cli container, so a file:// URL under it resolves there, with its real hash."""
    path = Path(out_dir) / "gov-anchor.json"
    path.write_bytes(GOV_ANCHOR_DOCUMENT)
    return f"file://{path}", hashlib.blake2b(GOV_ANCHOR_DOCUMENT, digest_size=32).hexdigest()


GOV_ANCHOR_SERVER = (
    "import http.server, os\n"
    "DOC = bytes.fromhex(os.environ['DWARF_ANCHOR_HEX'])\n"
    "class H(http.server.BaseHTTPRequestHandler):\n"
    "    def do_GET(self):\n"
    "        self.send_response(200); self.send_header('Content-Length', str(len(DOC))); self.end_headers()\n"
    "        self.wfile.write(DOC)\n"
    "    def log_message(self, *a):\n"
    "        pass\n"
    "http.server.HTTPServer(('0.0.0.0', 8080), H).serve_forever()\n"
)
GOV_ANCHOR_IMAGE = "dwarf/framework:current"


@contextlib.contextmanager
def serve_gov_anchor(out_dir: Path, *, image: str = GOV_ANCHOR_IMAGE):
    """Serve the proposal anchor over HTTP on Docker's default bridge, where the cardano-cli
    container runs (F152): cardano-cli 11.2.3 transaction build downloads and verifies the anchor
    and rejects file:// URLs. Yields (url, blake2b-256 hash); the container is removed on exit."""
    _, data_hash = write_gov_anchor(out_dir)  # the served bytes, kept as evidence
    name = f"dwarf-gov-anchor-{uuid.uuid4().hex[:12]}"
    _checked(["docker", "run", "-d", "--rm", "--name", name,
              "--label", "io.dwarf.managed=true", "--label", "io.dwarf.component=gov-anchor-server",
              "-e", f"DWARF_ANCHOR_HEX={GOV_ANCHOR_DOCUMENT.hex()}", "--entrypoint", "python3", image,
              "-c", GOV_ANCHOR_SERVER], timeout=120)
    try:
        address = _checked(["docker", "inspect", "-f", '{{(index .NetworkSettings.Networks "bridge").IPAddress}}',
                            name]).strip()
        if not address:
            raise RuntimeError("gov anchor server has no address on the default bridge")

        def ready():
            try:
                _checked(["docker", "exec", name, "python3", "-c",
                          "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/gov-anchor.json')"],
                         timeout=15)
                return True
            except Exception:  # noqa: BLE001 - not listening yet
                return None

        _wait(ready, timeout=60, interval=1, what="the gov anchor server to listen")
        yield f"http://{address}:8080/gov-anchor.json", data_hash
    finally:
        try:
            _checked(["docker", "rm", "-f", name], timeout=60)
        except Exception:  # noqa: BLE001 - already gone
            pass


def info_action_command(*, cli: str, deposit: int, stake_vkey: str, out_file: str,
                        anchor: tuple[str, str] = GOV_ANCHOR) -> list[str]:
    return [cli, "conway", "governance", "action", "create-info", "--testnet",
            "--governance-action-deposit", str(deposit), "--deposit-return-stake-verification-key-file", stake_vkey,
            "--anchor-url", anchor[0], "--anchor-data-hash", anchor[1], "--out-file", out_file]


def _signed_bytes(path: Path) -> bytes:
    return bytes.fromhex(json.loads(Path(path).read_text(encoding="utf-8"))["cborHex"])


def _write_unbuilt(path: Path, reason: str) -> None:
    Path(str(path) + ".unbuilt.json").write_text(
        json.dumps({"measured": False, "reason": reason}, indent=2) + "\n", encoding="utf-8")


def craft_gov_scriptcontext(*, runtime: dict, out_dir: Path, reference_node: str, target_node: str,
                            fee: int = 2 * ADA, adoption_timeout: float = 240) -> dict[str, Any]:
    """Honest setup (split + return-credential registration, plain proposal) on the producer,
    then the forged pair (registration + plain proposal, never submitted) and the subjects
    prop3 / vote (built against the confirmed live state, submitted later by the scenario).

    A subject that cannot be built is recorded as ``measured: false`` with its reason next to
    where its transaction would be (``subjects/<name>.tx.unbuilt.json``); nothing is substituted.
    """
    from scripts.runtime_cardano_measurement_calibration import (  # noqa: PLC0415
        _containerized_cardano_workload, _controller_container, _query_utxo, _transaction_id, _wait_for_utxo,
    )
    from scripts.runtime_redeemer_container_live_controls import (  # noqa: PLC0415
        _select_funding_input, _submit_reference_cli,
    )
    from scripts.runtime_transaction_array_arity import _funding_credentials  # noqa: PLC0415

    controller = _controller_container()
    if controller is None:
        raise RuntimeError("the live block-apply bridge requires the installed Docker controller")
    runtime_root = Path(str(runtime["runtime_root"]))
    reference, target = find_node(runtime, reference_node), find_node(runtime, target_node)
    magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    credential = _funding_credentials(runtime, runtime_root)[0]
    address = credential["address"]
    out_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir, keys_dir, subjects_dir = out_dir / "scripts", out_dir / "keys", out_dir / "subjects"
    for directory in (scripts_dir, keys_dir, subjects_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # The producer's own pool votes: its cold key hash is the ctx_votes literal.
    pool_index = int(reference["host_slot_index"])
    pool_hash = key_hash_from_vkey(producer_pool_dir(runtime_root, reference) / "cold.vkey")
    cold_vkey, cold_skey = (f"/env/pools-keys/pool{pool_index}/cold.vkey", f"/env/pools-keys/pool{pool_index}/cold.skey")
    prop3_scripts: dict[str, tuple[str, str]] = {}
    for role, name in PROP3_OBSERVERS.items():
        shutil.copyfile(GOV_FIXTURE_DIR / name, scripts_dir / name)
        prop3_scripts[role] = (str(scripts_dir / name),
                               script_policy_id(json.loads((scripts_dir / name).read_text(encoding="utf-8"))))
    votes_live = parameterize_ctx_votes(json.loads((GOV_FIXTURE_DIR / VOTES_OBSERVER).read_text(encoding="utf-8")), pool_hash)
    votes_script = scripts_dir / "ctx_votes-live.plutus"
    votes_script.write_text(json.dumps(votes_live, indent=2) + "\n", encoding="utf-8")
    votes_policy = script_policy_id(votes_live)

    commands: list[list[str]] = []
    record: dict[str, Any] = {
        "recipe": GOV_SCRIPTCONTEXT_RECIPE, "funding_credential": credential["id"], "pool_key_hash": pool_hash,
        "observers": {**{role: policy for role, (_p, policy) in prop3_scripts.items()}, "votes": votes_policy},
        "ctx_votes_sha256": digest(votes_script), "cardano_cli_commands": commands, "subjects": {},
    }

    def run(cmd: list[str], timeout: float = 120) -> str:
        commands.append(["cardano-cli", *cmd[1:]])
        return _checked(cmd, timeout=timeout)

    def submit_and_confirm(cli: str, signed: Path, label: str) -> str:
        tx_id = _transaction_id(cli, signed)
        commands.append(["cardano-cli", "conway", "transaction", "submit", "--tx-file", str(signed),
                         "--socket-path", socket_path, "--testnet-magic", str(magic)])
        submitted = _submit_reference_cli(cli, signed, socket_path=socket_path, network_magic=magic)
        record.setdefault("setup", {})[label] = {"transaction_id": tx_id, "submit": submitted}
        if submitted.get("http") != 202:
            raise RuntimeError(f"{label} was not accepted by the producer: {submitted.get('body')}")
        _, micros = _wait_for_utxo(cli=cli, socket_path=socket_path, network_magic=magic, address=address,
                                   destination=out_dir / f"{label}-included-utxo.json", transaction_id=tx_id,
                                   present=True, timeout_seconds=180)
        record["setup"][label]["included_after_micros"] = micros
        return tx_id

    cleanup = contextlib.ExitStack()
    try:
        with _containerized_cardano_workload(runtime_root=runtime_root, runtime=runtime, node=reference,
                                             output_dir=out_dir, controller=controller) as (workload, _node):
            cli = str(workload["support_binaries"]["cardano-cli"])
            pparams = out_dir / "protocol-parameters.json"
            run([cli, "conway", "query", "protocol-parameters", "--socket-path", socket_path,
                 "--testnet-magic", str(magic), "--out-file", str(pparams)])
            params = json.loads(pparams.read_text(encoding="utf-8"))
            stake_deposit, gov_deposit = int(params["stakeAddressDeposit"]), int(params["govActionDeposit"])
            record["deposits"] = {"stakeAddressDeposit": stake_deposit, "govActionDeposit": gov_deposit}

            stake = {}
            for name in ("h", "f"):  # h: honest return credential; f: registered inside the forged block
                vkey, skey, cert = keys_dir / f"stake-{name}.vkey", keys_dir / f"stake-{name}.skey", out_dir / f"reg-{name}.cert"
                run([cli, "conway", "stake-address", "key-gen", "--verification-key-file", str(vkey),
                     "--signing-key-file", str(skey)])
                run([cli, "conway", "stake-address", "registration-certificate", "--stake-verification-key-file",
                     str(vkey), "--key-reg-deposit-amt", str(stake_deposit), "--out-file", str(cert)])
                stake[name] = {"vkey": vkey, "skey": skey, "cert": cert, "hash": key_hash_from_vkey(vkey)}
            actions = {}
            anchor = cleanup.enter_context(serve_gov_anchor(out_dir))
            record["proposal_anchor"] = {"url": anchor[0], "data_hash": anchor[1], "served_by": "gov-anchor-server"}
            for label, owner in (("h", "h"), ("prop3", "h"), ("f", "f")):
                actions[label] = out_dir / f"info-{label}.action"
                run(info_action_command(cli=cli, deposit=gov_deposit, stake_vkey=str(stake[owner]["vkey"]),
                                        out_file=str(actions[label]), anchor=anchor))
            record["return_credentials"] = {"honest": stake["h"]["hash"], "forged": stake["f"]["hash"]}

            utxos = _query_utxo(cli=cli, socket_path=socket_path, network_magic=magic, address=address,
                                destination=out_dir / "funding-before.json")
            needed = 3 * gov_deposit + stake_deposit + 200 * ADA
            funding_input, funding_lovelace = _select_funding_input(utxos, minimum_lovelace=needed)
            plan = gov_split_plan(funding_lovelace=funding_lovelace, stake_deposit=stake_deposit,
                                  gov_deposit=gov_deposit, fee=fee)
            split_body, split_signed = out_dir / "split.body", out_dir / "split.signed"
            cmd = [cli, "conway", "transaction", "build-raw", "--tx-in", funding_input]
            for output in plan["outputs"]:
                cmd += ["--tx-out", f"{address}+{output['lovelace']}"]
            run(cmd + ["--certificate-file", str(stake["h"]["cert"]), "--fee", str(fee), "--out-file", str(split_body)])
            run([cli, "conway", "transaction", "sign", "--tx-body-file", str(split_body), "--signing-key-file",
                 credential["container_skey"], "--signing-key-file", str(stake["h"]["skey"]),
                 "--testnet-magic", str(magic), "--out-file", str(split_signed)])
            split_id = submit_and_confirm(cli, split_signed, "split_and_register")
            outref = {o["role"]: (f"{split_id}#{i}", o["lovelace"]) for i, o in enumerate(plan["outputs"])}
            record["split"] = {"transaction_id": split_id, "funding_input": funding_input, "plan": plan,
                               "outputs": {role: ref for role, (ref, _l) in outref.items()}}

            def plain_tx(label: str, role: str, *, cert: Path | None, action: Path | None, skeys: list[str]) -> Path:
                tx_in, lovelace = outref[role]
                deposit = (stake_deposit if cert else 0) + (gov_deposit if action else 0)
                body, signed = out_dir / f"{label}.body", out_dir / f"{label}.signed"
                cmd = [cli, "conway", "transaction", "build-raw", "--tx-in", tx_in,
                       "--tx-out", f"{address}+{lovelace - fee - deposit}"]
                if cert:
                    cmd += ["--certificate-file", str(cert)]
                if action:
                    cmd += ["--proposal-file", str(action)]
                run(cmd + ["--fee", str(fee), "--out-file", str(body)])
                sign = [cli, "conway", "transaction", "sign", "--tx-body-file", str(body)]
                for key in skeys:
                    sign += ["--signing-key-file", key]
                run(sign + ["--testnet-magic", str(magic), "--out-file", str(signed)])
                return signed

            pplain_h = plain_tx("pplain-h", "pplain_h", cert=None, action=actions["h"], skeys=[credential["container_skey"]])
            pplain_id = submit_and_confirm(cli, pplain_h, "plain_proposal")
            record["gov_action"] = {"transaction_id": pplain_id, "index": 0}

            # Amaru must hold the honest setup before it is asked about prop3 / vote.
            confirmed_tip = producer_tip(reference, magic)
            record["setup_tip"] = confirmed_tip
            target_log = Path(str(target.get("log_path") or ""))

            def target_holds_setup():
                tips = amaru_tips(_read_text(target_log))
                if confirmed_tip and tips and max(t["block"] for t in tips) >= int(confirmed_tip["block"]):
                    return tips[-1]
                return None

            not_ready = None
            if not confirmed_tip:
                not_ready = "the producer tip after the honest setup could not be read"
            else:
                try:
                    record["target_adoption"] = {"adopted": True, "tip": _wait(
                        target_holds_setup, timeout=adoption_timeout, interval=2,
                        what=f"{target_node} to adopt the block holding the honest setup")}
                except TimeoutError as exc:
                    not_ready = f"{target_node} did not adopt the honest setup chain: {exc}"
            if not_ready:
                record["target_adoption"] = {"adopted": False, "note": not_ready}

            # Forged block body: reg.tx + pplain.tx shapes, never submitted.
            reg_f = plain_tx("reg-f", "reg_f", cert=stake["f"]["cert"], action=None,
                             skeys=[credential["container_skey"], str(stake["f"]["skey"])])
            pplain_f = plain_tx("pplain-f", "pplain_f", cert=None, action=actions["f"], skeys=[credential["container_skey"]])
            reg_desc, pplain_desc = describe_gov_transaction(_signed_bytes(reg_f)), describe_gov_transaction(_signed_bytes(pplain_f))
            problem = check_setup_pair(reg_desc, pplain_desc, stake_deposit=stake_deposit, deposit=gov_deposit)
            if problem:
                raise RuntimeError(f"forged setup pair: {problem}")
            record["crafted"] = {
                "reg": {"transaction_id": _transaction_id(cli, reg_f), "file": str(reg_f), "describe": reg_desc},
                "pplain": {"transaction_id": _transaction_id(cli, pplain_f), "file": str(pplain_f), "describe": pplain_desc},
            }
            record["transaction_files"] = [str(reg_f), str(pplain_f)]

            # Subjects.
            def subject(name: str, build: Callable[[], Path], check: Callable[[dict], str | None]) -> None:
                destination = subjects_dir / f"{name}.tx"
                if not_ready:
                    _write_unbuilt(destination, not_ready)
                    record["subjects"][name] = {"measured": False, "reason": not_ready}
                    return
                try:
                    signed = build()
                    desc = describe_gov_transaction(_signed_bytes(signed))
                    problem = check(desc)
                    if problem:
                        raise RuntimeError(problem)
                    shutil.copyfile(signed, destination)
                    record["subjects"][name] = {"measured": True, "file": str(destination), "describe": desc,
                                                "transaction_id": _transaction_id(cli, signed),
                                                "sha256": digest(destination)}
                except Exception as exc:  # noqa: BLE001 - recorded, never substituted
                    reason = f"{name} could not be built on the live chain: {exc}"[:1500]
                    _write_unbuilt(destination, reason)
                    record["subjects"][name] = {"measured": False, "reason": reason}

            def build_prop3() -> Path:
                body, signed = out_dir / "prop3.body", out_dir / "prop3.signed"
                run(prop3_build_command(cli=cli, socket_path=socket_path, magic=magic, tx_in=outref["prop3"][0],
                                        collateral=outref["prop3_collateral"][0], scripts=prop3_scripts,
                                        deposit=gov_deposit, action_file=str(actions["prop3"]),
                                        change_address=address, out_file=str(body)))
                run([cli, "conway", "transaction", "sign", "--tx-body-file", str(body), "--signing-key-file",
                     credential["container_skey"], "--testnet-magic", str(magic), "--out-file", str(signed)])
                return signed

            def build_vote() -> Path:
                vote_file, body, signed = out_dir / "vote.file", out_dir / "vote.body", out_dir / "vote.signed"
                run(vote_create_command(cli=cli, action_tx=pplain_id, cold_vkey=cold_vkey, out_file=str(vote_file)))
                run(vote_build_command(cli=cli, socket_path=socket_path, magic=magic, tx_in=outref["vote"][0],
                                       collateral=outref["vote_collateral"][0], script=(str(votes_script), votes_policy),
                                       vote_file=str(vote_file), change_address=address, out_file=str(body)))
                run([cli, "conway", "transaction", "sign", "--tx-body-file", str(body), "--signing-key-file",
                     credential["container_skey"], "--signing-key-file", cold_skey, "--testnet-magic", str(magic),
                     "--out-file", str(signed)])
                return signed

            subject("prop3", build_prop3,
                    lambda d: check_prop3_subject(d, deposit=gov_deposit, return_account=stake["h"]["hash"]))
            subject("vote", build_vote,
                    lambda d: check_vote_subject(d, pool=pool_hash, action_tx=pplain_id, policy=votes_policy))
    finally:
        cleanup.close()
        for secret in keys_dir.glob("*.skey"):  # ephemeral stake keys never leave the run as evidence
            secret.unlink(missing_ok=True)
    return record


def write_forge_plan(*, runtime: dict, out_dir: Path, producer_node: str, transaction_files: list[str]) -> dict:
    try:
        from scripts import praos_kes  # noqa: PLC0415
    except ImportError:  # script mode
        import praos_kes  # noqa: PLC0415

    runtime_root = Path(str(runtime["runtime_root"]))
    producer = find_node(runtime, producer_node)
    pool = producer_pool_dir(runtime_root, producer)
    kes_skey = pool / "kes.skey"
    hot_vk, opcert_kes_period = opcert_hot_key(pool / "opcert.cert")
    derived = praos_kes.derive_ver_key(praos_kes.load_sign_key(kes_skey))
    if derived != hot_vk:
        raise RuntimeError(f"{kes_skey} is not the KES key {pool / 'opcert.cert'} certifies")
    params = genesis_params(runtime_root)
    plan = {
        "kes_skey": str(kes_skey),
        "slots_per_kes_period": params["slots_per_kes_period"],
        "transaction_files": [str(p) for p in transaction_files],
        "arm_file": str(out_dir / "arm.json"),
        "producer_node": producer_node,
        "opcert_hot_vkey": hot_vk.hex(),
        "opcert_kes_period": opcert_kes_period,
        "genesis": params,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "forge-plan.json").write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return plan


def _docker_inspect(name: str) -> dict:
    return json.loads(_checked(["docker", "inspect", name]))[0]


def _mount_source(inspected: dict, destination: str) -> str:
    for mount in inspected.get("Mounts") or []:
        if mount.get("Destination") == destination:
            return str(mount.get("Source"))
    raise RuntimeError(f"{inspected.get('Name')} has no {destination} mount")


def repoint_amaru(*, runtime: dict, runtime_metadata_path: Path, target: dict, proxy_address: str) -> dict:
    runtime_root = Path(str(runtime["runtime_root"]))
    compose_path = runtime_root / "docker-compose.yml"
    compose = json.loads(compose_path.read_text(encoding="utf-8"))
    service = compose["services"][target["id"]]
    before = service["command"][0]
    service["command"] = [repoint_compose_command(before, proxy_address)]
    compose_path.write_text(json.dumps(compose, indent=2) + "\n", encoding="utf-8")
    _checked(["docker", "compose", "--project-name", str(runtime["compose_project"]), "up", "-d", "--no-deps",
              target["id"]], cwd=runtime_root, timeout=180)
    inspected = _docker_inspect(str(target["container_name"]))
    target["container_id"] = inspected["Id"]
    target["container_peer_addresses"] = [proxy_address]
    runtime_metadata_path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"container_id": inspected["Id"], "peer_address": proxy_address, "running": inspected["State"]["Running"]}


def start_reference_consumer(*, runtime: dict, producer: dict, out_dir: Path, proxy: dict) -> dict:
    """An isolated cardano-node (fresh DB) whose only peer is the forging proxy."""
    from scripts.runtime_cardano_measurement_calibration import _controller_container, _owned_run_output_relative  # noqa: PLC0415

    name = f"{runtime['compose_project']}-blockapply-reference"
    consumer_dir = out_dir / "reference-consumer"
    consumer_dir.mkdir(parents=True, exist_ok=True)
    topology = json.loads(Path(str(producer["topology_path"])).read_text(encoding="utf-8"))
    topology["localRoots"] = [{
        "accessPoints": [{"address": proxy["host"], "port": int(proxy["port"])}],
        "advertise": False, "trustable": True, "valency": 1, "hotValency": 1, "warmValency": 1,
    }]
    topology["publicRoots"] = []
    topology.pop("bootstrapPeers", None)
    topology["useLedgerAfterSlot"] = -1
    (consumer_dir / "topology.json").write_text(json.dumps(topology, indent=2) + "\n", encoding="utf-8")
    env_source = _mount_source(_docker_inspect(str(producer["container_name"])), "/env")
    controller = _controller_container()
    if controller is None:
        raise RuntimeError("the live block-apply bridge requires the installed Docker controller")
    from scripts.runtime_cardano_measurement_calibration import _mount_source as _controller_mount  # noqa: PLC0415

    _abs, relative = _owned_run_output_relative(consumer_dir)
    consumer_source = str(Path(_controller_mount(controller, "/var/dwarf/runs", writable=True)) / relative)
    _run(["docker", "rm", "-f", name])
    _checked([
        "docker", "run", "-d", "--name", name, "--network", str(producer["container_network"]),
        "--label", "io.dwarf.managed=true", "--label", "io.dwarf.component=block-apply-reference-consumer",
        "-v", f"{env_source}:/env:ro", "-v", f"{consumer_source}:/bridge:ro",
        "--tmpfs", "/consumer:exec,size=2g",
        "--entrypoint", "cardano-node", str(producer["image_ref"]),
        "run", "--config", "/env/configuration.yaml", "--topology", "/bridge/topology.json",
        "--database-path", "/consumer/db", "--socket-path", "/consumer/sock",
        "--port", "3001", "--host-addr", "0.0.0.0",
    ])
    return {"name": name, "socket": "/consumer/sock", "image": producer["image_ref"]}


def consumer_query(name: str, magic: int, *args: str, timeout: float = 120) -> str:
    return _checked(["docker", "exec", name, "cardano-cli", "conway", "query", *args,
                     "--socket-path", "/consumer/sock", "--testnet-magic", str(magic)], timeout=timeout)


def _chain_tip(raw: str) -> dict | None:
    """A parsed ``query tip``; None at origin, where cardano-cli omits block/slot/hash."""
    tip = json.loads(raw)
    return tip if isinstance(tip, dict) and all(k in tip for k in ("block", "slot", "hash")) else None


def consumer_tip(name: str, magic: int) -> dict | None:
    try:
        return _chain_tip(consumer_query(name, magic, "tip"))
    except Exception:  # noqa: BLE001 - socket not up yet / node restarting
        return None


def consumer_pots(name: str, magic: int) -> dict:
    tip = consumer_tip(name, magic) or {}
    state = json.loads(consumer_query(name, magic, "ledger-state", timeout=300))
    return {**cardano_pots(state), "epoch": tip.get("epoch"), "slot": tip.get("slot"), "block": tip.get("block"),
            "hash": tip.get("hash")}


def amaru_restart_pots(*, target: dict, timeout: float = 240) -> dict:
    """Restart Amaru so it dumps its STABLE pots; return them with the tip it had at shutdown."""
    log_path = Path(str(target["log_path"]))
    text_before = _read_text(log_path)
    tips = amaru_tips(text_before)
    offset = len(text_before)
    _checked(["docker", "restart", str(target["container_name"])], timeout=180)

    def dumped():
        return amaru_last_pots(_read_text(log_path)[offset:])

    pots = _wait(dumped, timeout=timeout, what="amaru pots.dump after restart")
    return {**pots, "tip_at_restart": tips[-1] if tips else None, "tips_before_restart": tips[-50:]}


def amaru_stable_slot(snapshot: dict, security_param: int) -> int | None:
    """Slot of the block ``k`` below Amaru's tip at restart (its stable ledger point)."""
    tip = snapshot.get("tip_at_restart")
    if not tip:
        return None
    wanted = int(tip["block"]) - int(security_param)
    for entry in reversed(snapshot.get("tips_before_restart") or []):
        if int(entry["block"]) <= wanted:
            return int(entry["slot"])
    return None


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_from(path: Path, offset: int) -> str:
    try:
        with Path(path).open("rb") as handle:
            handle.seek(offset)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _size(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def producer_tip(producer: dict, magic: int) -> dict | None:
    try:
        return _chain_tip(_checked(["docker", "exec", str(producer["container_name"]), "cardano-cli", "conway", "query",
                                    "tip", "--socket-path", str(producer["container_socket_path"]),
                                    "--testnet-magic", str(magic)]))
    except Exception:  # noqa: BLE001
        return None


def cardano_consumer_verdict(*, tip: dict | None, forged_hashes: set[str], forged_block: int,
                             log_text: str, forged_hash: str) -> dict[str, Any]:
    """The reference consumer's own verdict: its tip on the fork, or its log rejecting the block."""
    reason_lines = [line.strip()[-600:] for line in log_text.splitlines()
                    if forged_hash in line and any(w in line for w in ("Invalid", "invalid", "Error", "error"))]
    if tip and tip.get("hash") in forged_hashes and int(tip.get("block") or -1) >= forged_block:
        return {"verdict": "adopt", "tip": tip, "reason_lines": reason_lines[-5:]}
    if reason_lines:
        return {"verdict": "reject", "tip": tip, "reason_lines": reason_lines[-5:]}
    return {"verdict": "not-observed", "tip": tip, "reason_lines": []}


ARM_DEADLINE = 0.9


def armable_after_snapshot(before_tip: dict, after_tip: dict, *, epoch_length: int,
                           deadline: float = ARM_DEADLINE) -> bool:
    """The pre-forge snapshot is usable only if the forge can still land in the snapshot's epoch.

    With short epochs (local reward-window timing: 400 slots of 0.5 s) restarting Amaru for its
    pots dump can take a large part of an epoch; a forge armed in the next epoch would make the
    before/after snapshots misaligned. Past ``deadline`` of the epoch the snapshot is retaken.
    """
    return (int(after_tip["epoch"]) == int(before_tip["epoch"])
            and int(after_tip["slotInEpoch"]) <= deadline * int(epoch_length))


def serve_and_arm(*, run_dir: Path, runtime_metadata_path: Path, target_node: str, reference_node: str,
                  snapshots: bool, sync_timeout: float = 900, arm_window: tuple[float, float] = (0.35, 0.65)) -> dict:
    from scripts.runtime_byzantine_peer import _launch_proxy_session, _pick_free_port  # noqa: PLC0415
    from scripts.runtime_chainsync_blockfetch_fault import _proxy_bind_address  # noqa: PLC0415

    state = load_state(run_dir)
    plan = state.get("plan")
    if not plan:
        raise RuntimeError("serve_crafted_block needs the forge plan written by forge_block")
    runtime = json.loads(runtime_metadata_path.read_text(encoding="utf-8"))
    target, producer = find_node(runtime, target_node), find_node(runtime, reference_node)
    if target.get("impl") != "amaru" or producer.get("impl") != "cardano-node":
        raise RuntimeError("the block-apply bridge serves an Amaru target against a cardano-node reference")
    magic = int(runtime["network_magic"])
    genesis = plan["genesis"]
    out = bridge_dir(run_dir)
    proxy_host = _proxy_bind_address(str(producer["container_network"]))
    proxy_port = _pick_free_port()
    upstream_port = str(producer.get("container_listen_address") or ":3001").rsplit(":", 1)[1]
    upstream = f"{producer.get('container_ip') or producer['id']}:{upstream_port}"
    session = f"{runtime['compose_project']}-blockapply-proxy"
    proxy_info = _launch_proxy_session(
        session=session, runtime_root=Path(str(runtime["runtime_root"])),
        listen_address=f"{proxy_host}:{proxy_port}", upstream_address=upstream, output_dir=out / "proxy",
        mutation_mode="forge_substitute", mutation_direction="both", mutation_protocol="any",
        mutate_after_segments=1, forge_plan=str(out / "forge-plan.json"),
    )
    runtime.setdefault("aux_sessions", []).append({"id": "block-apply-proxy", "kind": "block_apply_proxy", "session": session})
    runtime_metadata_path.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    proxy = {"host": proxy_host, "port": proxy_port, "upstream": upstream, **proxy_info}
    log_path = Path(str(target["log_path"]))
    log_offset = _size(log_path)
    repoint = repoint_amaru(runtime=runtime, runtime_metadata_path=runtime_metadata_path, target=target,
                            proxy_address=f"{proxy_host}:{proxy_port}")
    consumer = start_reference_consumer(runtime=runtime, producer=producer, out_dir=out, proxy=proxy)
    state["runtime_metadata_path"] = str(runtime_metadata_path)
    state.update({"serve": {"proxy": proxy, "repoint": repoint, "consumer": consumer, "log_offset": log_offset,
                            "target_node": target_node, "reference_node": reference_node}})
    save_state(run_dir, state)

    def synced():
        tip = producer_tip(producer, magic)
        ctip = consumer_tip(consumer["name"], magic)
        tips = amaru_tips(_read_from(log_path, log_offset))
        if not tip or not ctip or not tips:
            return None
        if ctip["block"] >= tip["block"] - 2 and tips[-1]["block"] >= tip["block"] - 2:
            return {"producer": tip, "consumer": ctip, "amaru": tips[-1]}
        return None

    sync = _wait(synced, timeout=sync_timeout, interval=3, what="Amaru and the reference consumer to follow the producer through the proxy")
    state["serve"]["synced"] = sync
    save_state(run_dir, state)
    before = None
    for _attempt in range(4):
        tip = _wait(lambda: producer_tip(producer, magic), timeout=60, what="producer tip")
        position = tip["slotInEpoch"] / genesis["epoch_length"]
        if snapshots and not arm_window[0] <= position <= arm_window[1]:
            wait_slots = (arm_window[0] * genesis["epoch_length"] - tip["slotInEpoch"]) % genesis["epoch_length"]
            time.sleep(max(1.0, wait_slots * float(genesis["slot_length"])))
            continue
        if snapshots:
            amaru_before = amaru_restart_pots(target=target)
            resumed_offset = _size(log_path)

            def resumed():
                ptip = producer_tip(producer, magic)
                tips = amaru_tips(_read_from(log_path, resumed_offset))
                return tips[-1] if ptip and tips and tips[-1]["block"] >= ptip["block"] - 2 else None

            _wait(resumed, timeout=180, interval=2, what="Amaru to resume at the tip after the snapshot restart")
            cardano_before = consumer_pots(consumer["name"], magic)
            after = _wait(lambda: producer_tip(producer, magic), timeout=60, what="producer tip")
            if not armable_after_snapshot(tip, after, epoch_length=genesis["epoch_length"]):
                state["serve"].setdefault("discarded_snapshots", []).append(
                    {"anchor": tip, "after": after, "reason": "snapshot ended past the arm deadline of its epoch"})
                save_state(run_dir, state)
                continue
            before = {"amaru": amaru_before, "cardano": cardano_before, "anchor": tip}
            tip = after
        break
    arm = {"armed_slot": int(tip["slot"]) + 1, "anchor": tip}
    (out / "arm.json").write_text(json.dumps(arm, indent=2) + "\n", encoding="utf-8")
    state["serve"].update({"arm": arm, "before": before, "anchor": tip})
    save_state(run_dir, state)
    return {"outcome": "armed", "proxy": proxy, "repoint": repoint, "consumer": consumer, "sync": sync, "arm": arm,
            "before_snapshot_taken": before is not None}


def _forge_record(run_dir: Path) -> dict:
    path = bridge_dir(run_dir) / "proxy" / "forge-record.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def observe(*, run_dir: Path, watch_seconds: float, snapshots: bool) -> dict:
    state = load_state(run_dir)
    serve = state.get("serve")
    if not serve:
        raise RuntimeError("block_apply_differential needs serve_crafted_block to have armed the bridge")
    runtime = json.loads(Path(state["runtime_metadata_path"]).read_text(encoding="utf-8"))
    target = find_node(runtime, serve["target_node"])
    magic = int(runtime["network_magic"])
    genesis = state["plan"]["genesis"]
    consumer = serve["consumer"]["name"]
    log_path = Path(str(target["log_path"]))
    result: dict[str, Any] = {"forge": None, "target": None, "reference": None}
    try:
        record = _wait(lambda: _forge_record(run_dir) if _forge_record(run_dir).get("forged_hash") else None,
                       timeout=watch_seconds, interval=2, what="the proxy to forge the crafted block")
    except TimeoutError as exc:
        result.update({"verdict": "not-forged", "note": str(exc), "forge": _forge_record(run_dir)})
        return result
    forged_hash = record["forged_hash"]
    forged_event = record.get("forged_event") or next(e for e in record["events"] if e.get("kind") == "forged")
    result["forge"] = {k: record.get(k) for k in ("armed_slot", "target_real_hash", "forged_hash",
                                                  "crafted_body_hash", "crafted_invalid_indices")}
    result["forge"].update({"slot": forged_event["slot"], "block_number": forged_event["block_number"]})

    def verdicts():
        current = _forge_record(run_dir)
        forged_set = {e["forged_hash"] for e in current.get("events", []) if e.get("forged_hash")}
        text = _read_from(log_path, int(serve["log_offset"]))
        amaru = amaru_header_verdict(text, forged_hash)
        inspected = _docker_inspect(str(target["container_name"]))
        amaru["crashed"] = (not inspected["State"]["Running"]) or amaru_crashed(text)
        logs = _run(["docker", "logs", "--tail", "4000", consumer], timeout=60)
        cardano = cardano_consumer_verdict(tip=consumer_tip(consumer, magic), forged_hashes=forged_set,
                                           forged_block=int(forged_event["block_number"]),
                                           log_text=(logs.stdout or "") + (logs.stderr or ""), forged_hash=forged_hash)
        if amaru["crashed"] or (amaru["verdict"] != "not-observed" and cardano["verdict"] != "not-observed"):
            return amaru, cardano
        return None

    try:
        amaru, cardano = _wait(verdicts, timeout=watch_seconds, interval=3, what="both nodes' verdicts on the forged block")
    except TimeoutError:
        amaru, cardano = None, None
    if amaru is None:  # record what each node showed when the watch ended (not-observed stays visible)
        current = _forge_record(run_dir)
        forged_set = {e["forged_hash"] for e in current.get("events", []) if e.get("forged_hash")}
        amaru = amaru_header_verdict(_read_from(log_path, int(serve["log_offset"])), forged_hash)
        logs = _run(["docker", "logs", "--tail", "4000", consumer], timeout=60)
        cardano = cardano_consumer_verdict(tip=consumer_tip(consumer, magic), forged_hashes=forged_set,
                                           forged_block=int(forged_event["block_number"]),
                                           log_text=(logs.stdout or "") + (logs.stderr or ""), forged_hash=forged_hash)
    result.update({"target": amaru, "reference": cardano, "verdict": combine_verdicts(amaru, cardano)})
    if snapshots:
        result["snapshots"] = collect_after_snapshots(run_dir=run_dir, state=state, target=target, consumer=consumer,
                                                      magic=magic, genesis=genesis, forged_slot=int(forged_event["slot"]),
                                                      both_adopted=result["verdict"] == "adopt")
    _run(["docker", "rm", "-f", consumer], timeout=60)
    state["observe"] = result
    save_state(run_dir, state)
    return result


def collect_after_snapshots(*, run_dir: Path, state: dict, target: dict, consumer: str, magic: int, genesis: dict,
                            forged_slot: int, both_adopted: bool, timeout: float | None = None) -> dict:
    serve = state["serve"]
    if timeout is None:  # up to three epochs of this devnet, never less than ten minutes
        timeout = max(600.0, 3 * int(genesis["epoch_length"]) * float(genesis["slot_length"]))
    before = serve.get("before")
    anchor = serve["anchor"]
    k = int(genesis["security_param"])

    def epoch(slot):
        return epoch_of(slot, anchor_slot=int(anchor["slot"]), anchor_epoch=int(anchor["epoch"]),
                        anchor_slot_in_epoch=int(anchor["slotInEpoch"]), epoch_length=int(genesis["epoch_length"]))

    forged_epoch = epoch(forged_slot)
    snapshots: dict[str, Any] = {"forged_epoch": forged_epoch, "aligned": False}
    if not before:
        snapshots["alignment_note"] = "no pre-forge snapshot was taken"
        return snapshots
    snapshots["amaru_before"], snapshots["cardano_before"] = before["amaru"], before["cardano"]
    if not both_adopted:
        snapshots["alignment_note"] = "the forged block was not adopted by both nodes; pots not compared"
        return snapshots
    log_path = Path(str(target["log_path"]))
    offset = int(serve["log_offset"])

    def past_boundary():
        ctip = consumer_tip(consumer, magic)
        tips = amaru_tips(_read_from(log_path, offset))
        if not ctip or len(tips) <= k:
            return None
        stable_slot = tips[-1 - k]["slot"]
        if ctip.get("epoch") == forged_epoch + 1 and epoch(stable_slot) == forged_epoch + 1:
            return ctip
        return None

    try:
        _wait(past_boundary, timeout=timeout, interval=3, what="both nodes' stable state to cross the epoch boundary")
    except TimeoutError as exc:
        snapshots["alignment_note"] = str(exc)
        return snapshots
    snapshots["amaru_after"] = amaru_restart_pots(target=target)
    snapshots["cardano_after"] = consumer_pots(consumer, magic)
    amaru_before_epoch = epoch(amaru_stable_slot(before["amaru"], k) or -10**9)
    amaru_after_epoch = epoch(amaru_stable_slot(snapshots["amaru_after"], k) or -10**9)
    epochs = {"amaru_before": amaru_before_epoch, "cardano_before": before["cardano"].get("epoch"),
              "amaru_after": amaru_after_epoch, "cardano_after": snapshots["cardano_after"].get("epoch")}
    snapshots["epochs"] = epochs
    snapshots["aligned"] = (epochs["amaru_before"] == epochs["cardano_before"] == forged_epoch
                            and epochs["amaru_after"] == epochs["cardano_after"] == forged_epoch + 1)
    if not snapshots["aligned"]:
        snapshots["alignment_note"] = f"snapshot epochs {epochs} are not forged epoch {forged_epoch} / {forged_epoch + 1}"
    pots_dir = bridge_dir(run_dir) / "pots"
    pots_dir.mkdir(parents=True, exist_ok=True)
    for name in ("amaru_before", "cardano_before", "amaru_after", "cardano_after"):
        (pots_dir / f"{name.replace('_', '-')}.json").write_text(
            json.dumps(snapshots[name], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshots
