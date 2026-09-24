#!/usr/bin/env python3
"""Deploy a retained DWARF Amaru/mixed profile from the proven live control.

The Cardano producer cluster stays alive while the bootstrap producer snapshots
its coherent ChainDB.  Amaru starts from that bundle and peers directly with
the same producers.  A deployment is retained only after every runtime and
identity gate passes; partial or failed projects are removed with their volumes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DWARF_ROOT = SCRIPT_DIR.parent
for import_root in (DWARF_ROOT, SCRIPT_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from check_cardano_amaru_topology import collect_and_classify  # noqa: E402
from qualify_node_versions import (  # noqa: E402
    AMARU_RELAYS,
    BASE_PACKAGE,
    CARDANO_SERVICES,
    PROTECTED_PROJECTS,
    _amaru_log_text,
    _compose,
    _identity_observation,
    _project_containers,
    _project_is_fresh,
    _render_baseline,
    _run,
    cardano_experimental_protocols_policy,
    classify_log_signals,
    classify_terminal_runtime_failure,
    transform_compose_model,
)
from redeploy_cardano_amaru_topology import (  # noqa: E402
    fresh_readiness_proven,
    remember_bootstrap_evidence,
)


LIFECYCLE = "cardano_amaru_relay_bootstrap_control"
REQUIRED_GATES = {
    "fresh_state",
    "exact_identity",
    "required_services",
    "chain_progress",
    "peer_formation",
    "consumer_amaru_only_path",
    "consumer_converged",
    "no_fatal_signatures",
    "no_restart_loop",
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class RuntimeControlError(RuntimeError):
    """The retained live-control deployment cannot safely continue."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def validate_runtime_request(config: dict[str, Any]) -> None:
    project = str(config.get("compose_project") or "")
    runtime_root = Path(str(config.get("runtime_root") or ""))
    if config.get("lifecycle") != LIFECYCLE:
        raise RuntimeControlError(f"unsupported lifecycle; expected {LIFECYCLE}")
    if config.get("scope") not in {"amaru-only", "mixed"}:
        raise RuntimeControlError("live Amaru control scope must be amaru-only or mixed")
    if project in PROTECTED_PROJECTS:
        raise RuntimeControlError(f"compose project is protected: {project}")
    if not project.startswith("dwarf-profile-"):
        raise RuntimeControlError("retained project must use the dwarf-profile- prefix")
    if not str(config.get("profile_id") or "").startswith("profile-"):
        raise RuntimeControlError("profile_id must identify a DWARF profile")
    if not runtime_root.is_absolute() or runtime_root == Path("/"):
        raise RuntimeControlError("runtime_root must be a specific absolute path")
    for key in ("cardano_image", "amaru_image"):
        if "@sha256:" not in str(config.get(key) or ""):
            raise RuntimeControlError(f"{key} must be pinned by digest")
    for key in ("supporting_cardano_version", "amaru_version"):
        if not str(config.get(key) or "").strip():
            raise RuntimeControlError(f"{key} is required")
    mode = str(config.get("measurement_target_mode") or "stock")
    if mode not in {"stock", "patched"}:
        raise RuntimeControlError("measurement_target_mode must be stock or patched")
    if mode == "patched":
        if config.get("amaru_runtime_interface") != "extracted-binary":
            raise RuntimeControlError(
                "patched target requires the extracted-binary Amaru runtime interface"
            )
        identity = config.get("measurement_target_identity")
        if not isinstance(identity, dict) or not identity:
            raise RuntimeControlError(
                "patched target requires measurement_target_identity"
            )
        if identity.get("target_mode") != "patched":
            raise RuntimeControlError("measurement_target_identity mode is not patched")
        if identity.get("image") != config.get("amaru_image"):
            raise RuntimeControlError(
                "measurement_target_identity image does not match amaru_image"
            )
        if identity.get("version") != config.get("amaru_version"):
            raise RuntimeControlError(
                "measurement_target_identity version does not match amaru_version"
            )
        if not SHA40.fullmatch(str(identity.get("source_revision") or "")):
            raise RuntimeControlError(
                "measurement_target_identity requires an exact source revision"
            )
        if not SHA64.fullmatch(str(identity.get("patch_set_sha256") or "")):
            raise RuntimeControlError(
                "measurement_target_identity requires an exact patch-set sha256"
            )
        for key in ("image_digest", "executable_digest", "build_result_sha256"):
            if not DIGEST.fullmatch(str(identity.get(key) or "")):
                raise RuntimeControlError(
                    f"measurement_target_identity requires immutable {key}"
                )
    if config.get("plutus_v2_genesis") is True:
        configured_path = str(config.get("plutus_v2_cost_model_path") or "")
        if not configured_path:
            raise RuntimeControlError("PlutusV2 cost model path is required")
        model_path = Path(configured_path)
        if not model_path.is_absolute():
            model_path = DWARF_ROOT / model_path
        if not model_path.is_file():
            raise RuntimeControlError(f"PlutusV2 cost model path does not exist: {model_path}")
        expected = str(config.get("plutus_v2_cost_model_sha256") or "")
        if not SHA64.fullmatch(expected):
            raise RuntimeControlError("PlutusV2 cost model sha256 is required")
        if hashlib.sha256(model_path.read_bytes()).hexdigest() != expected:
            raise RuntimeControlError("PlutusV2 cost model sha256 does not match")
    override = config.get("kes_genesis_override")
    if override is not None:
        if not isinstance(override, dict):
            raise RuntimeControlError("kes_genesis_override must be an object")
        try:
            slots = int(override["slots_per_kes_period"])
            evolutions = int(override["max_kes_evolutions"])
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeControlError(
                "kes_genesis_override needs integer slots_per_kes_period and "
                "max_kes_evolutions"
            ) from error
        if slots < 1 or evolutions < 1:
            raise RuntimeControlError("kes_genesis_override values must be >= 1")


