#!/usr/bin/env python3
"""On-chain Plutus phase-2 differential between Amaru and cardano-node, version-agnostic.

For each case family (``plutus_cases``) the run funds fresh inputs on the running devnet, builds
one minting transaction per case and node against the live protocol parameters and cost models,
submits it to Amaru (container LocalTxSubmission) and to cardano-node (raw LocalTxSubmission
helper), and records each node's admission outcome. Every family first requires a control that
both nodes admit, include and adopt, so a rejection is never mistaken for coverage.

The node identities come from the resolved plan; nothing here names a release. Extends the
pattern of ``runtime_uplc_deep_delay_stack_safety``.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
from collections.abc import Callable, Mapping
from decimal import ROUND_CEILING
from pathlib import Path
from typing import Any

import cbor2

try:  # scripts directory (helpers) or the scripts package (tests, primitives)
    import plutus_cases
    from runtime_uplc_deep_delay_stack_safety import (
        _ledger_fee_transaction_size,
        _output_with_coin,
        _positive_decimal_parameter,
        _positive_parameter,
    )
except ImportError:  # pragma: no cover - import path depends on the caller
    from scripts import plutus_cases
    from scripts.runtime_uplc_deep_delay_stack_safety import (
        _ledger_fee_transaction_size,
        _output_with_coin,
        _positive_decimal_parameter,
        _positive_parameter,
    )

# Conway witness-set keys for the script languages (CDDL: 3 = V1, 6 = V2, 7 = V3).
WITNESS_KEYS = {"PlutusV1": 3, "PlutusV2": 6, "PlutusV3": 7}
SCRIPT_FILE_TYPES = {"PlutusV1": "PlutusScriptV1", "PlutusV2": "PlutusScriptV2", "PlutusV3": "PlutusScriptV3"}
CASE_INPUT_LOVELACE = 20_000_000
COLLATERAL_LOVELACE = 5_000_000
REJECT_CLASSES = {"phase2_reject", "ledger_reject", "inconclusive_reject", "decode_reject"}
PROPAGATION_MARGIN_SECONDS = 5.0


def seconds_until_target_eligible(gate: Mapping[str, Any] | None, *, now: float) -> float:
    """Time left before the reference accepts transactions relayed from the target.

    A transaction Amaru admits before cardano-node's tx-submission responder is active is never
    re-offered, so it cannot be included and the control would be falsely incomplete.
    """
    eligible = (gate or {}).get("eligible_at_epoch_seconds")
    if not isinstance(eligible, (int, float)):
        return 0.0
    return max(0.0, float(eligible) + PROPAGATION_MARGIN_SECONDS - now)


def case_budget(protocol_parameters: Mapping[str, Any]) -> tuple[int, int]:
    """Half of the live per-transaction execution limit: generous, never over the limit."""
    limits = protocol_parameters.get("maxTxExecutionUnits")
    if not isinstance(limits, Mapping):
        raise ValueError("live protocol parameters omit maxTxExecutionUnits")
    steps, memory = int(limits.get("steps") or 0), int(limits.get("memory") or 0)
    if steps <= 0 or memory <= 0:
        raise ValueError("live maxTxExecutionUnits must be positive")
    return steps // 2, memory // 2


def _mint_budget(witnesses: Mapping[Any, Any]) -> tuple[int, int]:
    redeemers = witnesses.get(5)
    entry = redeemers.get((1, 0)) if isinstance(redeemers, dict) else None
    if not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[1], list) or len(entry[1]) != 2:
        raise ValueError("case transaction requires one mint redeemer with an execution budget")
    memory, steps = entry[1]
    return int(steps), int(memory)


def build_case_transaction(
    *,
    template_payload: bytes,
    address: bytes,
    input_lovelace: int,
    program: bytes,
    language: str,
    protocol_parameters: Mapping[str, Any],
    budget: tuple[int, int],
    sign_body: Callable[[bytes], bytes],
) -> dict[str, Any]:
    """Finalise a fee-correct signed minting transaction carrying exactly ``program``."""
    if language not in WITNESS_KEYS:
        raise ValueError(f"unsupported Plutus language {language!r}")
    transaction = cbor2.loads(template_payload)
    if not isinstance(transaction, list) or len(transaction) != 4:
        raise ValueError("transaction template must be a four-element transaction")
    body, witnesses = transaction[0], transaction[1]
    outputs = body.get(1) if isinstance(body, dict) else None
    scripts = witnesses.get(WITNESS_KEYS[language]) if isinstance(witnesses, dict) else None
    if not isinstance(outputs, list) or len(outputs) != 1:
        raise ValueError("case transaction requires exactly one output")
    if not isinstance(scripts, (list, set, tuple)) or len(scripts) != 1:
        raise ValueError(f"case transaction requires exactly one {language} script")
    witness = cbor2.dumps(program)
    if next(iter(scripts)) != witness:
        raise ValueError(f"{language} witness does not contain the expected program")
    if (cost_models := protocol_parameters.get("costModels")) is None or not isinstance(cost_models.get(language), list):
        raise ValueError(f"live protocol parameters do not contain a {language} cost model")
    if _mint_budget(witnesses) != tuple(budget):
        raise ValueError("case transaction budget does not match the requested budget")

    max_tx_size = _positive_parameter(protocol_parameters, "maxTxSize")
    fee_per_byte = _positive_parameter(protocol_parameters, "txFeePerByte")
    fee_fixed = _positive_parameter(protocol_parameters, "txFeeFixed")
    coins_per_utxo_byte = _positive_parameter(protocol_parameters, "utxoCostPerByte")
    prices = protocol_parameters.get("executionUnitPrices")
    if not isinstance(prices, Mapping):
        raise ValueError("live protocol parameters do not contain execution unit prices")
    steps, memory = budget
    execution_fee = int((_positive_decimal_parameter(prices, "priceSteps") * steps
                         + _positive_decimal_parameter(prices, "priceMemory") * memory).to_integral_value(rounding=ROUND_CEILING))
    fee, payload, body_raw, final_output = 1, b"", b"", outputs[0]
    for _ in range(12):
        output_coin = input_lovelace - fee
        if output_coin <= 0:
            raise ValueError("funding input cannot satisfy the transaction fee")
        final_output = _output_with_coin(outputs[0], address, output_coin)
        final_body = dict(body)
        final_body[1] = [final_output]
        final_body[2] = fee
        body_raw = cbor2.dumps(final_body)
        payload = sign_body(body_raw)
        minimum = fee_fixed + fee_per_byte * _ledger_fee_transaction_size(payload) + execution_fee
        if minimum == fee:
            break
        fee = minimum
    else:
        raise RuntimeError("minimum-fee calculation did not converge")
    minimum_coin = (160 + len(cbor2.dumps(final_output))) * coins_per_utxo_byte
    if input_lovelace - fee < minimum_coin:
        raise ValueError("case output is below minimum ADA")
    if len(payload) > max_tx_size:
        raise ValueError("case transaction exceeds maxTxSize")
    stream = io.BytesIO(payload)
    cbor2.CBORDecoder(stream).decode()
    if stream.tell() != len(payload):
        raise ValueError("case transaction has trailing outer bytes")
    return {
        "language": language,
        "program_size": len(program),
        "program_sha256": hashlib.sha256(program).hexdigest(),
        "script_witness_sha256": hashlib.sha256(witness).hexdigest(),
        "body": body_raw,
        "body_sha256": hashlib.sha256(body_raw).hexdigest(),
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "transaction_size": len(payload),
        "fee": fee,
        "execution_fee": execution_fee,
        "execution_units": {"steps": steps, "memory": memory},
        "output_coin": input_lovelace - fee,
        "minimum_coin": minimum_coin,
    }


def _outcome(classification: str | None) -> str:
    if classification == "accepted":
        return "accept"
    if classification in REJECT_CLASSES:
        return "reject"
    return "inconclusive"


def assess_case_outcomes(families: list[dict[str, Any]]) -> dict[str, Any]:
    """Agreement between the nodes and with the specification, per case, with named exceptions."""
    disagreements, mismatches, inconclusive, reason_differences, controls_ok = [], [], [], [], True
    count = 0
    for family in families:
        control = family.get("control") or {}
        for role in ("target", "reference"):
            entry = control.get(role) or {}
            if not (entry.get("classification") == "accepted" and entry.get("included") and entry.get("target_adopted")):
                controls_ok = False
        for record in family.get("cases") or []:
            count += 1
            target_class = (record.get("target") or {}).get("classification")
            reference_class = (record.get("reference") or {}).get("classification")
            target, reference = _outcome(target_class), _outcome(reference_class)
            key = {"family": family.get("family"), "case_id": record.get("case_id")}
            if "inconclusive" in (target, reference):
                inconclusive.append(key)
                continue
            if target != reference:
                disagreements.append({**key, "target": target_class, "reference": reference_class})
                continue
            if target != record.get("expected"):
                mismatches.append({**key, "expected": record.get("expected"), "observed": target})
            if target == "reject" and target_class != reference_class:
                reason_differences.append({**key, "target": target_class, "reference": reference_class})
    complete = controls_ok and not inconclusive and count > 0
    return {
        "complete": complete,
        "controls_admitted_included_adopted": controls_ok,
        "case_count": count,
        "agreement": complete and not disagreements,
        "specification_match": complete and not disagreements and not mismatches,
        "disagreements": disagreements,
        "specification_mismatches": mismatches,
        "inconclusive": inconclusive,
        "reject_reason_differences": reason_differences,
    }


def _script_file(path: Path, program: bytes, *, language: str, description: str) -> None:
    path.write_text(json.dumps({"type": SCRIPT_FILE_TYPES[language], "description": description,
                                "cborHex": cbor2.dumps(program).hex()}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_plutus_case_differential(
    *, runtime: dict[str, Any], output_dir: Path, target_node: str, reference_node: str,
    expected_target_version: str, expected_target_source: str,
    expected_reference_version: str, expected_reference_source: str,
    family_names: list[str],
) -> dict[str, Any]:
    """Fund, build and submit every case of the selected families to both nodes."""
    from profile_manager.submit_primitives import _validation_stage
    from scripts.runtime_cardano_measurement_calibration import (
        _checked, _containerized_cardano_workload, _controller_container, _inspect_container,
        _query_utxo, _transaction_id,
    )
    from scripts.runtime_governance_zero_denominator import _terminal_inclusion_tip, resign_transaction_body
    from scripts.runtime_redeemer_container_live_controls import (
        _envelope, _query_reference_tip, _reference_tx_submission_propagation_gate,
        _select_funding_input, _submit_amaru_container, _submit_reference_raw,
        _wait_for_reference_inclusion, _wait_for_target_adoption, _write_envelope,
        readable_reference_response, split_transaction, validate_runtime_identity,
    )

    unknown = [name for name in family_names if name not in plutus_cases.FAMILY_LANGUAGES]
    if unknown or not family_names:
        raise ValueError(f"unknown or empty Plutus case families: {unknown or family_names}")
    identities = validate_runtime_identity(
        runtime, target_node=target_node, reference_node=reference_node,
        expected_target_version=expected_target_version, expected_target_source=expected_target_source,
        expected_reference_version=expected_reference_version, expected_reference_source=expected_reference_source,
    )
    controller = _controller_container()
    if controller is None:
        raise RuntimeError("the Plutus case differential requires the installed Docker controller")
    runtime_root = Path(str(runtime.get("runtime_root") or ""))
    if not runtime_root.is_absolute():
        raise RuntimeError("runtime root is not absolute")
    output_dir.mkdir(parents=True, exist_ok=True)
    target, reference = identities["target"], identities["reference"]
    network_magic = int(runtime["network_magic"])
    socket_path = str(reference.get("container_socket_path") or "")
    helper_image = str(os.environ.get("DWARF_LOCALTXSUBMISSION_HELPER_IMAGE") or "")
    propagation_gate = _reference_tx_submission_propagation_gate(reference, inspect_container=_inspect_container)

    with _containerized_cardano_workload(
        runtime_root=runtime_root, runtime=runtime, node=reference, output_dir=output_dir, controller=controller,
    ) as (workload_runtime, _workload_node):
        cli = str(workload_runtime["support_binaries"]["cardano-cli"])
        key_root = Path(str(workload_runtime["workload_key_root"]))
        controller_key_root = Path(str(workload_runtime["controller_workload_key_root"]))
        address = _checked([cli, "latest", "genesis", "initial-addr", "--verification-key-file",
                            str(key_root / "utxo.vkey"), "--testnet-magic", str(network_magic)]).stdout.strip()
        if (runtime.get("generated_workload_funding") or {}).get("address") != address:
            raise RuntimeError("Plutus case funding address does not match the generated substrate")
        pparams_path = output_dir / "protocol-parameters.json"
        _checked([cli, "query", "protocol-parameters", "--socket-path", socket_path,
                  "--testnet-magic", str(network_magic), "--out-file", str(pparams_path)])
        from decimal import Decimal
        pparams = json.loads(pparams_path.read_text(encoding="utf-8"), parse_float=Decimal)
        budget = case_budget(pparams)
        cost_models = pparams.get("costModels") or {}
        chosen = {name: plutus_cases.select_language(name, cost_models) for name in family_names}
        unsupported = [name for name, language in chosen.items() if language is None]
        if unsupported:
            raise RuntimeError(f"live protocol parameters have no cost model for any language of {unsupported} "
                               f"(chain offers {sorted(cost_models)})")
        selected = [plutus_cases.family(name, chosen[name]) for name in family_names]
        languages = sorted(set(chosen.values()))
        # two controls plus one transaction per case and node
        spend_count = sum(2 + 2 * len(family.cases) for family in selected)

        funding_before = _query_utxo(cli=cli, socket_path=socket_path, network_magic=network_magic,
                                     address=address, destination=output_dir / "funding-before.json")
        needed = spend_count * CASE_INPUT_LOVELACE + 3 * COLLATERAL_LOVELACE + 10_000_000
        fresh_input, _ = _select_funding_input(funding_before, minimum_lovelace=needed)
        setup_body = output_dir / "funding-setup.body"
        command = [cli, "conway", "transaction", "build", "--socket-path", socket_path, "--testnet-magic",
                   str(network_magic), "--tx-in", fresh_input, "--change-address", address]
        for _ in range(spend_count):
            command.extend(["--tx-out", f"{address}+{CASE_INPUT_LOVELACE}"])
        for _ in range(3):
            command.extend(["--tx-out", f"{address}+{COLLATERAL_LOVELACE}"])
        command.extend(["--out-file", str(setup_body)])
        _checked(command)
        setup_tx = output_dir / "funding-setup.tx"
        _checked([cli, "conway", "transaction", "sign", "--tx-body-file", str(setup_body), "--signing-key-file",
                  str(key_root / "utxo.skey"), "--testnet-magic", str(network_magic), "--out-file", str(setup_tx)])
        setup_id = _transaction_id(cli, setup_tx)
        tip_before = _query_reference_tip(cli=cli, socket_path=socket_path, network_magic=network_magic)
        _checked([cli, "conway", "transaction", "submit", "--tx-file", str(setup_tx), "--socket-path", socket_path,
                  "--testnet-magic", str(network_magic)])
        funding_ready = _wait_for_reference_inclusion(
            cli=cli, socket_path=socket_path, network_magic=network_magic, address=address,
            destination=output_dir / "funding-ready.json", transaction_id=setup_id, initial_tip=tip_before,
            evidence_path=output_dir / "funding-inclusion.json",
        )
        spends = [f"{setup_id}#{index}" for index in range(spend_count)]
        collaterals = [f"{setup_id}#{index}" for index in range(spend_count, spend_count + 3)]
        for txin in spends:
            if ((funding_ready.get(txin) or {}).get("value") or {}).get("lovelace") != CASE_INPUT_LOVELACE:
                raise RuntimeError("Plutus case funding output does not match")
        _wait_for_target_adoption(
            log_path=Path(str(target.get("log_path") or "")),
            expected_tip=_terminal_inclusion_tip(output_dir / "funding-inclusion.json", label="Plutus case funding"),
            evidence_path=output_dir / "funding-target-adoption.json",
        )
        unit_path = output_dir / "unit.json"
        unit_path.write_text(json.dumps({"constructor": 0, "fields": []}) + "\n", encoding="utf-8")
        next_spend = iter(spends)

        def build(case: "plutus_cases.Case", case_dir: Path) -> dict[str, Any]:
            case_dir.mkdir(parents=True, exist_ok=True)
            program = case.program()
            script_path = case_dir / "policy.plutus"
            _script_file(script_path, program, language=case.language, description=f"{case.case_id}: {case.description}")
            policy_id = _checked([cli, "conway", "transaction", "policyid", "--script-file", str(script_path)]).stdout.strip()
            if len(policy_id) != 56:
                raise RuntimeError("case policy id is invalid")
            funding_input = next(next_spend)
            token = f"1 {policy_id}.43415345"
            raw_path = case_dir / "template.raw"
            _checked([cli, "conway", "transaction", "build-raw", "--tx-in", funding_input,
                      "--tx-out", f"{address}+{CASE_INPUT_LOVELACE - 1_000_000}+{token}", "--fee", "1000000",
                      "--mint", token, "--mint-script-file", str(script_path), "--mint-redeemer-file", str(unit_path),
                      "--mint-execution-units", f"({budget[0]},{budget[1]})", "--protocol-params-file", str(pparams_path),
                      "--tx-in-collateral", collaterals[0], "--out-file", str(raw_path)])
            signed_template = case_dir / "template.signed"
            _checked([cli, "conway", "transaction", "sign", "--tx-body-file", str(raw_path), "--signing-key-file",
                      str(key_root / "utxo.skey"), "--testnet-magic", str(network_magic), "--out-file", str(signed_template)])
            envelope, template_payload = _envelope(signed_template)
            template_body, _w, _t = split_transaction(template_payload)
            output_address = cbor2.loads(template_body)[1][0][0]
            built = build_case_transaction(
                template_payload=template_payload, address=output_address, input_lovelace=CASE_INPUT_LOVELACE,
                program=program, language=case.language, protocol_parameters=pparams, budget=budget,
                sign_body=lambda body: resign_transaction_body(template_payload, body, [controller_key_root / "utxo.skey"]),
            )
            _write_envelope(case_dir / "transaction.signed", envelope, built["payload"])
            built["transaction_id"] = hashlib.blake2b(built["body"], digest_size=32).hexdigest()
            built["funding_input"] = funding_input
            built["policy_id"] = policy_id
            return built

        def submit(role: str, built: dict[str, Any], case_dir: Path, *, wait_inclusion: bool) -> dict[str, Any]:
            if role == "target":
                wait = seconds_until_target_eligible(propagation_gate, now=time.time())
                if wait:
                    time.sleep(wait)
            pre_tip = _query_reference_tip(cli=cli, socket_path=socket_path, network_magic=network_magic)
            if role == "target":
                response = _submit_amaru_container(str(target["container_id"]), built["payload"])
                classification = _validation_stage(response)
            else:
                # an unreadable node rejection is read with the reference image's own cli (F102)
                response = readable_reference_response(
                    _submit_reference_raw(runtime=runtime, reference=reference, payload=built["payload"],
                                          helper_image=helper_image),
                    payload=built["payload"], cli=cli, socket_path=socket_path, network_magic=network_magic,
                    envelope_from=setup_tx, work_dir=case_dir / "cli-reject-reading")
                classification = response["classification"]
            included = adopted = False
            if wait_inclusion and classification == "accepted":
                inclusion_path = case_dir / "inclusion.json"
                try:
                    _wait_for_reference_inclusion(
                        cli=cli, socket_path=socket_path, network_magic=network_magic, address=address,
                        destination=case_dir / "included-utxo.json", transaction_id=built["transaction_id"],
                        initial_tip=pre_tip, evidence_path=inclusion_path,
                        target_propagation_gate=propagation_gate if role == "target" else None,
                    )
                    included = True
                    _wait_for_target_adoption(
                        log_path=Path(str(target.get("log_path") or "")),
                        expected_tip=_terminal_inclusion_tip(inclusion_path, label=f"Plutus case {role}"),
                        evidence_path=case_dir / "target-adoption.json",
                    )
                    adopted = True
                except RuntimeError:
                    pass
            record = {**response, "classification": classification, "included": included, "target_adopted": adopted,
                      "transaction_id": built["transaction_id"], "payload_sha256": built["payload_sha256"],
                      "funding_input": built["funding_input"]}
            (case_dir / "result.json").write_text(json.dumps(record, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
            return record

        family_records = []
        for family in selected:
            family_dir = output_dir / family.name
            control: dict[str, Any] = {"case_id": family.control.case_id, "program_sha256": family.control.program_sha256()}
            for role in ("target", "reference"):
                control_dir = family_dir / family.control.case_id / role
                control[role] = submit(role, build(family.control, control_dir), control_dir, wait_inclusion=True)
            control_ok = all(control[r]["classification"] == "accepted" and control[r]["included"] and control[r]["target_adopted"]
                             for r in ("target", "reference"))
            records = []
            if control_ok:
                for case in family.cases:
                    record: dict[str, Any] = {"case_id": case.case_id, "expected": case.expected, "language": case.language,
                                              "description": case.description, "program_sha256": case.program_sha256()}
                    for role in ("target", "reference"):
                        case_dir = family_dir / case.case_id / role
                        built = build(case, case_dir)
                        record[role] = submit(role, built, case_dir, wait_inclusion=False)
                        record.setdefault("program_size", built["program_size"])
                    records.append(record)
            family_records.append({"family": family.name, "language": family.language, "control": control, "cases": records})

        status = assess_case_outcomes(family_records)
        report = {
            "schema_version": 1,
            "case_family": "plutus_case_differential",
            "families": family_names,
            "runtime_identity": identities,
            "protocol": {"case_budget": {"steps": budget[0], "memory": budget[1]}, "languages": languages,
                         "family_languages": chosen, "live_cost_model_languages": sorted(cost_models)},
            "records": family_records,
            "differential_status": status,
            "claim_limits": [
                "Admission is the outcome: a node evaluates the script during mempool admission.",
                "Only controls are required to be included and adopted; case transactions are not waited on.",
                "Expected outcomes come from the Plutus Core specification for the case's language.",
            ],
        }
        (output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        return report