def _plutus_v2_model_path(config: dict[str, Any]) -> Path:
    path = Path(str(config["plutus_v2_cost_model_path"]))
    return path if path.is_absolute() else DWARF_ROOT / path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plutus_v2_model(document: Any, *, source: str) -> list[int]:
    try:
        model = document["costModels"]["PlutusV2"]
    except (KeyError, TypeError):
        raise RuntimeControlError(f"{source} has no PlutusV2 cost model") from None
    if (
        not isinstance(model, list)
        or len(model) != 175
        or any(isinstance(value, bool) or not isinstance(value, int) for value in model)
    ):
        raise RuntimeControlError(f"{source} has an invalid PlutusV2 cost model")
    return model


def verify_plutus_v2_evidence(
    *,
    pinned_model_path: Path,
    expected_sha256: str,
    genesis_paths: list[Path],
    conway_genesis_paths: list[Path],
    live_protocol_parameters_path: Path,
) -> dict[str, Any]:
    if _sha256(pinned_model_path) != expected_sha256:
        raise RuntimeControlError("pinned PlutusV2 cost model sha256 does not match")
    pinned = json.loads(pinned_model_path.read_text(encoding="utf-8"))
    if (
        not isinstance(pinned, list)
        or len(pinned) != 175
        or any(isinstance(value, bool) or not isinstance(value, int) for value in pinned)
    ):
        raise RuntimeControlError("pinned PlutusV2 cost model is not a 175-integer array")
    if len(genesis_paths) != 3:
        raise RuntimeControlError("three generated Alonzo genesis files are required")
    genesis_evidence = []
    for path in genesis_paths:
        body = json.loads(path.read_text(encoding="utf-8"))
        if _plutus_v2_model(body, source=str(path)) != pinned:
            raise RuntimeControlError(f"generated PlutusV2 cost model does not match: {path}")
        genesis_evidence.append(
            {"kind": "alonzo", "path": str(path), "sha256": _sha256(path)}
        )
    if len({item["sha256"] for item in genesis_evidence}) != 1:
        raise RuntimeControlError("generated Alonzo genesis files are not identical")
    if len(conway_genesis_paths) != 3:
        raise RuntimeControlError("three generated Conway genesis files are required")
    conway_evidence = []
    for path in conway_genesis_paths:
        body = json.loads(path.read_text(encoding="utf-8"))
        drep = body.get("dRepVotingThresholds") or {}
        pools = body.get("poolVotingThresholds") or {}
        committee = body.get("committee") or {}
        if (
            drep.get("ppTechnicalGroup") != 0
            or drep.get("ppNetworkGroup") == 0
            or pools.get("ppSecurityGroup") == 0
            or committee.get("members") != {}
            or committee.get("threshold") != 0
            or body.get("committeeMinSize") != 0
            or int(body.get("govActionDeposit") or 0) <= 0
            or (body.get("constitution") or {}).get("script") is not None
        ):
            raise RuntimeControlError(
                f"generated Conway governance settings are not narrowly scoped: {path}"
            )
        conway_evidence.append(
            {"kind": "conway", "path": str(path), "sha256": _sha256(path)}
        )
    if len({item["sha256"] for item in conway_evidence}) != 1:
        raise RuntimeControlError("generated Conway genesis files are not identical")
    live_body = json.loads(live_protocol_parameters_path.read_text(encoding="utf-8"))
    if _plutus_v2_model(live_body, source="live protocol parameters") != pinned:
        raise RuntimeControlError("live PlutusV2 cost model does not match pinned model")
    return {
        "verified": True,
        "pinned_cost_model": {
            "path": str(pinned_model_path),
            "sha256": expected_sha256,
        },
        "cost_model_entry_count": len(pinned),
        "generated_genesis": genesis_evidence + conway_evidence,
        "live_protocol_parameters": {
            "path": str(live_protocol_parameters_path),
            "sha256": _sha256(live_protocol_parameters_path),
            "plutus_v2_cost_model_sha256": hashlib.sha256(
                json.dumps(pinned, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        },
    }


def _shelley_override_doc_line(text: str) -> int:
    """Return the 0-based index of the ``activeSlotsCoeff`` line in the Shelley
    override document of the base testnet.yaml. Fails closed when the anchor is
    absent so a short-KES override never silently lands in the wrong document."""
    for index, line in enumerate(text.splitlines()):
        if line.startswith("activeSlotsCoeff:"):
            return index
    raise RuntimeControlError(
        "base testnet.yaml has no activeSlotsCoeff anchor for the KES override"
    )


def apply_kes_genesis_override(
    model: dict[str, Any], config: dict[str, Any], *, base_package: Path
) -> dict[str, Any] | None:
    """Write a short-KES copy of the base testnet.yaml into the runtime root and
    repoint the configurator bind mount at it. The two keys are injected into
    the Shelley override document (the one merged into shelley-genesis) so the
    configurator issues every pool opcert against the short-KES window."""
    override = config.get("kes_genesis_override")
    if not override:
        return None
    slots = int(override["slots_per_kes_period"])
    evolutions = int(override["max_kes_evolutions"])
    source = Path(base_package) / "testnet.yaml"
    text = source.read_text(encoding="utf-8")
    if "slotsPerKESPeriod:" in text or "maxKESEvolutions:" in text:
        raise RuntimeControlError(
            "base testnet.yaml already pins KES parameters; refusing to override"
        )
    lines = text.splitlines()
    anchor = _shelley_override_doc_line(text)
    lines.insert(anchor + 1, f"slotsPerKESPeriod: {slots}")
    lines.insert(anchor + 2, f"maxKESEvolutions: {evolutions}")
    injected = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    runtime_root = Path(str(config["runtime_root"]))
    runtime_root.mkdir(parents=True, exist_ok=True)
    target = runtime_root / "testnet.yaml"
    target.write_text(injected, encoding="utf-8")
    configurator = model.get("services", {}).get("configurator")
    if not isinstance(configurator, dict):
        raise RuntimeControlError("baseline is missing the configurator service")
    volumes = configurator.get("volumes")
    if not isinstance(volumes, list):
        raise RuntimeControlError("configurator has no volumes to repoint")
    repointed = False
    for volume in volumes:
        if isinstance(volume, dict) and volume.get("target") == "/testnet.yaml":
            volume["source"] = str(target)
            repointed = True
        elif isinstance(volume, str) and volume.split(":")[1:2] == ["/testnet.yaml"]:
            idx = volumes.index(volume)
            suffix = volume.split(":", 2)[2] if volume.count(":") >= 2 else "ro"
            volumes[idx] = f"{target}:/testnet.yaml:{suffix}"
            repointed = True
    if not repointed:
        raise RuntimeControlError("configurator has no /testnet.yaml mount to override")
    return {
        "slots_per_kes_period": slots,
        "max_kes_evolutions": evolutions,
        "window_slots": slots * evolutions,
        "testnet_yaml_path": str(target),
    }



def prepare_runtime_model(
    baseline: dict[str, Any],
    config: dict[str, Any],
    *,
    base_package: Path = BASE_PACKAGE,
) -> dict[str, Any]:
    validate_runtime_request(config)
    model = transform_compose_model(
        baseline,
        scope=str(config["scope"]),
        project=str(config["compose_project"]),
        cardano_image=str(config["cardano_image"]),
        amaru_image=str(config["amaru_image"]),
        allowed_project_prefix="dwarf-profile-",
        amaru_runtime_interface=config.get("amaru_runtime_interface"),
        amaru_json_traces=bool(config.get("amaru_json_traces", False)),
        cardano_experimental_protocols=(
            bool(config["cardano_experimental_protocols"])
            if config.get("cardano_experimental_protocols") is not None
            else cardano_experimental_protocols_policy(
                str(config["scope"]), config.get("supporting_cardano_version")
            )
        ),
    )
    if config.get("plutus_v2_genesis") is True:
        configurator = model["services"]["configurator"]
        volumes = configurator.setdefault("volumes", [])
        if not isinstance(volumes, list):
            raise RuntimeControlError("configurator volumes must be a list")
        model_path = _plutus_v2_model_path(config)
        volumes.append(f"{model_path}:/dwarf/plutus-v2-cost-model.json:ro")
        command = configurator.get("command")
        if not isinstance(command, list) or len(command) != 1:
            raise RuntimeControlError("configurator command must contain one shell program")
        command[0] = command[0].rstrip() + """
for dir in /configs/[123]; do
  genesis="$$dir/configs/alonzo-genesis.json"
  conway_genesis="$$dir/configs/conway-genesis.json"
  jq --slurpfile model /dwarf/plutus-v2-cost-model.json '.costModels.PlutusV2 = $$model[0]' "$$genesis" > "$$genesis.tmp"
  mv "$$genesis.tmp" "$$genesis"
  jq -e '.costModels.PlutusV2 | length == 175' "$$genesis" >/dev/null
  jq '.dRepVotingThresholds.ppTechnicalGroup = 0
      | .committee.members = {}
      | .committee.threshold = 0
      | .committeeMinSize = 0
      | .constitution.script = null' "$$conway_genesis" > "$$conway_genesis.tmp"
  mv "$$conway_genesis.tmp" "$$conway_genesis"
done
mkdir -p /plutus-v2-activation
cp /dwarf/plutus-v2-cost-model.json /plutus-v2-activation/pinned-cost-model.json
cp /tmp/testnet/utxos/keys/genesis.1.vkey /plutus-v2-activation/utxo.vkey
cp /tmp/testnet/utxos/keys/genesis.1.skey /plutus-v2-activation/utxo.skey
cp /tmp/testnet/utxos/keys/stake.1.vkey /plutus-v2-activation/stake.vkey
echo "installed pinned PlutusV2 cost model in every generated Alonzo genesis"
"""
        volumes.append("plutus-v2-activation:/plutus-v2-activation")
        model.setdefault("volumes", {})["plutus-v2-activation"] = {}
        model["volumes"]["plutus-v2-activation-evidence"] = {}
        activation_command = r"""
set -euo pipefail
cleanup() {
  rm -f /activation/utxo.skey
}
trap cleanup EXIT
deadline=$$((SECONDS + 900))
while :; do
  if cardano-cli query tip --socket-path /state/node.socket --testnet-magic 42 > /evidence/tip.json 2>/evidence/query-error.txt; then
    era=$$(jq -r '.era // ""' /evidence/tip.json)
    epoch=$$(jq -r '.epoch // -1' /evidence/tip.json)
    if [ "$$era" = "Conway" ]; then break; fi
    if [ "$$SECONDS" -ge "$$deadline" ]; then
      echo "Conway activation window was not observed" >&2
      exit 41
    fi
  fi
  sleep 2
done
jq '{PlutusV2: .}' /activation/pinned-cost-model.json > /evidence/cost-models.json
cardano-cli query protocol-parameters --socket-path /state/node.socket \
  --testnet-magic 42 > /evidence/protocol-parameters-before.json
gov_deposit=$$(jq -r '.govActionDeposit' /evidence/protocol-parameters-before.json)
anchor_url=https://example.com
anchor_hash=$$(cardano-cli hash anchor-data --url "$$anchor_url")
cardano-cli conway governance action create-protocol-parameters-update \
  --testnet --governance-action-deposit "$$gov_deposit" \
  --deposit-return-stake-verification-key-file /activation/stake.vkey \
  --anchor-url "$$anchor_url" --anchor-data-hash "$$anchor_hash" \
  --cost-model-file /evidence/cost-models.json \
  --out-file /evidence/update.action
address=$$(cardano-cli address build --testnet-magic 42 --payment-verification-key-file /activation/utxo.vkey)
cardano-cli query utxo --address "$$address" --socket-path /state/node.socket \
  --testnet-magic 42 --out-file /evidence/utxo-before.json
tx_in=$$(jq -r 'to_entries | max_by(.value.value.lovelace) | .key' /evidence/utxo-before.json)
cardano-cli conway transaction build --testnet-magic 42 \
  --socket-path /state/node.socket --change-address "$$address" \
  --tx-in "$$tx_in" --proposal-file /evidence/update.action \
  --out-file /evidence/update.body
cardano-cli conway transaction sign --tx-body-file /evidence/update.body \
  --testnet-magic 42 --signing-key-file /activation/utxo.skey \
  --out-file /evidence/update.tx
cardano-cli conway transaction submit --socket-path /state/node.socket \
  --testnet-magic 42 --tx-file /evidence/update.tx > /evidence/submit.json
cardano-cli conway transaction txid --tx-file /evidence/update.tx > /evidence/txid.json
tx_id=$$(jq -r '.txhash' /evidence/txid.json)
while :; do
  cardano-cli query utxo --address "$$address" --socket-path /state/node.socket \
    --testnet-magic 42 --out-file /evidence/utxo-after.json
  if jq -e --arg tx_id "$$tx_id" 'keys | any(startswith($$tx_id + "#"))' /evidence/utxo-after.json >/dev/null; then
    break
  fi
  if [ "$$SECONDS" -ge "$$deadline" ]; then
    echo "PlutusV2 update transaction was not included" >&2
    exit 43
  fi
  sleep 2
done
while :; do
  cardano-cli query protocol-parameters --socket-path /state/node.socket \
    --testnet-magic 42 > /evidence/live-protocol-parameters.json
  if jq -e --slurpfile model /activation/pinned-cost-model.json \
    '.costModels.PlutusV2 == $$model[0]' /evidence/live-protocol-parameters.json >/dev/null; then
    break
  fi
  if [ "$$SECONDS" -ge "$$deadline" ]; then
    echo "PlutusV2 cost model did not become active" >&2
    exit 44
  fi
  sleep 2
done
cardano-cli query tip --socket-path /state/node.socket --testnet-magic 42 > /evidence/active-tip.json
active_epoch=$$(jq -r '.epoch' /evidence/active-tip.json)
active_era=$$(jq -r '.era' /evidence/active-tip.json)
if [ "$$active_era" != "Conway" ]; then
  echo "PlutusV2 became active outside Conway" >&2
  exit 45
fi
jq -n --arg tx_id "$$tx_id" --arg address "$$address" \
  --arg anchor_url "$$anchor_url" --arg anchor_hash "$$anchor_hash" \
  --argjson governance_action_deposit "$$gov_deposit" \
  --arg era "$$active_era" --argjson active_epoch "$$active_epoch" \
  --argjson submitted_epoch "$$epoch" \
  '{verified: true, update_transaction_id: $$tx_id, governance_action_id: ($$tx_id + "#0"), governance_action_deposit: $$governance_action_deposit, payment_address: $$address, anchor_url: $$anchor_url, anchor_hash: $$anchor_hash, submitted_in_epoch: $$submitted_epoch, active_in_epoch: $$active_epoch, active_era: $$era}' \
  > /evidence/result.json
""".strip() + "\n"
        model["services"]["plutus-v2-activate"] = {
            "image": str(config["cardano_image"]),
            "entrypoint": ["/bin/bash", "-lc"],
            "command": [activation_command],
            "read_only": True,
            "tmpfs": ["/tmp"],
            "volumes": [
                "p1-state:/state:ro",
                "plutus-v2-activation:/activation",
                "plutus-v2-activation-evidence:/evidence",
            ],
            "depends_on": {"p1": {"condition": "service_started"}},
            "restart": "no",
        }
        seed = model["services"]["amaru-consumer-seed"]
        seed.setdefault("depends_on", {})["plutus-v2-activate"] = {
            "condition": "service_completed_successfully"
        }
    for service_name, service in model["services"].items():
        labels = service.setdefault("labels", {})
        if not isinstance(labels, dict):
            raise RuntimeControlError(f"{service_name} labels must be a mapping")
        labels.update(
            {
                "ada2.managed": "dwarf",
                "ada2.profile": str(config["profile_id"]),
                "ada2.service": service_name,
            }
        )
    retained_runtime = {
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "fresh_state_required": True,
        "retained_only_after_runtime_gates": True,
    }
    if config.get("plutus_v2_genesis") is True:
        retained_runtime["plutus_v2_genesis"] = True
    kes_override = apply_kes_genesis_override(
        model, config, base_package=base_package
    )
    if kes_override is not None:
        retained_runtime["kes_genesis_override"] = kes_override
    model["x-dwarf-retained-runtime"] = retained_runtime
    return model


def build_runtime_metadata(
    config: dict[str, Any],
    *,
    identity: dict[str, Any],
    observation: dict[str, Any],
    compose_file: str,
    plutus_v2: dict[str, Any] | None = None,
) -> dict[str, Any]:
    substrate = dict(config.get("substrate") or {})
    metadata = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "created_at": _utc_now(),
        "testbed": "local-devnet",
        "target_implementation": config["scope"].replace("-only", ""),
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "compose_project": config["compose_project"],
        "compose_file": compose_file,
        "logical_target_count": int(substrate.get("target_node_count") or 0),
        "declared_support_node_count": int(substrate.get("support_node_count") or 0),
        "actual_topology": {
            "cardano_services": list(CARDANO_SERVICES),
            "amaru_services": list(AMARU_RELAYS),
            "bootstrap_strategy": "per-relay-safe-snapshot-of-live-producer",
            "bootstrap_sources": {
                "amaru-relay-1": "p1",
                "amaru-relay-2": "p2",
            },
            "isolated_consumer": "amaru-consumer",
        },
        "versions": {
            "cardano-node": config["supporting_cardano_version"],
            "amaru": config["amaru_version"],
            "policy": substrate.get("version_policy"),
            "policy_source": substrate.get("version_policy_source"),
            "status": substrate.get("version_status"),
            "deployment_adapter": substrate.get("deployment_adapter"),
            "catalog_revision": substrate.get("catalog_revision"),
            "catalog_snapshot": substrate.get("catalog_snapshot"),
        },
        "images": {
            "cardano-node": config["cardano_image"],
            "amaru": config["amaru_image"],
        },
        "identity": identity,
        "measurement_target": config.get("measurement_target_identity"),
        "readiness": observation,
    }
    if plutus_v2 is not None:
        metadata["plutus_v2"] = plutus_v2
    return metadata


def collect_plutus_v2_evidence(
    config: dict[str, Any], *, project: str, evidence_root: Path
) -> dict[str, Any]:
    containers = _project_containers(project)
    configurator = containers.get("configurator")
    producer = containers.get("p1")
    activator = containers.get("plutus-v2-activate")
    if not configurator or not producer or not activator:
        raise RuntimeControlError(
            "PlutusV2 evidence requires configurator, activation, and p1 containers"
        )
    output_root = evidence_root / "plutus-v2"
    output_root.mkdir(parents=True, exist_ok=False)
    pinned_path = output_root / "pinned-cost-model.json"
    shutil.copy2(_plutus_v2_model_path(config), pinned_path)
    activation_root = output_root / "activation"
    activation_root.mkdir()
    activation_copy = _run(
        ["docker", "cp", f"{activator}:/evidence/.", str(activation_root)],
        timeout=120,
    )
    if activation_copy.returncode != 0:
        raise RuntimeControlError(
            activation_copy.stderr.strip() or "cannot retain PlutusV2 activation evidence"
        )
    activation_result_path = activation_root / "result.json"
    if not activation_result_path.is_file():
        raise RuntimeControlError("PlutusV2 activation result is missing")
    activation_result = json.loads(activation_result_path.read_text(encoding="utf-8"))
    if activation_result.get("verified") is not True:
        raise RuntimeControlError("PlutusV2 activation did not verify")
    genesis_paths = []
    conway_genesis_paths = []
    for index in range(1, 4):
        destination = output_root / f"alonzo-genesis-p{index}.json"
        copied = _run(
            [
                "docker", "cp",
                f"{configurator}:/configs/{index}/configs/alonzo-genesis.json",
                str(destination),
            ],
            timeout=120,
        )
        if copied.returncode != 0:
            raise RuntimeControlError(copied.stderr.strip() or f"cannot retain p{index} Alonzo genesis")
        genesis_paths.append(destination)
        conway_destination = output_root / f"conway-genesis-p{index}.json"
        conway_copied = _run(
            [
                "docker", "cp",
                f"{configurator}:/configs/{index}/configs/conway-genesis.json",
                str(conway_destination),
            ],
            timeout=120,
        )
        if conway_copied.returncode != 0:
            raise RuntimeControlError(
                conway_copied.stderr.strip()
                or f"cannot retain p{index} Conway genesis"
            )
        conway_genesis_paths.append(conway_destination)
    live_path = output_root / "live-protocol-parameters.json"
    queried = _run(
        [
            "docker", "exec", producer, "cardano-cli", "query", "protocol-parameters",
            "--socket-path", "/state/node.socket", "--testnet-magic", "42",
        ],
        timeout=120,
    )
    if queried.returncode != 0:
        raise RuntimeControlError(queried.stderr.strip() or "cannot query live protocol parameters")
    live_path.write_text(queried.stdout, encoding="utf-8")
    evidence = verify_plutus_v2_evidence(
        pinned_model_path=pinned_path,
        expected_sha256=str(config["plutus_v2_cost_model_sha256"]),
        genesis_paths=genesis_paths,
        conway_genesis_paths=conway_genesis_paths,
        live_protocol_parameters_path=live_path,
    )
    evidence["activation"] = {
        **activation_result,
        "artifacts": [
            {
                "path": str(path),
                "sha256": _sha256(path),
            }
            for path in sorted(activation_root.iterdir())
            if path.is_file()
        ],
    }
    return evidence


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _compose_logs(project: str, compose_file: Path) -> str:
    result = _run(
        _compose(project, compose_file, "logs", "--no-color", "--timestamps"),
        timeout=300,
    )
    return result.stdout + ("\n[stderr]\n" + result.stderr if result.stderr else "")


def _gate_result(gates: dict[str, bool]) -> dict[str, Any]:
    required = set(REQUIRED_GATES)
    if "plutus_v2_live_parameters" in gates:
        required.add("plutus_v2_live_parameters")
    failed = sorted(name for name in required if gates.get(name) is not True)
    return {
        "passed": not failed,
        "classification": "retained-runtime-ready" if not failed else "runtime-contract-failed",
        "required_gates": sorted(required),
        "failed_gates": failed,
        "gates": {name: gates.get(name) is True for name in sorted(required)},
    }


def deploy(config: dict[str, Any], *, base_package: Path = BASE_PACKAGE) -> dict[str, Any]:
    validate_runtime_request(config)
    runtime_root = Path(config["runtime_root"])
    project = str(config["compose_project"])
    evidence_root = runtime_root / "evidence"
    compose_file = runtime_root / "docker-compose.json"
    report_file = evidence_root / "deployment-report.json"
    runtime_file = runtime_root / "runtime.json"
    if compose_file.exists() or runtime_file.exists():
        raise RuntimeControlError(f"runtime assets already exist under {runtime_root}")
    if not _project_is_fresh(project):
        raise RuntimeControlError(f"compose project is not fresh: {project}")
    evidence_root.mkdir(parents=True, exist_ok=True)
    model = prepare_runtime_model(
        _render_baseline(base_package), config, base_package=base_package
    )
    _write_json(compose_file, model)

    events: list[dict[str, Any]] = []

    def emit(stage: str, **details: Any) -> None:
        event = {"at": _utc_now(), "stage": stage, **details}
        events.append(event)
        print(json.dumps(event, sort_keys=True), flush=True)

    gates: dict[str, bool] = {"fresh_state": True}
    if config.get("plutus_v2_genesis") is True:
        gates["plutus_v2_live_parameters"] = False
    observation: dict[str, Any] = {}
    identity: dict[str, Any] = {}
    signals: dict[str, list[str]] = {"fatal": [], "background": []}
    terminal_failure: str | None = None
    error: str | None = None
    retain = False
    plutus_v2_evidence: dict[str, Any] | None = None
    emit("prepared", project=project, runtime_root=str(runtime_root))
    try:
        up = _run(
            _compose(project, compose_file, "up", "-d"),
            timeout=max(900, int(config.get("healthy_timeout_seconds") or 1800)),
        )
        if up.returncode != 0:
            raise RuntimeControlError((up.stderr or up.stdout or "compose startup failed").strip())
        emit("started")
        deadline = time.monotonic() + max(
            1, int(config.get("healthy_timeout_seconds") or 1800)
        )
        bootstrap_evidence: dict[str, Any] = {}
        attempt = 0
        while time.monotonic() <= deadline:
            attempt += 1
            observation = collect_and_classify(
                project=project,
                output=evidence_root / f"health-{attempt:03d}.json",
                sample_seconds=float(config.get("sample_seconds") or 10.0),
            )
            bootstrap_evidence = remember_bootstrap_evidence(
                observation, bootstrap_evidence
            )
            ready = fresh_readiness_proven(observation, bootstrap_evidence)
            emit(
                "readiness",
                attempt=attempt,
                ready=ready,
                state=observation.get("state"),
            )
            if ready:
                break
            if observation.get("state") == "unhealthy":
                terminal_failure = classify_terminal_runtime_failure(
                    _amaru_log_text(project)
                )
                if terminal_failure:
                    raise RuntimeControlError(terminal_failure)
            time.sleep(max(1.0, float(config.get("sample_seconds") or 10.0)))

        identity = _identity_observation(
            project,
            scope=str(config["scope"]),
            cardano_version=str(config["supporting_cardano_version"]),
            amaru_version=str(config["amaru_version"]),
            cardano_image=str(config["cardano_image"]),
            amaru_image=str(config["amaru_image"]),
            amaru_runtime_interface=config.get("amaru_runtime_interface"),
            measurement_target_identity=config.get("measurement_target_identity"),
        )
        _write_json(evidence_root / "identity.json", identity)
        logs = _compose_logs(project, compose_file) + "\n" + _amaru_log_text(project)
        (evidence_root / "compose-logs.txt").write_text(logs, encoding="utf-8")
        ps = _run(_compose(project, compose_file, "ps", "--all"), timeout=60)
        (evidence_root / "compose-ps.txt").write_text(
            ps.stdout + ps.stderr, encoding="utf-8"
        )

        healthy = fresh_readiness_proven(observation, bootstrap_evidence)
        observed = observation.get("observation") or {}
        containers = observed.get("containers") or {}
        gates.update(
            {
                "exact_identity": bool(identity.get("matched")),
                "required_services": observation.get("state") == "healthy",
                "chain_progress": healthy,
                "peer_formation": observation.get("state") == "healthy",
                "consumer_amaru_only_path": (
                    (observed.get("peer_contract") or {}).get(
                        "amaru_consumer_only_amaru_upstreams"
                    )
                    is True
                ),
                "consumer_converged": healthy,
                "no_restart_loop": bool(containers)
                and all(
                    int(item.get("restart_count") or 0) == 0
                    for item in containers.values()
                ),
            }
        )
        signals = classify_log_signals(logs)
        gates["no_fatal_signatures"] = not signals["fatal"]
        classification = _gate_result(gates)
        core_failed = [
            name
            for name in classification["failed_gates"]
            if name != "plutus_v2_live_parameters"
        ]
        if core_failed:
            raise RuntimeControlError(
                "runtime gates failed: " + ", ".join(core_failed)
            )
        if config.get("plutus_v2_genesis") is True:
            plutus_v2_evidence = collect_plutus_v2_evidence(
                config, project=project, evidence_root=evidence_root
            )
            gates["plutus_v2_live_parameters"] = True
        retain = True
        metadata = build_runtime_metadata(
            config,
            identity=identity,
            observation=observation,
            compose_file=str(compose_file),
            plutus_v2=plutus_v2_evidence,
        )
        _write_json(runtime_file, metadata)
        emit("retained", project=project, runtime_json=str(runtime_file))
    except Exception as exc:
        error = str(exc)
        emit("failed", error=error)
    finally:
        clean_failure_teardown = None
        if not retain:
            down = _run(
                _compose(project, compose_file, "down", "-v", "--remove-orphans"),
                timeout=600,
            )
            clean_failure_teardown = down.returncode == 0 and _project_is_fresh(project)
            (evidence_root / "teardown.txt").write_text(
                down.stdout + ("\n[stderr]\n" + down.stderr if down.stderr else ""),
                encoding="utf-8",
            )
            emit("failure_teardown", passed=clean_failure_teardown)

    classified = _gate_result(gates)
    result = {
        "schema_version": 1,
        "profile_id": config["profile_id"],
        "scope": config["scope"],
        "lifecycle": LIFECYCLE,
        "project": project,
        "runtime_root": str(runtime_root),
        "completed_at": _utc_now(),
        "retained": retain,
        "error": error,
        "terminal_failure": terminal_failure,
        "identity": identity,
        "observation": observation,
        "log_signals": signals,
        "events": events,
        "clean_failure_teardown": clean_failure_teardown,
        **classified,
    }
    _write_json(report_file, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--base-package", type=Path, default=BASE_PACKAGE)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = deploy(config, base_package=args.base_package)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("passed") and result.get("retained") else 2


if __name__ == "__main__":
    raise SystemExit(main())
