import hashlib
import json
import os
import platform
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from profile_manager.profile_shapes import shape_from_profile_dict
from profile_manager.amaru_runtime import uses_modern_amaru_runtime_interface


# Writable profile catalog. Honors ADA2_DWARF_PROFILES_DIR (seeded from the
# baked profiles at install) so profiles can be created from the dashboard;
# falls back to the baked source tree.
PROFILE_ROOT = Path(os.environ.get("ADA2_DWARF_PROFILES_DIR") or (Path(__file__).resolve().parents[1] / "profiles"))

# Single source of truth for where devnet runtimes live on the deploy host.
# Every profile's remote_runtime_root is normalized to <base>/<id> at load
# time (see Profile.from_dict), so the per-profile value baked in profile.yaml
# is cosmetic — deploy target and remove archival root can never diverge.
# Override with ADA2_DWARF_REMOTE_BASE; default matches the baked convention.
_DEFAULT_REMOTE_BASE = "/opt/dwarf/cardano-profiles"


def remote_base():
    return (os.environ.get("ADA2_DWARF_REMOTE_BASE") or _DEFAULT_REMOTE_BASE).rstrip("/")


def runtime_root(profile_id: str) -> Path:
    """Resolve a retained profile runtime across legacy and controller layouts."""

    profile_root = Path(remote_base()) / profile_id
    current = profile_root / "current"
    if (current / "runtime.json").is_file():
        return current
    return profile_root


# Default locations on the remote build host.
REMOTE_SOURCE_PATH = "/home/dwarf/cardano-node"
REMOTE_DOCKERFILE_PATH = "/home/dwarf/dwarf-fw/devnet-build/cardano-node.Dockerfile"


def _normalize_kes_genesis_override(value):
    """Validate and normalize an optional short-KES genesis override.

    Returns None (leave baseline KES parameters unchanged) or a dict with
    exactly ``slots_per_kes_period`` and ``max_kes_evolutions`` positive ints.
    Fails closed on a malformed override rather than silently deploying a
    devnet whose opcert boundary rules would stay unreachable.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("kes_genesis_override must be an object or null")
    try:
        slots = int(value["slots_per_kes_period"])
        evolutions = int(value["max_kes_evolutions"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "kes_genesis_override requires integer slots_per_kes_period and "
            "max_kes_evolutions"
        ) from error
    if slots < 1 or evolutions < 1:
        raise ValueError("kes_genesis_override values must be >= 1")
    return {"slots_per_kes_period": slots, "max_kes_evolutions": evolutions}


def _normalize_generated_topology(value):
    """Validate the persisted provenance contract for dashboard-generated profiles."""

    if value is None:
        return None
    if isinstance(value, dict) and value.get("schema_version") == "v2":
        required_v2 = {
            "schema_version", "implementation", "network_magic",
            "haskell_count", "amaru_count", "cardano_version", "amaru_version",
        }
        if set(value) != required_v2:
            raise ValueError("generated_topology must be an exact v2 generator contract")
        haskell_count = value.get("haskell_count")
        amaru_count = value.get("amaru_count")
        if any(
            isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 32
            for count in (haskell_count, amaru_count)
        ) or not 1 <= haskell_count + amaru_count <= 32:
            raise ValueError("generated_topology v2 total node count must be from 1 through 32")
        expected = (
            "mixed" if haskell_count and amaru_count
            else "cardano-node" if haskell_count else "amaru"
        )
        if value.get("implementation") != expected:
            raise ValueError("generated_topology v2 implementation does not match node counts")
        network_magic = value.get("network_magic")
        if isinstance(network_magic, bool) or not isinstance(network_magic, int) or not 1 <= network_magic <= 2**32 - 1:
            raise ValueError("generated_topology network_magic must be from 1 through 4294967295")
        if haskell_count and not str(value.get("cardano_version") or "").strip():
            raise ValueError("generated_topology v2 requires cardano_version")
        if amaru_count and not str(value.get("amaru_version") or "").strip():
            raise ValueError("generated_topology v2 requires amaru_version")
        return dict(value)
    required = {
        "schema_version",
        "implementation",
        "network_magic",
        "node_count",
        "version",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("generated_topology must be an exact v1 generator contract")
    if value.get("schema_version") != "v1":
        raise ValueError("generated_topology schema_version must be v1")
    implementation = value.get("implementation")
    if implementation not in {"cardano-node", "amaru"}:
        raise ValueError("generated_topology implementation is unsupported")
    node_count = value.get("node_count")
    if isinstance(node_count, bool) or not isinstance(node_count, int) or not 1 <= node_count <= 32:
        raise ValueError("generated_topology node_count must be from 1 through 32")
    network_magic = value.get("network_magic")
    if (
        isinstance(network_magic, bool)
        or not isinstance(network_magic, int)
        or not 1 <= network_magic <= 2**32 - 1
    ):
        raise ValueError("generated_topology network_magic must be from 1 through 4294967295")
    version = value.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("generated_topology version must be a non-empty string")
    return {
        "schema_version": "v1",
        "implementation": implementation,
        "network_magic": network_magic,
        "node_count": node_count,
        "version": version,
    }


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    node_type: str
    node_count: int
    amaru_node_count: int
    network_magic: int
    peer_sharing: bool
    remote_runtime_root: str
    compose_project: str
    topology_pattern: str | None = None
    shared_genesis: bool = False
    amaru_network: str | None = None
    upstream_peer_address: str | None = None
    listen_address: str | None = None
    config_source_dir: str | None = None
    public_network: str | None = None
    testbed: str | None = None
    version_policy: str = "latest-confirmed"
    version_policy_source: str = "implicit-default"
    cardano_version: str | None = None
    amaru_version: str | None = None
    compatibility_pair: str | None = None
    measurement_target_mode: str = "stock"
    measurement_revision: str | None = None
    measurement_patch_revision: str | None = None
    measurement_patch_set_sha256: str | None = None
    amaru_json_traces: bool = False
    cardano_measurement_traces: bool = False
    plutus_v2_genesis: bool = False
    plutus_v2_cost_model_path: str | None = None
    plutus_v2_cost_model_sha256: str | None = None
    cardano_experimental_protocols: bool | None = None
    kes_genesis_override: dict | None = None
    generated_topology: dict | None = None

    @classmethod
    def from_dict(cls, data):
        shape = shape_from_profile_dict(data)
        declared_version_policy = str(data.get("version_policy") or "").strip()
        return cls(
            id=data["id"],
            label=data["label"],
            node_type=shape.node_type,
            node_count=shape.haskell_count,
            amaru_node_count=shape.amaru_count,
            network_magic=int(data["network_magic"]),
            peer_sharing=bool(data["peer_sharing"]),
            # Normalize to a single base so deploy and remove agree on the root.
            remote_runtime_root=f"{remote_base()}/{data['id']}",
            compose_project=shape.compose_project,
            topology_pattern=shape.topology_pattern,
            shared_genesis=shape.shared_genesis,
            amaru_network=data.get("amaru_network"),
            upstream_peer_address=data.get("upstream_peer_address"),
            listen_address=data.get("listen_address"),
            config_source_dir=data.get("config_source_dir"),
            public_network=data.get("public_network"),
            testbed=data.get("testbed"),
            version_policy=declared_version_policy or "latest-confirmed",
            version_policy_source=(
                "explicit" if declared_version_policy else "implicit-default"
            ),
            cardano_version=data.get("cardano_version"),
            amaru_version=data.get("amaru_version"),
            compatibility_pair=data.get("compatibility_pair"),
            measurement_target_mode=str(data.get("measurement_target_mode") or "stock"),
            measurement_revision=data.get("measurement_revision"),
            measurement_patch_revision=data.get("measurement_patch_revision"),
            measurement_patch_set_sha256=data.get("measurement_patch_set_sha256"),
            amaru_json_traces=bool(data.get("amaru_json_traces", False)),
            cardano_measurement_traces=bool(
                data.get("cardano_measurement_traces", False)
            ),
            plutus_v2_genesis=bool(data.get("plutus_v2_genesis", False)),
            plutus_v2_cost_model_path=data.get("plutus_v2_cost_model_path"),
            plutus_v2_cost_model_sha256=data.get("plutus_v2_cost_model_sha256"),
            cardano_experimental_protocols=(
                None
                if data.get("cardano_experimental_protocols") is None
                else bool(data.get("cardano_experimental_protocols"))
            ),
            kes_genesis_override=_normalize_kes_genesis_override(
                data.get("kes_genesis_override")
            ),
            generated_topology=_normalize_generated_topology(
                data.get("generated_topology")
            ),
        )


def load_profiles():
    profiles = []
    for path in sorted(PROFILE_ROOT.glob("*/profile.yaml")):
        profiles.append(Profile.from_dict(json.loads(path.read_text(encoding="utf-8"))))
    return profiles


def find_profile(profile_id):
    for profile in load_profiles():
        if profile.id == profile_id:
            return profile
    raise KeyError(f"Unknown profile: {profile_id}")


def operator_preference_scope(profile: Profile) -> str:
    """Return the profile-scoped key used by the separate operator overlay."""
    scope = _profile_deploy_mode(profile)
    return f"{'cardano-only' if scope == 'haskell-only' else scope}:{profile.id}"


def persist_topology_configuration(configuration, *, create=True):
    """Atomically retain a validated generated or exact topology as a profile.

    Replay deliberately persists only the declarative profile.  Resolved
    images, controller ownership, runtime paths, commands, and observations
    are always re-derived by the installed catalog and deployment adapter.
    """

    from profile_manager.data.catalog_definitions import (
        CatalogError,
        save_definition,
        serialize_definition,
    )
    from profile_manager.topology_export import replay_profile_from_snapshot

    try:
        profile_data = replay_profile_from_snapshot(
            configuration,
            attestation_secret=os.environ.get("DWARF_CONTROLLER_TOKEN"),
        )
        if (
            configuration.get("artifact_kind") == "exact_deployed_snapshot"
            and configuration.get("generator") is None
        ):
            # A catalog-backed exact snapshot has already been matched against
            # this installed profile by replay_profile_from_snapshot.  Opening
            # that trusted profile must never overwrite its catalog source.
            return find_profile(str(profile_data["id"]))
        record = save_definition(
            "profiles",
            str(profile_data["id"]),
            serialize_definition(profile_data),
            create=create,
        )
        return Profile.from_dict(record.data)
    except CatalogError as exc:
        raise ValueError(str(exc)) from exc


def profile_list_text():
    lines = ["Available profiles:"]
    for profile in load_profiles():
        setting = "PeerSharingEnabled" if profile.peer_sharing else "PeerSharingDisabled"
        if profile.amaru_node_count:
            count_text = f"haskell={profile.node_count} amaru={profile.amaru_node_count}"
        else:
            count_text = f"nodes={profile.node_count}"
        lines.append(f"- {profile.id}: {profile.label} ({setting}, {count_text})")
    return "\n".join(lines) + "\n"


def profile_diff_text(left_id, right_id):
    left = find_profile(left_id)
    right = find_profile(right_id)
    fields = (
        "label",
        "node_type",
        "node_count",
        "amaru_node_count",
        "network_magic",
        "peer_sharing",
        "remote_runtime_root",
        "compose_project",
        "topology_pattern",
        "shared_genesis",
        "amaru_network",
        "upstream_peer_address",
        "listen_address",
        "config_source_dir",
        "public_network",
        "testbed",
        "version_policy",
        "version_policy_source",
        "cardano_version",
        "amaru_version",
        "compatibility_pair",
        "measurement_target_mode",
        "measurement_revision",
        "measurement_patch_revision",
        "measurement_patch_set_sha256",
        "amaru_json_traces",
        "cardano_measurement_traces",
        "plutus_v2_genesis",
        "plutus_v2_cost_model_path",
        "plutus_v2_cost_model_sha256",
        "cardano_experimental_protocols",
        "kes_genesis_override",
    )
    lines = [
        "Profile diff",
        f"Left: {left.id}",
        f"Right: {right.id}",
        "",
        "| Field | Left | Right | Status |",
        "|---|---|---|---|",
    ]
    for field in fields:
        left_value = getattr(left, field)
        right_value = getattr(right, field)
        status = "same" if left_value == right_value else f"{left_value} -> {right_value}"
        lines.append(f"| {field} | {left_value} | {right_value} | {status} |")
    return "\n".join(lines) + "\n"


def status_command():
    return r"""set -e
echo "HOST=$(hostname)"
date -u
echo "DOCKER_STATUS"
docker info --format 'server_version={{.ServerVersion}} containers={{.Containers}} running={{.ContainersRunning}}' 2>/dev/null || echo "docker unavailable"
echo "DWARF_COMPOSE_PROJECTS"
docker compose ls --format json 2>/dev/null | grep -E 'dwarf-' || true
echo "CARDANO_CONTAINERS"
docker ps --filter 'label=ada2.managed=dwarf' --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
echo "RUNTIME_SIZES"
for d in __REMOTE_BASE__/*/env; do
  [ -e "$d" ] && du -sh "$d" 2>/dev/null || true
done
echo "IMAGES"
docker image ls 'dwarf/cardano-node' --format '{{.Repository}}:{{.Tag}} size={{.Size}}' 2>/dev/null || true
""".replace("__REMOTE_BASE__", remote_base())


def active_profile_command():
    """Emit one ``DWARF_NODE`` line per live devnet node — for BOTH docker-based
    devnets (label ada2.managed=dwarf) and host-tmux devnets (the generated-local
    profiles run cardano-node inside ``dwarf-profile-<id>-node<N>`` tmux sessions,
    which carry no docker label). The dashboard counts these lines to show the
    real substrate state; a non-empty stream also means "a devnet is active" for
    the deploy/remove pre-checks."""
    return r"""docker ps --filter 'label=ada2.managed=dwarf' --format 'DWARF_NODE docker {{.Label "ada2.profile"}} {{.Names}} {{.Status}}' 2>/dev/null || true
tmux ls 2>/dev/null | grep -oE '^dwarf-profile-[^:]+' | sed 's/^/DWARF_NODE tmux /' || true"""


def persisted_deployment_command(runtime_root):
    """Emit a bounded, single-line copy of the retained deployment plan."""
    path = shlex.quote(str(Path(runtime_root) / "versioned-deployment.json"))
    return f"""python3 - {path} <<'PY'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    if path.stat().st_size > 1048576:
        raise ValueError("deployment file exceeds 1048576 bytes")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("deployment file is not an object")
except (OSError, ValueError, TypeError):
    value = None
print("DWARF_PERSISTED_DEPLOYMENT=" + json.dumps(value, sort_keys=True, separators=(",", ":")))
PY"""


def health_with_persisted_deployment_command(health_command, runtime_root):
    """Emit persisted evidence without replacing the health probe exit status."""
    return f"""(
{health_command}
)
dwarf_profile_health_status=$?
{persisted_deployment_command(runtime_root)}
exit "$dwarf_profile_health_status"
"""


def _bool_text(value):
    return "true" if value else "false"


def _profile_deploy_mode(profile):
    if profile.node_count > 0 and profile.amaru_node_count > 0:
        return "mixed"
    if profile.amaru_node_count > 0:
        return "amaru-only"
    return "haskell-only"


def _is_generated_haskell_local_profile(profile):
    return (
        _profile_deploy_mode(profile) == "haskell-only"
        and profile.config_source_dir is None
        and profile.topology_pattern == "local-mesh"
        and profile.node_count > 1
    )


def deployment_adapter_for_profile(profile):
    """Classify lifecycle/topology independently from node-version policy.

    Version resolution supplies immutable artifacts to this adapter; it must
    never decide which network, peer, genesis, or lifecycle the profile uses.
    """

    mode = _profile_deploy_mode(profile)
    if mode == "mixed":
        return "amaru-control"
    if mode == "amaru-only":
        if profile.upstream_peer_address or profile.public_network or profile.amaru_network:
            return "amaru-public-peer"
        return "amaru-control"
    if profile.config_source_dir or profile.upstream_peer_address or profile.public_network:
        return "cardano-public-peer"
    if _is_generated_haskell_local_profile(profile):
        return "generated-cardano-local"
    return "cardano-compose-local"


def _public_network(profile):
    return profile.public_network or profile.amaru_network or "preview"


def _public_testbed(profile):
    return profile.testbed or f"public-{_public_network(profile)}"


def _profile_as_custom_bundle(profile):
    return (
        {
            "id": profile.id,
            "label": profile.label,
            "version": 1,
            "execution_type": "approval-required-runtime",
            "safety_level": "approval-required",
            "requires_approval": True,
            "mutates_runtime": True,
            "touches_public_network": False,
            "profile_id": profile.id,
            "candidate_ids": [],
            "output_dir": f"agent/testing/devnet-profiles/{profile.id}",
            "commands": [],
        },
        {
            "id": profile.id,
            "label": profile.label,
            "network_magic": profile.network_magic,
            "node_counts": {"haskell": profile.node_count, "amaru": profile.amaru_node_count},
            "peer_sharing": profile.peer_sharing,
            "submit_api": {"enabled": False, "bind": "127.0.0.1:8090"},
            "observability": {"logs": True},
            "topology": {
                "mode": "local-only",
                "pattern": profile.topology_pattern,
                "shared_genesis": profile.shared_genesis,
                "public_roots": [],
                "bootstrap_peers": [],
            },
            "runtime_root": profile.remote_runtime_root,
            "compose_project": profile.compose_project,
        },
    )


def _exact_oci_reference(release):
    registration = release.get("operator_registration")
    if isinstance(registration, dict):
        config_id = str(registration.get("config_image_id") or "")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", config_id):
            return config_id
    for artifact in release.get("artifacts", []):
        if artifact.get("kind") != "oci" or artifact.get("availability") != "available":
            continue
        reference = str(artifact.get("reference") or "").strip()
        digest = str(artifact.get("digest") or "").strip()
        if not reference or not digest:
            continue
        if "@sha256:" in reference:
            return reference
        return f"{reference}@{digest}"
    raise ValueError(
        f"{release.get('implementation')} {release.get('version')} has no available immutable OCI artifact"
    )


def _versioned_node(node_id, role, release, *, supporting=False):
    node = {
        "id": node_id,
        "impl": release["implementation"],
        "version": release["version"],
        "role": role,
        "image": _exact_oci_reference(release),
        "source_revision": release["source_revision"],
        "supporting": supporting,
        "target_mode": "stock",
    }
    if isinstance(release.get("operator_registration"), dict):
        node["operator_registration"] = json.loads(
            json.dumps(release["operator_registration"])
        )
        if node["impl"] == "amaru":
            node["amaru_runtime_interface"] = release[
                "operator_registration"
            ].get("runtime_interface")
    return node


_AMARU_CONTROL_CARDANO_NODE_SERVICES = (
    "p1",
    "p2",
    "p3",
    "relay1",
    "relay2",
    "amaru-consumer",
)
_AMARU_CONTROL_AMARU_NODE_SERVICES = ("amaru-relay-1", "amaru-relay-2")
_AMARU_CONTROL_INFRASTRUCTURE_SERVICES = (
    "configurator",
    "tracer",
    "tracer-sidecar",
    "amaru-consumer-seed",
    "sidecar",
    "log-tailer",
)


def amaru_control_topology_shape(substrate):
    """Return the exact fixed relay-bootstrap shape and selected-node mapping."""

    if substrate.get("deployment_adapter") != "amaru-control":
        raise ValueError("topology does not use the Amaru control lifecycle")
    nodes = substrate.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Amaru control lifecycle requires exact selected nodes")
    cardano = [node for node in nodes if node.get("impl") == "cardano-node"]
    amaru = [node for node in nodes if node.get("impl") == "amaru"]
    selected_cardano = [node for node in cardano if node.get("supporting") is not True]
    support_cardano = [node for node in cardano if node.get("supporting") is True]
    if len(selected_cardano) > 2:
        raise ValueError("proven Amaru lifecycle cannot honor selected Cardano node count")
    if not amaru or len(amaru) > 2:
        raise ValueError("proven Amaru lifecycle cannot honor selected Amaru node count")
    scope = substrate.get("scope")
    if scope == "amaru-only" and len(support_cardano) != 1:
        raise ValueError(
            "proven Amaru lifecycle requires exactly one catalog-selected Cardano support node"
        )
    if scope == "mixed" and support_cardano:
        raise ValueError("mixed topology cannot contain implicit support nodes")
    if scope not in {"amaru-only", "mixed"}:
        raise ValueError("Amaru control lifecycle requires Amaru-only or mixed scope")
    if int(substrate.get("target_node_count") or -1) != len(selected_cardano) + len(amaru):
        raise ValueError("selected target-node count differs from the topology")
    if int(substrate.get("support_node_count") or 0) != len(support_cardano):
        raise ValueError("selected support-node count differs from the topology")
    selected_node_services = {
        str(node["id"]): f"p{index}"
        for index, node in enumerate(selected_cardano, start=1)
    }
    if support_cardano:
        selected_node_services[str(support_cardano[0]["id"])] = "p1"
    selected_node_services.update(
        {
            str(node["id"]): f"amaru-relay-{index}"
            for index, node in enumerate(amaru, start=1)
        }
    )
    selected_target_node_services = {
        str(node["id"]): selected_node_services[str(node["id"])]
        for node in [*selected_cardano, *amaru]
    }
    selected_support_node_services = {
        str(node["id"]): selected_node_services[str(node["id"])]
        for node in support_cardano
    }
    node_services = [
        *_AMARU_CONTROL_CARDANO_NODE_SERVICES,
        *_AMARU_CONTROL_AMARU_NODE_SERVICES,
    ]
    infrastructure_services = list(_AMARU_CONTROL_INFRASTRUCTURE_SERVICES)
    conditional_infrastructure_services = []
    if any(
        node.get("impl") == "amaru"
        and uses_modern_amaru_runtime_interface(
            str(node.get("image") or ""),
            runtime_interface=(
                "extracted-binary"
                if node.get("target_mode") == "patched"
                else node.get("amaru_runtime_interface")
            ),
        )
        for node in nodes
    ):
        conditional_infrastructure_services.append("amaru-target-extract")
    if substrate.get("plutus_v2_genesis") is True:
        conditional_infrastructure_services.append("plutus-v2-activate")
    infrastructure_services.extend(conditional_infrastructure_services)
    selected_services = set(selected_node_services.values())
    all_services = [*node_services, *infrastructure_services]
    return {
        "lifecycle": "cardano_amaru_relay_bootstrap_control",
        "selected_node_services": selected_node_services,
        "selected_target_node_services": selected_target_node_services,
        "selected_support_node_services": selected_support_node_services,
        "requested_target_nodes": int(substrate["target_node_count"]),
        "requested_support_nodes": int(substrate["support_node_count"]),
        "physical_node_services": node_services,
        "physical_cardano_node_count": len(_AMARU_CONTROL_CARDANO_NODE_SERVICES),
        "physical_amaru_node_count": len(_AMARU_CONTROL_AMARU_NODE_SERVICES),
        "fixed_additional_node_services": [
            service for service in node_services if service not in selected_services
        ],
        "infrastructure_services": infrastructure_services,
        "conditional_infrastructure_services": conditional_infrastructure_services,
        "network_names": ["default", "amaru-consumer-net"],
        "all_services": all_services,
        "physical_service_count": len(all_services),
    }


def versioned_substrate_for_profile(profile, version_preview):
    """Translate a frozen profile version preview into a real Docker substrate.

    Amaru does not currently forge a standalone fresh devnet.  An
    ``amaru-only`` profile therefore means that every *target* node is Amaru,
    while one explicitly-labelled Cardano producer supplies the honest chain.
    The support node is recorded separately so the topology cannot be
    mistaken for an Amaru-only producer network.
    """

    from profile_manager.version_catalog import resolve_default
    from profile_manager.version_discovery import load_effective_version_catalog

    resolved = dict(version_preview.get("resolved") or {})
    scope = str(version_preview.get("scope") or _profile_deploy_mode(profile))
    deployment_adapter = deployment_adapter_for_profile(profile)
    if deployment_adapter == "amaru-control" and profile.peer_sharing:
        raise ValueError("amaru-control profiles require peer_sharing=false")
    nodes = []
    target_node_count = profile.node_count + profile.amaru_node_count
    support_node_count = 0

    if scope == "amaru-only" and deployment_adapter == "amaru-control":
        support_release = (version_preview.get("supporting") or {}).get("cardano-node")
        if support_release is None:
            support_release = resolve_default(load_effective_version_catalog(), "cardano-only")["release"]
        nodes.append(
            _versioned_node(
                "bootstrap-cardano", "bootstrap-producer", support_release, supporting=True
            )
        )
        support_node_count = 1
    elif scope != "amaru-only":
        cardano_release = resolved.get("cardano-node")
        if cardano_release is None:
            raise ValueError("versioned Cardano or mixed profile did not resolve cardano-node")
        for index in range(1, profile.node_count + 1):
            nodes.append(_versioned_node(f"node{index}", "producer", cardano_release))

    amaru_release = resolved.get("amaru")
    if profile.amaru_node_count:
        if amaru_release is None:
            raise ValueError("versioned Amaru or mixed profile did not resolve Amaru")
        for index in range(1, profile.amaru_node_count + 1):
            nodes.append(_versioned_node(f"amaru{index}", "consumer", amaru_release))

    if profile.measurement_target_mode not in {"stock", "patched"}:
        raise ValueError("measurement_target_mode must be stock or patched")
    if profile.measurement_target_mode == "patched":
        if profile.amaru_node_count:
            implementation = "amaru"
            from profile_manager.measurement_targets import resolve_patched_amaru_target

            target = resolve_patched_amaru_target(profile)
        elif profile.node_count:
            implementation = "cardano-node"
            from profile_manager.measurement_targets import resolve_patched_cardano_target

            target = resolve_patched_cardano_target(profile)
        else:
            raise ValueError("patched measurement target requires at least one target node")
        for node in nodes:
            if node["impl"] != implementation:
                continue
            patched_identity = {
                "image": target.get("runtime_image_reference")
                or target["image_reference"],
                "image_digest": target["image_digest"],
                "executable_digest": target["executable_digest"],
                "target_mode": "patched",
                "patch_set_sha256": target["patch_set_sha256"],
                "build_result_sha256": target["build_result_sha256"],
                "runtime_probe_log_sha256": target["runtime_probe_log_sha256"],
            }
            if target.get("runtime_image_reference"):
                patched_identity.update(
                    {
                        "source_image_reference": target["image_reference"],
                        "image_manifest_digest": target["image_manifest_digest"],
                        "image_config_digest": target["image_config_digest"],
                        "qualification_image_id": target["qualification_image_id"],
                        "transfer_source_installation_id": target[
                            "transfer_source_installation_id"
                        ],
                        "transfer_manifest_sha256": target[
                            "transfer_manifest_sha256"
                        ],
                    }
                )
            if target.get("runtime_probe_image"):
                patched_identity["runtime_probe_image"] = target[
                    "runtime_probe_image"
                ]
            node.update(patched_identity)

    edges = [
        {"from": left["id"], "to": right["id"]}
        for left in nodes
        for right in nodes
        if left["id"] != right["id"]
    ]
    selected_releases = []
    selected_keys = set()
    for release in [
        *resolved.values(),
        *(version_preview.get("supporting") or {}).values(),
    ]:
        if not isinstance(release, dict):
            continue
        key = (release.get("implementation"), release.get("version"))
        if key in selected_keys:
            continue
        selected_keys.add(key)
        selected_releases.append(release)
    full_catalog = version_preview.get("catalog_snapshot") or {}
    selected_catalog_snapshot = {
        "schema_version": 1,
        "catalog_revision": version_preview.get("catalog_revision"),
        "catalog_updated_at": full_catalog.get("updated_at"),
        "selection_policy": version_preview.get("policy"),
        "selection_status": version_preview.get("status"),
        "selected_pair": version_preview.get("pair"),
        "selected_releases": selected_releases,
    }
    for field in ("operator_registration_id", "operator_overlay_revision"):
        value = version_preview.get(field)
        if value is not None:
            selected_catalog_snapshot[field] = value
    substrate = {
        "profile_id": profile.id,
        "compose_mode": "docker",
        "scope": scope,
        "target_node_count": target_node_count,
        "support_node_count": support_node_count,
        "deployment_adapter": deployment_adapter,
        "network": (
            _public_network(profile)
            if deployment_adapter in {"cardano-public-peer", "amaru-public-peer"}
            else f"testnet_{profile.network_magic}"
        ),
        # Public profiles have a catalog-owned magic too.  Preserve it in the
        # retained deployment so desired/persisted reconciliation is exact;
        # the adapter still validates it against the staged network config.
        "network_magic": profile.network_magic,
        "peer_sharing": profile.peer_sharing,
        # Public adapters always fetch their canonical named-network assets in
        # the owned runtime.  A catalog's source-host /opt or /home hint is not
        # portable and must never silently become a different genesis/config
        # input merely because that path happens to exist on one host.
        "config_source_dir": (
            None
            if deployment_adapter in {"cardano-public-peer", "amaru-public-peer"}
            else profile.config_source_dir
        ),
        "upstream_peer_address": profile.upstream_peer_address,
        "listen_address": profile.listen_address,
        "version_policy": version_preview.get("policy"),
        "version_policy_source": version_preview.get("policy_source"),
        "version_status": version_preview.get("status"),
        "unknown_acknowledged": bool(version_preview.get("unknown_acknowledged", False)),
        "catalog_revision": version_preview.get("catalog_revision"),
        "catalog_snapshot": selected_catalog_snapshot,
        "measurement_target_mode": profile.measurement_target_mode,
        "amaru_json_traces": profile.amaru_json_traces,
        "cardano_measurement_traces": profile.cardano_measurement_traces,
        "plutus_v2_genesis": profile.plutus_v2_genesis,
        "plutus_v2_cost_model_path": profile.plutus_v2_cost_model_path,
        "plutus_v2_cost_model_sha256": profile.plutus_v2_cost_model_sha256,
        "cardano_experimental_protocols": profile.cardano_experimental_protocols,
        "kes_genesis_override": profile.kes_genesis_override,
        "nodes": nodes,
        "topology": {"edges": edges},
    }
    for field in ("operator_registration_id", "operator_overlay_revision"):
        value = version_preview.get(field)
        if value is not None:
            substrate[field] = value
    if deployment_adapter == "amaru-control":
        substrate["actual_topology"] = amaru_control_topology_shape(substrate)
    return substrate


def _local_executable_identity_from_substrate(
    substrate, *, require_package_proof=True
):
    """Return one exact controller-packaged local identity, or fail closed."""
    registration_id = substrate.get("operator_registration_id")
    overlay_revision = substrate.get("operator_overlay_revision")
    catalog = substrate.get("catalog_snapshot")
    if registration_id is None and overlay_revision is None:
        return None
    if (
        not isinstance(registration_id, str)
        or not registration_id
        or not isinstance(overlay_revision, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", overlay_revision)
        or not isinstance(catalog, dict)
        or catalog.get("operator_registration_id") != registration_id
        or catalog.get("operator_overlay_revision") != overlay_revision
    ):
        return None
    registrations = []
    releases = catalog.get("selected_releases")
    if not isinstance(releases, list):
        return None
    selected_release = None
    selected_artifact = None
    for release in releases:
        if not isinstance(release, dict):
            return None
        registration = release.get("operator_registration")
        if not isinstance(registration, dict):
            continue
        artifacts = release.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        matching = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
            and artifact.get("operator_registration") == registration
        ]
        if len(matching) != 1:
            return None
        registrations.append(registration)
        selected_release = release
        selected_artifact = matching[0]
    if len(registrations) != 1:
        return None
    registration = registrations[0]
    try:
        from profile_manager.deployment_versions import (
            DeploymentVersionGateError,
            operator_registration_immutable_identity,
        )

        identity = operator_registration_immutable_identity(registration)
    except (DeploymentVersionGateError, KeyError, TypeError, ValueError):
        return None
    package_proof = selected_release.get("operator_package_proof")
    expected_package_proof = {
        "status": "verified",
        "registration_id": identity["registration_id"],
        "config_image_id": identity["config_image_id"],
        "executable_digest": identity["executable_digest"],
        "platform": identity["platform"],
        "runtime_interface": identity["runtime_interface"],
        "controller_installation_id": identity["controller_installation_id"],
    }
    package_proof_present = "operator_package_proof" in selected_release
    if (
        (
            (require_package_proof or package_proof_present)
            and package_proof != expected_package_proof
        )
        or identity["registration_id"] != registration_id
        or selected_release.get("implementation") != identity["implementation"]
        or selected_release.get("version") != identity["version"]
        or selected_release.get("source_revision") != identity["source_revision"]
        or selected_artifact.get("kind") != "oci"
        or selected_artifact.get("availability") != "available"
        or selected_artifact.get("reference") != identity["config_image_id"]
        or selected_artifact.get("config_image_id") != identity["config_image_id"]
        or selected_artifact.get("digest") is not None
        or selected_artifact.get("executable_digest")
        != identity["executable_digest"]
        or selected_artifact.get("platforms") != [identity["platform"]]
        or selected_artifact.get("runtime_interface")
        != identity["runtime_interface"]
    ):
        return None
    matching_nodes = []
    for node in substrate.get("nodes") or []:
        if not isinstance(node, dict):
            return None
        node_registration = node.get("operator_registration")
        if node_registration is None:
            continue
        if node_registration != registration:
            return None
        matching_nodes.append(node)
    if not matching_nodes or any(
        node.get("impl") != identity["implementation"]
        or node.get("version") != identity["version"]
        or node.get("image") != identity["config_image_id"]
        or node.get("source_revision") != identity["source_revision"]
        or (
            identity["implementation"] == "amaru"
            and node.get("amaru_runtime_interface")
            != identity["runtime_interface"]
        )
        for node in matching_nodes
    ):
        return None
    return {**identity, "operator_overlay_revision": overlay_revision}


def _strip_local_executable_digest(contract, identity):
    """Remove only the proven additive digest from the physical contract."""
    registration_id = identity["registration_id"]
    catalog = contract.get("catalog_snapshot")
    if isinstance(catalog, dict):
        for release in catalog.get("selected_releases") or []:
            if not isinstance(release, dict):
                continue
            registration = release.get("operator_registration")
            if isinstance(registration, dict) and registration.get(
                "registration_id"
            ) == registration_id:
                registration.pop("executable_digest", None)
                release.pop("operator_package_proof", None)
            for artifact in release.get("artifacts") or []:
                if not isinstance(artifact, dict):
                    continue
                artifact_registration = artifact.get("operator_registration")
                if isinstance(artifact_registration, dict) and artifact_registration.get(
                    "registration_id"
                ) == registration_id:
                    artifact_registration.pop("executable_digest", None)
                    # ``operator_registration_release`` has always emitted the
                    # optional artifact field as JSON null.  Preserve that
                    # historical representation while removing only the newly
                    # proven value from physical deployment identity.
                    artifact["executable_digest"] = None
    for node in contract.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        registration = node.get("operator_registration")
        if isinstance(registration, dict) and registration.get(
            "registration_id"
        ) == registration_id:
            registration.pop("executable_digest", None)


def _validated_local_executable_reuse_metadata(value):
    """Validate the compact proof carried by desired-topology identity."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "registration_id",
        "implementation",
        "version",
        "config_image_id",
        "executable_digest",
        "platform",
        "runtime_interface",
        "controller_installation_id",
        "source_revision",
        "qualification",
        "operator_overlay_revision",
    }:
        return None
    if (
        value.get("schema_version") != "v1"
        or not isinstance(value.get("registration_id"), str)
        or not value["registration_id"].startswith("registration-")
        or value.get("implementation") not in {"amaru", "cardano-node"}
        or not isinstance(value.get("version"), str)
        or not value["version"]
        or not isinstance(value.get("config_image_id"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["config_image_id"])
        or not isinstance(value.get("executable_digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["executable_digest"])
        or value.get("platform") not in {"linux/amd64", "linux/arm64"}
        or not isinstance(value.get("controller_installation_id"), str)
        or not value["controller_installation_id"]
        or value.get("qualification") != "unverified"
        or not isinstance(value.get("operator_overlay_revision"), str)
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", value["operator_overlay_revision"]
        )
    ):
        return None
    source_revision = value.get("source_revision")
    if source_revision is not None and (
        not isinstance(source_revision, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_revision)
    ):
        return None
    if (
        value["implementation"] == "amaru"
        and value.get("runtime_interface") != "extracted-binary"
    ) or (
        value["implementation"] == "cardano-node"
        and value.get("runtime_interface") is not None
    ):
        return None
    return value


def _local_executable_metadata_relation(observed, expected):
    """Return equal/enriched/absent, rejecting every changed or malformed proof."""
    observed_present = "local_executable_reuse_metadata" in observed
    expected_present = "local_executable_reuse_metadata" in expected
    observed_value = (
        _validated_local_executable_reuse_metadata(
            observed.get("local_executable_reuse_metadata")
        )
        if observed_present
        else None
    )
    expected_value = (
        _validated_local_executable_reuse_metadata(
            expected.get("local_executable_reuse_metadata")
        )
        if expected_present
        else None
    )
    if observed_present and observed_value is None:
        return None
    if expected_present and expected_value is None:
        return None
    for container, value in (
        (observed, observed_value),
        (expected, expected_value),
    ):
        if value is not None and value.get("operator_overlay_revision") != container.get(
            "operator_overlay_revision"
        ):
            return None
    if not observed_present and not expected_present:
        return "absent"
    if not observed_present and expected_value is not None:
        return "enriched"
    observed_identity = dict(observed_value or {})
    expected_identity = dict(expected_value or {})
    observed_identity.pop("operator_overlay_revision", None)
    expected_identity.pop("operator_overlay_revision", None)
    if observed_identity == expected_identity:
        return "equal"
    return None


def _deployment_contract_from_substrate(
    substrate, *, trusted_local_identity=None
):
    """Return the runtime-affecting identity while retaining full provenance elsewhere."""
    contract = json.loads(json.dumps(substrate))
    for field in (
        "controller_ownership",
        "docker_host_runtime_root",
        # The controller re-resolves an already selected concrete release set
        # as ``exact``. These selection labels are not deployment policy; the
        # selected releases and their verification records remain fully bound.
        "version_policy",
        "version_policy_source",
        "version_status",
        "unknown_acknowledged",
        # The append-only operator ledger revision changes when the operator
        # records evidence or a preference.  It invalidates the complete plan,
        # but is not itself a physical deployment input.
        "operator_overlay_revision",
    ):
        contract.pop(field, None)
    contract.pop("catalog_revision", None)
    catalog = contract.get("catalog_snapshot")
    if isinstance(catalog, dict):
        catalog.pop("catalog_revision", None)
        catalog.pop("catalog_updated_at", None)
        catalog.pop("operator_overlay_revision", None)
        for field in ("selection_policy", "selection_status", "selected_pair"):
            catalog.pop(field, None)
        for release in catalog.get("selected_releases") or []:
            if not isinstance(release, dict):
                continue
            for artifact in release.get("artifacts") or []:
                if not isinstance(artifact, dict):
                    continue
                # Catalog discoverability and advertised host platforms may be
                # added after an exact image has been deployed. They do not
                # alter that image, its qualification, or the generated runtime.
                artifact.pop("public", None)
                artifact.pop("platforms", None)
    local_identity = trusted_local_identity or _local_executable_identity_from_substrate(
        substrate
    )
    if local_identity is not None:
        _strip_local_executable_digest(contract, local_identity)
    if contract.get("network") in {"mainnet", "preprod", "preview"}:
        contract["config_source_dir"] = None
    return contract


def _catalog_reuse_metadata(substrate):
    """Expose the narrow eligibility facts needed for directional reuse checks."""
    raw_arch = str(os.environ.get("DWARF_HOST_ARCH") or platform.machine()).casefold()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}.get(
        raw_arch, raw_arch
    )
    container_platform = f"linux/{architecture}"
    catalog = substrate.get("catalog_snapshot")
    releases = catalog.get("selected_releases") if isinstance(catalog, dict) else None
    artifacts = []
    if isinstance(releases, list):
        for release in releases:
            if not isinstance(release, dict):
                continue
            source_revision = release.get("source_revision")
            source_revision_valid = bool(
                isinstance(source_revision, str)
                and re.fullmatch(r"[0-9a-f]{40}", source_revision)
            )
            verification = release.get("verification")
            verification_valid = bool(
                isinstance(verification, dict)
                and any(
                    isinstance(record, dict)
                    and record.get("status") == "confirmed"
                    and isinstance(record.get("checked_at"), str)
                    and bool(record["checked_at"])
                    and isinstance(record.get("evidence"), list)
                    and bool(record["evidence"])
                    and all(
                        isinstance(item, str) and bool(item)
                        for item in record["evidence"]
                    )
                    for record in verification.values()
                )
            )
            for artifact in release.get("artifacts") or []:
                if not isinstance(artifact, dict) or artifact.get("kind") != "oci":
                    continue
                platforms = artifact.get("platforms")
                platforms_state = (
                    "absent"
                    if "platforms" not in artifact
                    else (
                        "valid"
                        if isinstance(platforms, list)
                        and platforms
                        and all(isinstance(item, str) for item in platforms)
                        else "malformed"
                    )
                )
                public = artifact.get("public")
                public_state = (
                    "absent"
                    if "public" not in artifact
                    else "valid" if isinstance(public, bool) else "malformed"
                )
                reference = artifact.get("reference")
                digest = artifact.get("digest")
                reference_valid = bool(
                    isinstance(reference, str)
                    and "/" in reference
                    and not reference.startswith("/")
                    and not reference.endswith("/")
                    and "://" not in reference
                    and not re.search(r"\s", reference)
                    and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]+", reference)
                )
                digest_valid = bool(
                    isinstance(digest, str)
                    and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                )
                if reference_valid and "@" in reference:
                    reference_valid = reference.rsplit("@", 1)[1] == digest
                qualification_present = "qualification" in artifact
                qualification = artifact.get("qualification")
                qualification_valid = bool(
                    not qualification_present
                    or (
                        isinstance(qualification, dict)
                        and qualification.get("status") == "verified"
                        and isinstance(
                            qualification.get("evidence_sha256"), str
                        )
                        and re.fullmatch(
                            r"(?:sha256:)?[0-9a-f]{64}",
                            qualification["evidence_sha256"],
                        )
                    )
                )
                platforms_valid = bool(
                    platforms_state == "valid"
                    and len(platforms) <= 16
                    and len(set(platforms)) == len(platforms)
                    and all(
                        item in {"linux/amd64", "linux/arm64"}
                        for item in platforms
                    )
                )
                artifacts.append(
                    {
                        "implementation": release.get("implementation"),
                        "version": release.get("version"),
                        "reference": reference,
                        "digest": digest,
                        "availability": artifact.get("availability"),
                        "source_revision": source_revision,
                        "source_revision_valid": source_revision_valid,
                        "reference_valid": reference_valid,
                        "digest_valid": digest_valid,
                        "verification_valid": verification_valid,
                        "qualification_present": qualification_present,
                        "qualification_valid": qualification_valid,
                        "public": public if public_state == "valid" else None,
                        "public_state": public_state,
                        "platforms": (
                            sorted(set(platforms))
                            if platforms_state == "valid"
                            else None
                        ),
                        "platforms_state": (
                            platforms_state
                            if platforms_state != "valid" or platforms_valid
                            else "malformed"
                        ),
                    }
                )
    eligible = bool(artifacts) and all(
        row["public"] is True
        and row["public_state"] == "valid"
        and row["availability"] == "available"
        and row["source_revision_valid"] is True
        and row["reference_valid"] is True
        and row["digest_valid"] is True
        and row["verification_valid"] is True
        and row["qualification_valid"] is True
        and isinstance(row["platforms"], list)
        and row["platforms_state"] == "valid"
        and container_platform in row["platforms"]
        for row in artifacts
    )
    return {
        "container_platform": container_platform,
        "eligible": eligible,
        "artifacts": artifacts,
    }


def _validated_catalog_reuse_metadata(meta, *, require_verified=False):
    """Return exact artifact rows only for complete, honest tri-state metadata."""
    if not isinstance(meta, dict):
        return None
    platform_name = meta.get("container_platform")
    rows = meta.get("artifacts")
    if (
        platform_name not in {"linux/amd64", "linux/arm64"}
        or not isinstance(meta.get("eligible"), bool)
        or not isinstance(rows, list)
        or not rows
    ):
        return None
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        key = tuple(
            row.get(name)
            for name in (
                "implementation",
                "version",
                "source_revision",
                "reference",
                "digest",
            )
        )
        source_revision_valid = bool(
            isinstance(key[2], str) and re.fullmatch(r"[0-9a-f]{40}", key[2])
        )
        reference_valid = bool(
            isinstance(key[3], str)
            and "/" in key[3]
            and not key[3].startswith("/")
            and not key[3].endswith("/")
            and "://" not in key[3]
            and not re.search(r"\s", key[3])
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]+", key[3])
        )
        digest_valid = bool(
            isinstance(key[4], str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", key[4])
        )
        if reference_valid and "@" in key[3]:
            reference_valid = key[3].rsplit("@", 1)[1] == key[4]
        if (
            any(not isinstance(value, str) or not value for value in key[:2])
            or key[2] is not None and not isinstance(key[2], str)
            or not isinstance(key[3], str) or not key[3]
            or key[4] is not None and not isinstance(key[4], str)
            or key in result
            or any(
                not isinstance(row.get(name), bool)
                for name in (
                    "source_revision_valid",
                    "reference_valid",
                    "digest_valid",
                    "verification_valid",
                    "qualification_valid",
                )
            )
            or row.get("availability")
            not in {"available", "unavailable", "unknown"}
            or not isinstance(row.get("qualification_present"), bool)
            or row.get("source_revision_valid") is not source_revision_valid
            or row.get("reference_valid") is not reference_valid
            or row.get("digest_valid") is not digest_valid
            or row.get("qualification_present") is False
            and row.get("qualification_valid") is not True
        ):
            return None
        if require_verified and (
            any(
                row.get(name) is not True
                for name in (
                    "source_revision_valid",
                    "reference_valid",
                    "digest_valid",
                    "verification_valid",
                    "qualification_valid",
                )
            )
            or row.get("availability") != "available"
        ):
            return None
        public_state = row.get("public_state")
        if (
            public_state == "absent"
            and row.get("public") is not None
        ) or (
            public_state == "valid"
            and not isinstance(row.get("public"), bool)
        ) or public_state not in {"absent", "valid"}:
            return None
        platforms_state = row.get("platforms_state")
        platforms = row.get("platforms")
        if platforms_state == "absent":
            if platforms is not None:
                return None
        elif (
            platforms_state != "valid"
            or not isinstance(platforms, list)
            or not platforms
            or len(platforms) > 16
            or len(set(platforms)) != len(platforms)
            or any(item not in {"linux/amd64", "linux/arm64"} for item in platforms)
        ):
            return None
        result[key] = row
    eligible = all(
        row.get("public_state") == "valid"
        and row.get("public") is True
        and row.get("availability") == "available"
        and row.get("source_revision_valid") is True
        and row.get("reference_valid") is True
        and row.get("digest_valid") is True
        and row.get("verification_valid") is True
        and row.get("qualification_valid") is True
        and row.get("platforms_state") == "valid"
        and platform_name in row.get("platforms", [])
        for row in result.values()
    )
    return (platform_name, result) if meta["eligible"] is eligible else None


def _catalog_metadata_directionally_enriched(before_meta, after_meta):
    """Accept only authenticated, one-way eligibility enrichment."""
    before_validated = _validated_catalog_reuse_metadata(
        before_meta, require_verified=True
    )
    after_validated = _validated_catalog_reuse_metadata(
        after_meta, require_verified=True
    )
    if before_validated is None or after_validated is None:
        return False
    before_platform, before = before_validated
    platform_name, after = after_validated
    if before_platform != platform_name:
        return False

    if set(before) != set(after):
        return False
    enriched = False
    for key, current in after.items():
        retained = before[key]
        if retained.get("qualification_present") != current.get(
            "qualification_present"
        ):
            return False
        for row in (retained, current):
            if any(
                row.get(name) is not True
                for name in (
                    "source_revision_valid",
                    "reference_valid",
                    "digest_valid",
                    "verification_valid",
                    "qualification_valid",
                )
            ) or row.get("availability") != "available":
                return False
            if (
                row.get("public_state") == "malformed"
                or row.get("platforms_state") == "malformed"
            ):
                return False
        row_enriched = False
        retained_public = (retained.get("public_state"), retained.get("public"))
        current_public = (current.get("public_state"), current.get("public"))
        if retained_public[0] == "absent" and current_public == ("valid", True):
            row_enriched = True
        elif retained_public != current_public:
            if retained_public == ("valid", False) and current_public == ("valid", True):
                row_enriched = True
            else:
                return False

        retained_platforms = retained.get("platforms")
        current_platforms = current.get("platforms")
        if retained.get("platforms_state") == "absent":
            if (
                current.get("platforms_state") == "valid"
                and isinstance(current_platforms, list)
                and platform_name in current_platforms
            ):
                row_enriched = True
            elif current.get("platforms_state") != "absent":
                return False
        elif retained.get("platforms_state") == current.get("platforms_state") == "valid":
            retained_set = set(retained_platforms or [])
            current_set = set(current_platforms or [])
            if (
                platform_name not in current_set
                or not retained_set.issubset(current_set)
            ):
                return False
            row_enriched = row_enriched or retained_set != current_set
        else:
            return False
        if row_enriched:
            if (
                current_public != ("valid", True)
                or current.get("platforms_state") != "valid"
                or platform_name not in (current_platforms or [])
            ):
                return False
            enriched = True
    return enriched


def _operator_overlay_relation(before, after):
    """Classify the exact operator identity relationship, or reject it."""
    before_registration = before.get("operator_registration_id")
    after_registration = after.get("operator_registration_id")
    before_revision = before.get("operator_overlay_revision")
    after_revision = after.get("operator_overlay_revision")
    for value, registration, revision in (
        (before, before_registration, before_revision),
        (after, after_registration, after_revision),
    ):
        catalog = value.get("catalog_snapshot")
        if isinstance(catalog, dict) and (
            catalog.get("operator_registration_id") != registration
            or catalog.get("operator_overlay_revision") != revision
        ):
            return None
    if all(
        value is None
        for value in (
            before_registration,
            after_registration,
            before_revision,
            after_revision,
        )
    ):
        return "ordinary"
    if (
        not isinstance(before_registration, str)
        or not before_registration
        or after_registration != before_registration
        or not isinstance(before_revision, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", before_revision)
        or not isinstance(after_revision, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", after_revision)
    ):
        return None
    return "equal" if before_revision == after_revision else "changed"


def deployment_substrates_reusable(retained, current):
    """Return whether current is the same runtime plus trusted catalog enrichment."""
    if not isinstance(retained, dict) or not isinstance(current, dict):
        return False
    retained_metadata = _catalog_reuse_metadata(retained)
    current_metadata = _catalog_reuse_metadata(current)
    retained_local = _local_executable_identity_from_substrate(retained)
    current_local = _local_executable_identity_from_substrate(current)
    retained_contract_identity = None
    if retained_local is None and current_local is None:
        if (
            _local_executable_identity_from_substrate(
                retained, require_package_proof=False
            )
            is not None
            or _local_executable_identity_from_substrate(
                current, require_package_proof=False
            )
            is not None
        ):
            return False
        local_relation = "absent"
    elif retained_local is None and current_local is not None:
        retained_embedded = _local_executable_identity_from_substrate(
            retained, require_package_proof=False
        )
        if retained_embedded is None:
            local_relation = "enriched"
        elif {
            key: value
            for key, value in retained_embedded.items()
            if key != "operator_overlay_revision"
        } == {
            key: value
            for key, value in current_local.items()
            if key != "operator_overlay_revision"
        }:
            local_relation = "proof-enriched"
            retained_contract_identity = current_local
        else:
            return False
    elif retained_local is not None and current_local is None:
        return False
    elif {
        key: value
        for key, value in retained_local.items()
        if key != "operator_overlay_revision"
    } == {
        key: value
        for key, value in current_local.items()
        if key != "operator_overlay_revision"
    }:
        local_relation = "equal"
    else:
        return False
    if _deployment_contract_from_substrate(
        retained, trusted_local_identity=retained_contract_identity
    ) != _deployment_contract_from_substrate(current):
        return False
    overlay_relation = _operator_overlay_relation(retained, current)
    if overlay_relation is None:
        return False
    if overlay_relation in {"equal", "changed"}:
        for substrate in (retained, current):
            catalog = substrate.get("catalog_snapshot")
            if (
                not isinstance(catalog, dict)
                or catalog.get("operator_registration_id")
                != substrate.get("operator_registration_id")
                or catalog.get("operator_overlay_revision")
                != substrate.get("operator_overlay_revision")
            ):
                return False
    if retained_metadata == current_metadata and overlay_relation == "changed":
        return bool(
            local_relation in {"absent", "equal", "proof-enriched"}
            and _validated_catalog_reuse_metadata(retained_metadata) is not None
            and _validated_catalog_reuse_metadata(current_metadata) is not None
        )
    if (
        retained_metadata == current_metadata
        and overlay_relation == "equal"
        and local_relation == "enriched"
    ):
        return bool(
            _validated_catalog_reuse_metadata(retained_metadata) is not None
            and _validated_catalog_reuse_metadata(current_metadata) is not None
        )
    return bool(
        overlay_relation in {"ordinary", "equal"}
        and local_relation in {"absent", "equal"}
        and _catalog_metadata_directionally_enriched(
            retained_metadata, current_metadata
        )
    )


def deployment_topologies_reusable(observed, expected):
    """Shared dashboard/controller identity predicate; never trusts caller flags."""
    if not isinstance(observed, dict) or not isinstance(expected, dict):
        return False

    def canonical_plan_hash(value):
        claimed = value.get("plan_sha256")
        if not isinstance(claimed, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", claimed
        ):
            return None
        body = {key: item for key, item in value.items() if key != "plan_sha256"}
        actual = "sha256:" + hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return claimed if claimed == actual else None

    observed_plan = canonical_plan_hash(observed)
    expected_plan = canonical_plan_hash(expected)
    if observed_plan is None or expected_plan is None:
        return False
    observed_metadata = _validated_catalog_reuse_metadata(
        observed.get("catalog_reuse_metadata")
    )
    expected_metadata = _validated_catalog_reuse_metadata(
        expected.get("catalog_reuse_metadata")
    )
    if observed_metadata is None or expected_metadata is None:
        return False
    ignored = {
        "plan_sha256",
        "catalog_revision",
        "substrate_sha256",
        "catalog_reuse_metadata",
        "operator_overlay_revision",
        "local_executable_reuse_metadata",
    }
    observed_invariants = {
        key: value for key, value in observed.items() if key not in ignored
    }
    expected_invariants = {
        key: value for key, value in expected.items() if key not in ignored
    }
    if observed_invariants != expected_invariants:
        return False
    overlay_relation = _operator_overlay_relation(observed, expected)
    if overlay_relation is None:
        return False
    local_relation = _local_executable_metadata_relation(observed, expected)
    if local_relation is None:
        return False
    if (
        observed_plan == expected_plan
        and overlay_relation in {"ordinary", "equal"}
    ):
        return bool(
            local_relation in {"absent", "equal"}
            and observed.get("catalog_reuse_metadata") == expected.get(
                "catalog_reuse_metadata"
            )
        )
    observed_deployment = observed.get("deployment_sha256")
    metadata_matches = observed.get("catalog_reuse_metadata") == expected.get(
        "catalog_reuse_metadata"
    )
    return bool(
        isinstance(observed_deployment, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", observed_deployment)
        and observed_deployment == expected.get("deployment_sha256")
        and (
            (
                metadata_matches
                and overlay_relation == "changed"
                and local_relation in {"absent", "equal"}
                and observed_plan != expected_plan
            )
            or (
                overlay_relation in {"ordinary", "equal"}
                and local_relation in {"absent", "equal"}
                and _catalog_metadata_directionally_enriched(
                    observed.get("catalog_reuse_metadata"),
                    expected.get("catalog_reuse_metadata"),
                )
            )
            or (
                metadata_matches
                and overlay_relation == "equal"
                and local_relation == "enriched"
                and observed_plan != expected_plan
            )
        )
    )


def desired_topology_from_substrate(profile_id, substrate):
    """Return one exact reconciliation identity for a retained substrate.

    The compact node rows remain suitable for inventory joins, while the
    substrate digest binds generation inputs that do not appear in a Docker
    container identity (for example patched executable provenance, genesis
    features, and protocol/KES configuration).
    """
    # Controller ownership and the host-mapped current directory are execution
    # metadata added after the dashboard/controller independently resolve the
    # same topology.  They are verified separately and are not generator input.
    substrate_contract = {
        key: value
        for key, value in substrate.items()
        if key
        not in {
            "controller_ownership",
            "docker_host_runtime_root",
            # Selection policy explains how the catalog chose the immutable
            # releases; it is retained in the substrate audit record but is
            # not a runtime-generation input.  The controller deliberately
            # re-resolves a requested concrete node set as ``exact``.
            "version_policy",
            "version_policy_source",
            "version_status",
            "unknown_acknowledged",
        }
    }
    substrate_contract = json.loads(json.dumps(substrate_contract))
    catalog_contract = substrate_contract.get("catalog_snapshot")
    if isinstance(catalog_contract, dict):
        for field in ("selection_policy", "selection_status", "selected_pair"):
            catalog_contract.pop(field, None)
    # Public-network adapters fetch the canonical network assets inside their
    # owned runtime.  Catalog profiles may retain a source-checkout-only /opt
    # or /home hint which is intentionally cleared by an installed controller
    # when that host path is unavailable.  It is therefore not generator
    # identity for public networks.  Custom/devnet source paths remain exact
    # provenance and continue to affect the reconciliation digest.
    if substrate_contract.get("network") in {"mainnet", "preprod", "preview"}:
        substrate_contract["config_source_dir"] = None
    substrate_body = json.dumps(
        substrate_contract, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    deployment_body = json.dumps(
        _deployment_contract_from_substrate(substrate),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    desired = {
        "schema_version": "v1",
        "topology_id": profile_id,
        "profile_id": profile_id,
        # Reconciliation/controller identity always carries the concrete
        # network magic, including named public networks whose adapter does
        # not need a CLI --testnet-magic argument.
        "network_magic": substrate.get("network_magic"),
        "peer_sharing": substrate.get("peer_sharing"),
        "deployment_adapter": substrate.get("deployment_adapter"),
        "catalog_revision": substrate.get("catalog_revision"),
        "topology": substrate.get("topology"),
        "substrate_sha256": "sha256:" + hashlib.sha256(substrate_body).hexdigest(),
        "deployment_sha256": "sha256:"
        + hashlib.sha256(deployment_body).hexdigest(),
        "catalog_reuse_metadata": _catalog_reuse_metadata(substrate),
        "nodes": [
            {
                "id": str(node["id"]),
                "implementation": str(
                    node["impl"] if "impl" in node else node["implementation"]
                ),
                "version": str(node["version"]),
                "image": str(node["image"]),
                "role": str(node["role"]),
                "supporting": bool(node.get("supporting", False)),
            }
            for node in substrate["nodes"]
        ],
    }
    for field in ("operator_registration_id", "operator_overlay_revision"):
        value = substrate.get(field)
        if value is not None:
            desired[field] = value
    local_executable_identity = _local_executable_identity_from_substrate(substrate)
    if local_executable_identity is not None:
        desired["local_executable_reuse_metadata"] = local_executable_identity
    if any(node["implementation"] == "amaru" for node in desired["nodes"]):
        desired["amaru_json_traces"] = bool(substrate.get("amaru_json_traces"))
    encoded = json.dumps(desired, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return {
        **desired,
        "plan_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def desired_topology_from_retained_substrate(
    profile_id, substrate, *, expected_desired=None
):
    """Rebuild retained identity with only a matching current package proof."""
    desired = desired_topology_from_substrate(profile_id, substrate)
    expected_local = _validated_local_executable_reuse_metadata(
        (expected_desired or {}).get("local_executable_reuse_metadata")
    )
    if expected_local is None:
        return desired
    retained_local = _local_executable_identity_from_substrate(
        substrate, require_package_proof=False
    )
    if retained_local is None or _operator_overlay_relation(
        substrate, expected_desired or {}
    ) not in {"equal", "changed"}:
        return desired
    retained_identity = {
        key: value
        for key, value in retained_local.items()
        if key != "operator_overlay_revision"
    }
    expected_identity = {
        key: value
        for key, value in expected_local.items()
        if key != "operator_overlay_revision"
    }
    if retained_identity != expected_identity:
        return desired

    enriched = json.loads(json.dumps(substrate))
    releases = (enriched.get("catalog_snapshot") or {}).get("selected_releases")
    if not isinstance(releases, list):
        return desired
    matching = [
        release
        for release in releases
        if isinstance(release, dict)
        and isinstance(release.get("operator_registration"), dict)
        and release["operator_registration"].get("registration_id")
        == expected_local["registration_id"]
    ]
    if len(matching) != 1:
        return desired
    matching[0]["operator_package_proof"] = {
        "status": "verified",
        "registration_id": expected_local["registration_id"],
        "config_image_id": expected_local["config_image_id"],
        "executable_digest": expected_local["executable_digest"],
        "platform": expected_local["platform"],
        "runtime_interface": expected_local["runtime_interface"],
        "controller_installation_id": expected_local[
            "controller_installation_id"
        ],
    }
    enriched_desired = desired_topology_from_substrate(profile_id, enriched)
    enriched_local = enriched_desired.get("local_executable_reuse_metadata")
    if not isinstance(enriched_local, dict) or {
        key: value
        for key, value in enriched_local.items()
        if key != "operator_overlay_revision"
    } != expected_identity:
        return desired
    return enriched_desired


def verified_desired_topology_from_legacy(
    profile_id, substrate, legacy_desired
):
    """Upgrade one exact pre-deployment-hash desired record, or fail closed."""
    if (
        not isinstance(profile_id, str)
        or not profile_id
        or not isinstance(substrate, dict)
        or substrate.get("profile_id") != profile_id
        or not isinstance(legacy_desired, dict)
    ):
        return None
    if (
        "deployment_sha256" in legacy_desired
        or "catalog_reuse_metadata" in legacy_desired
    ):
        return None
    try:
        current = desired_topology_from_substrate(profile_id, substrate)
    except (KeyError, TypeError, ValueError):
        return None
    legacy_body = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "plan_sha256",
            "deployment_sha256",
            "catalog_reuse_metadata",
        }
    }
    encoded = json.dumps(
        legacy_body, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    verified_legacy = {
        **legacy_body,
        "plan_sha256": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }
    return current if legacy_desired == verified_legacy else None


def desired_topology_for_profile(profile, version_preview):
    """Return the immutable desired topology consumed by reconciliation."""
    substrate = versioned_substrate_for_profile(profile, version_preview)
    return desired_topology_from_substrate(profile.id, substrate)


def _versioned_deploy_command(profile, version_preview, remote_dwarf_root=None):
    substrate = versioned_substrate_for_profile(profile, version_preview)
    use_amaru_control = substrate["deployment_adapter"] == "amaru-control"
    adapter = (
        "runtime_amaru_control_substrate.py"
        if use_amaru_control
        else "runtime_compose_substrate.py"
    )
    runtime = shlex.quote(profile.remote_runtime_root)
    project = shlex.quote(profile.compose_project)
    config_body = {
        "substrate": substrate,
        "output_dir": f"{profile.remote_runtime_root}/evidence",
        "runtime_root": profile.remote_runtime_root,
        "compose_project": profile.compose_project,
        "healthy_timeout_seconds": 300,
        "deployment_adapter": substrate["deployment_adapter"],
        "network": substrate["network"],
        "peer_sharing": substrate["peer_sharing"],
        "config_source_dir": substrate.get("config_source_dir"),
        "upstream_peer_address": substrate.get("upstream_peer_address"),
        "listen_address": substrate.get("listen_address"),
    }
    if use_amaru_control:
        cardano = next(
            node for node in substrate["nodes"] if node["impl"] == "cardano-node"
        )
        amaru = next(node for node in substrate["nodes"] if node["impl"] == "amaru")
        config_body.update(
            {
                "profile_id": profile.id,
                "scope": substrate["scope"],
                "lifecycle": "cardano_amaru_relay_bootstrap_control",
                "supporting_cardano_version": cardano["version"],
                "cardano_image": cardano["image"],
                "amaru_version": amaru["version"],
                "amaru_image": amaru["image"],
                "measurement_target_mode": amaru["target_mode"],
                "amaru_json_traces": substrate["amaru_json_traces"],
                "amaru_runtime_interface": (
                    "extracted-binary"
                    if amaru["target_mode"] == "patched"
                    else amaru.get("amaru_runtime_interface")
                ),
                "measurement_target_identity": {
                    key: amaru[key]
                    for key in (
                        "version",
                        "source_revision",
                        "target_mode",
                        "image",
                        "image_digest",
                        "executable_digest",
                        "patch_set_sha256",
                        "build_result_sha256",
                        "runtime_probe_image",
                        "runtime_probe_log_sha256",
                        "source_image_reference",
                        "image_manifest_digest",
                        "image_config_digest",
                        "qualification_image_id",
                        "transfer_source_installation_id",
                        "transfer_manifest_sha256",
                    )
                    if key in amaru
                },
                "healthy_timeout_seconds": 1800,
            }
        )
        if substrate.get("cardano_experimental_protocols") is not None:
            config_body["cardano_experimental_protocols"] = bool(
                substrate["cardano_experimental_protocols"]
            )
        if substrate.get("kes_genesis_override"):
            config_body["kes_genesis_override"] = dict(
                substrate["kes_genesis_override"]
            )
        if substrate["plutus_v2_genesis"]:
            cost_model_path = Path(str(substrate["plutus_v2_cost_model_path"]))
            if not cost_model_path.is_absolute() and remote_dwarf_root:
                cost_model_path = Path(remote_dwarf_root) / cost_model_path
            config_body.update(
                {
                    "plutus_v2_genesis": True,
                    "plutus_v2_cost_model_path": str(cost_model_path),
                    "plutus_v2_cost_model_sha256": substrate[
                        "plutus_v2_cost_model_sha256"
                    ],
                }
            )
    config_json = json.dumps(config_body, indent=2, sort_keys=True)
    image_refs = sorted(
        {node["image"] for node in substrate["nodes"] if node["target_mode"] == "stock"}
    )
    image_refs.extend(
        node["runtime_probe_image"]
        for node in substrate["nodes"]
        if node["target_mode"] == "patched" and node.get("runtime_probe_image")
    )
    image_refs = sorted(set(image_refs))
    pull_lines = "\n".join(f"docker pull {shlex.quote(image)}" for image in image_refs)
    local_image_checks = []
    for node in substrate["nodes"]:
        if node["target_mode"] != "patched":
            continue
        image = shlex.quote(node["image"])
        expected_id = shlex.quote(node["image_digest"])
        revision = shlex.quote(node["source_revision"])
        patch_set = shlex.quote(node["patch_set_sha256"])
        implementation_label = (
            "Cardano-node" if node["impl"] == "cardano-node" else "Amaru"
        )
        identity_check = f'''actual_id=$(docker image inspect --format '{{{{.Id}}}}' {image} 2>/dev/null || true)
if [ "$actual_id" != {expected_id} ]; then
  echo "Patched {implementation_label} image is missing or has the wrong image id: {node['image']}" >&2
  exit 8
fi
actual_revision=$(docker image inspect --format '{{{{index .Config.Labels "org.opencontainers.image.revision"}}}}' {image})
actual_patch=$(docker image inspect --format '{{{{index .Config.Labels "org.dwarf.measurement.patch-sha256"}}}}' {image})
if [ "$actual_revision" != {revision} ] || [ "$actual_patch" != {patch_set} ]; then
  echo "Patched {implementation_label} image labels do not match the audited target identity" >&2
  exit 9
fi'''
        if node["impl"] == "cardano-node":
            compatibility_probe = f'''echo "DWARF patched-target Cardano-node compatibility probe"
docker run --rm --entrypoint /usr/local/bin/cardano-node {image} --version'''
        else:
            probe_image = shlex.quote(node["runtime_probe_image"])
            compatibility_probe = f'''probe_dir=$(mktemp -d)
probe_container=""
cleanup_dwarf_probe() {{
  [ -z "$probe_container" ] || docker rm -f "$probe_container" >/dev/null 2>&1 || true
  rm -rf "$probe_dir"
}}
trap cleanup_dwarf_probe EXIT
probe_container=$(docker create {image})
docker cp "$probe_container:/usr/local/bin/amaru" "$probe_dir/amaru"
docker rm "$probe_container" >/dev/null
probe_container=""
chmod 0755 "$probe_dir/amaru"
echo "DWARF patched-target wrapper compatibility probe"
docker run --rm --entrypoint /bin/bash --volume "$probe_dir/amaru:/target/amaru:ro" {probe_image} -lc '/target/amaru --version'
cleanup_dwarf_probe
trap - EXIT'''
        local_image_checks.append(identity_check + "\n" + compatibility_probe)
    image_check_lines = "\n".join(local_image_checks)
    dwarf_root_assignment = (
        f"dwarf_root={shlex.quote(remote_dwarf_root)}"
        if remote_dwarf_root
        else 'dwarf_root="${ADA2_DWARF_ROOT:-}"'
    )
    return f"""set -e
runtime={runtime}
project={project}
if [ -e "$runtime/env" ] || [ -e "$runtime/docker-compose.yml" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
{dwarf_root_assignment}
if [ -z "$dwarf_root" ] || [ ! -f "$dwarf_root/scripts/{adapter}" ]; then
  echo "ADA2_DWARF_ROOT must identify the installed DWARF source root" >&2
  exit 7
fi
mkdir -p "$runtime"
config_path="$runtime/versioned-deployment.json"
cat > "$config_path" <<'DWARF_VERSIONED_DEPLOYMENT'
{config_json}
DWARF_VERSIONED_DEPLOYMENT
{pull_lines}
{image_check_lines}
cd "$dwarf_root"
PYTHONPATH="$dwarf_root" python3 scripts/{adapter} --config "$config_path"
"""


def compose_template(profile):
    """Render a docker-compose.yml for a profile.

    N node services, one per pool node. Each shares a bridge network named after
    the compose project so the nodes can reach each other by container name. The
    env dir is bind-mounted into each container.
    """
    project = profile.compose_project
    network = f"{project}-net"
    lines = [
        "name: " + project,
        "networks:",
        f"  {network}:",
        "    driver: bridge",
        "services:",
    ]
    for i in range(1, profile.node_count + 1):
        service = f"node{i}"
        lines.extend([
            f"  {service}:",
            "    image: dwarf/cardano-node:${CARDANO_NODE_SHA:-latest}",
            "    labels:",
            "      ada2.managed: dwarf",
            f"      ada2.profile: {profile.id}",
            f"      ada2.service: {service}",
            "    networks:",
            f"      {network}:",
            "        aliases:",
            f"          - {service}",
            "    volumes:",
            f"      - {profile.remote_runtime_root}/env:/env:rw",
            f"      - {profile.remote_runtime_root}/logs/{service}:/logs:rw",
            "    command:",
            f"      - --config=/env/node-data/node{i}/config.json",
            f"      - --topology=/env/node-data/node{i}/topology.json",
            f"      - --database-path=/env/node-data/node{i}/db",
            f"      - --socket-path=/env/socket/{service}/sock",
            "    restart: on-failure",
        ])
    return "\n".join(lines) + "\n"


def deploy_dry_run_text(profile):
    from dataclasses import asdict
    from profile_manager.deployment_versions import build_deployment_version_preview

    preview = build_deployment_version_preview(asdict(profile))
    substrate = versioned_substrate_for_profile(profile, preview)
    identities = ", ".join(
        f"{node['id']}={node['impl']} {node['version']} ({node['image']})"
        for node in substrate["nodes"]
    )
    image_preflight = (
        "Would pull stock and wrapper images, verify the local patched image identity, "
        "and run its exact wrapper-compatibility probe before launch.\n"
        if any(node["target_mode"] == "patched" for node in substrate["nodes"])
        else "Would pull every digest-pinned image before launch and verify the running image and node-reported version.\n"
    )
    return (
        f"DRY RUN deploy for {profile.id}\n"
        f"Version policy: {preview['policy']} ({preview['policy_source']}; {preview['status']}).\n"
        f"Deployment adapter: {substrate['deployment_adapter']} ({preview['deployment_context']}).\n"
        f"Exact real-node artifacts: {identities}.\n"
        f"Would create a fresh runtime under {profile.remote_runtime_root} while preserving the profile's topology, network, peer, and configuration contract.\n"
        f"{image_preflight}"
        "No remote state changed.\n"
    )

    # The code below is retained temporarily while downstream importers move
    # to the immutable adapter path above; it is unreachable by construction.
    if profile.version_policy != "legacy":
        from dataclasses import asdict
        from profile_manager.deployment_versions import build_deployment_version_preview

        preview = build_deployment_version_preview(asdict(profile))
        substrate = versioned_substrate_for_profile(profile, preview)
        identities = ", ".join(
            f"{node['id']}={node['impl']} {node['version']} ({node['image']})"
            for node in substrate["nodes"]
        )
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Version policy: {preview['policy']} ({preview['status']}).\n"
            f"Exact real-node artifacts: {identities}.\n"
            f"Would create a fresh Docker substrate under {profile.remote_runtime_root}.\n"
            "Would pull every digest-pinned image before launch and verify the running image and node-reported version.\n"
            "No remote state changed.\n"
        )
    if _profile_deploy_mode(profile) == "mixed":
        from profile_manager.custom_packages import package_deploy_dry_run_text

        package, custom_profile = _profile_as_custom_bundle(profile)
        return package_deploy_dry_run_text(package, custom_profile)
    if _is_generated_haskell_local_profile(profile):
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Generated Haskell-only local devnet: {profile.node_count} Haskell node(s), 0 Amaru node(s).\n"
            f"Topology pattern: {profile.topology_pattern}.\n"
            f"Shared genesis: {'yes' if profile.shared_genesis else 'no'}.\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            "Would create the devnet env via /home/dwarf/.local/bin/cardano-testnet create-env.\n"
            "Would run Haskell nodes from /home/dwarf/.local/bin/cardano-node under tmux sessions scoped to this profile.\n"
            "Would auto-assign localhost listener ports and rewrite local-mesh topology from the generated node count.\n"
            "Would write runtime metadata under runtime.json for host-process inspection.\n"
            "No remote state changed.\n"
        )
    if _profile_deploy_mode(profile) == "haskell-only" and profile.config_source_dir:
        network = _public_network(profile)
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            f"Would fail fast if upstream peer {profile.upstream_peer_address} is unreachable.\n"
            f"Would copy the official {network} config set from {profile.config_source_dir}.\n"
            f"Would rewrite topology.json under the runtime root to use {profile.upstream_peer_address} as the bootstrap peer.\n"
            f"Would start one Haskell cardano-node from /home/dwarf/.local/bin/cardano-node listening on {profile.listen_address}.\n"
            "Would write runtime metadata under runtime.json and keep logs under logs/node1/.\n"
            f"This profile depends on public {network} connectivity and is not a self-contained local devnet.\n"
            "No remote state changed.\n"
        )
    if _profile_deploy_mode(profile) == "amaru-only":
        network = _public_network(profile)
        return (
            f"DRY RUN deploy for {profile.id}\n"
            f"Would create remote runtime root: {profile.remote_runtime_root}\n"
            f"Would fail fast if upstream peer {profile.upstream_peer_address} is unreachable.\n"
            f"Would bootstrap Amaru for network {profile.amaru_network} using the bundled upstream bootstrap config.\n"
            f"Would start one Amaru node from /home/dwarf/amaru-verification/target/debug/amaru listening on {profile.listen_address}.\n"
            "Would write runtime metadata under runtime.json and keep logs under logs/amaru1/.\n"
            f"This profile depends on public {network} connectivity and is not a self-contained local devnet.\n"
            "No remote state changed.\n"
        )
    return (
        f"DRY RUN deploy for {profile.id}\n"
        f"Would create remote runtime root: {profile.remote_runtime_root}\n"
        f"Would generate devnet env via cardano-testnet create-env (in a build-stage container).\n"
        f"Would (re)build docker image dwarf/cardano-node from {REMOTE_SOURCE_PATH}.\n"
        f"Would bring up docker compose project: {profile.compose_project} ({profile.node_count} node containers).\n"
        "Would verify no other Dwarf-managed compose project is running first.\n"
        "No remote state changed.\n"
    )


def remove_dry_run_text():
    return (
        "DRY RUN remove\n"
        "Would detect active Dwarf-managed compose projects.\n"
        "Would ask for explicit confirmation before stopping anything.\n"
        "Would run docker compose down -v and archive the runtime directory.\n"
        "No remote state changed.\n"
    )


def deploy_command(profile, version_preview=None, remote_dwarf_root=None):
    """Build the image, generate the env via cardano-testnet, write compose, up -d."""
    if version_preview is None:
        from dataclasses import asdict
        from profile_manager.deployment_versions import build_deployment_version_preview

        version_preview = build_deployment_version_preview(asdict(profile))
    return _versioned_deploy_command(
        profile,
        version_preview,
        remote_dwarf_root=remote_dwarf_root,
    )

    # No caller can reach the historical ambient-binary implementation below.
    if profile.version_policy != "legacy":
        if version_preview is None:
            from dataclasses import asdict
            from profile_manager.deployment_versions import build_deployment_version_preview

            version_preview = build_deployment_version_preview(asdict(profile))
        return _versioned_deploy_command(profile, version_preview)
    deploy_mode = _profile_deploy_mode(profile)
    if deploy_mode == "mixed":
        from profile_manager.custom_packages import package_deploy_command

        package, custom_profile = _profile_as_custom_bundle(profile)
        return package_deploy_command(package, custom_profile)
    if _is_generated_haskell_local_profile(profile):
        runtime = shlex.quote(profile.remote_runtime_root)
        project = shlex.quote(profile.compose_project)
        peer_sharing = _bool_text(profile.peer_sharing)
        return f"""set -e
runtime={runtime}
project={project}
cardano_node_bin=/home/dwarf/.local/bin/cardano-node
cardano_cli_bin=/home/dwarf/.local/bin/cardano-cli
cardano_testnet_bin=/home/dwarf/.local/bin/cardano-testnet
if [ -e "$runtime/env" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
mkdir -p "$runtime/logs" "$runtime/socket" "$runtime/pids"
cardano_sha=$("$cardano_node_bin" --version 2>/dev/null | head -n 1 | tr ' /' '__' | tr -cd '[:alnum:]_.-')
[ -n "$cardano_sha" ] || cardano_sha=host
printf 'CARDANO_NODE_SHA=%s\\n' "$cardano_sha" > "$runtime/.env"
mkdir -p "$runtime/env"
export CARDANO_NODE="$cardano_node_bin"
export CARDANO_CLI="$cardano_cli_bin"
"$cardano_testnet_bin" \\
  create-env --output "$runtime/env" --num-pool-nodes {profile.node_count} --testnet-magic {profile.network_magic} --node-logging-format json
python3 - "$runtime/env/configuration.yaml" <<'PY'
import sys
path = sys.argv[1]
body = open(path, encoding="utf-8").read()
body = body.replace('"PeerSharing": true', '"PeerSharing": {peer_sharing}')
body = body.replace('"PeerSharing": false', '"PeerSharing": {peer_sharing}')
open(path, "w", encoding="utf-8").write(body)
PY
python3 - "$runtime/env" "$runtime/runtime.json" "$project" {profile.node_count} <<'PY'
import json, os, sys
env_dir = sys.argv[1]
metadata_path = sys.argv[2]
project = sys.argv[3]
node_count = int(sys.argv[4])
base_port = 33001
nodes = []
for i in range(1, node_count + 1):
    topo_path = os.path.join(env_dir, "node-data", f"node{{i}}", "topology.json")
    if not os.path.exists(topo_path):
        continue
    data = json.load(open(topo_path))
    port = base_port + i - 1
    peers = [{{"address": "127.0.0.1", "port": base_port + j - 1}}
             for j in range(1, node_count + 1) if j != i]
    if "Producers" in data:
        data["Producers"] = [dict(peer, valency=1) for peer in peers]
    else:
        data["localRoots"] = [{{
            "accessPoints": peers,
            "advertise": False,
            "valency": len(peers),
        }}]
    json.dump(data, open(topo_path, "w"), indent=2)
    nodes.append({{
        "name": f"node{{i}}",
        "port": port,
        "session": f"{{project}}-node{{i}}",
        "socket_path": os.path.join(os.path.dirname(metadata_path), "socket", f"node{{i}}.sock"),
        "db_dir": os.path.join(env_dir, "node-data", f"node{{i}}", "db"),
        "config_path": os.path.join(env_dir, "configuration.yaml"),
        "topology_path": topo_path,
        "log_path": os.path.join(os.path.dirname(metadata_path), "logs", f"node{{i}}", "stdout.log"),
        "pid_file": os.path.join(os.path.dirname(metadata_path), "pids", f"node{{i}}.pid"),
    }})
json.dump({{"profile_id": os.path.basename(os.path.dirname(metadata_path)), "haskell_nodes": nodes}}, open(metadata_path, "w"), indent=2)
PY
for i in $(seq 1 {profile.node_count}); do mkdir -p "$runtime/logs/node$i"; done
for i in $(seq 1 {profile.node_count}); do
  session="$project-node$i"
  port=$((33000 + i))
  socket_path="$runtime/socket/node$i.sock"
  db_dir="$runtime/env/node-data/node$i/db"
  config_path="$runtime/env/configuration.yaml"
  topology_path="$runtime/env/node-data/node$i/topology.json"
  log_path="$runtime/logs/node$i/stdout.log"
  pid_file="$runtime/pids/node$i.pid"
  kes_key="$runtime/env/pools-keys/pool$i/kes.skey"
  vrf_key="$runtime/env/pools-keys/pool$i/vrf.skey"
  opcert="$runtime/env/pools-keys/pool$i/opcert.cert"
  byron_delegation="$runtime/env/pools-keys/pool$i/byron-delegation.cert"
  byron_signing="$runtime/env/pools-keys/pool$i/byron-delegate.key"
  if tmux has-session -t "$session" 2>/dev/null; then
    echo "Session already exists: $session" >&2
    exit 5
  fi
  tmux new-session -d -s "$session" "bash -lc 'echo \\$$ > $pid_file; exec $cardano_node_bin run --config $config_path --topology $topology_path --database-path $db_dir --socket-path $socket_path --port $port --host-addr 127.0.0.1 --shelley-kes-key $kes_key --shelley-vrf-key $vrf_key --shelley-operational-certificate $opcert --byron-delegation-certificate $byron_delegation --byron-signing-key $byron_signing 2>&1 | tee -a $log_path'"
done
sleep 10
tmux ls | grep "$project" || true
"""
    if deploy_mode == "haskell-only" and profile.config_source_dir:
        runtime = shlex.quote(profile.remote_runtime_root)
        session = shlex.quote(profile.compose_project)
        node_bin = shlex.quote("/home/dwarf/.local/bin/cardano-node")
        public_network = _public_network(profile)
        testbed = _public_testbed(profile)
        listen_address_raw = profile.listen_address or "127.0.0.1:39100"
        listen_host, listen_port_text = listen_address_raw.rsplit(":", 1)
        listen_port = int(listen_port_text)
        peer_address = shlex.quote(profile.upstream_peer_address or "preview-node.play.dev.cardano.org:3001")
        listen_address = shlex.quote(listen_address_raw)
        listen_host_q = shlex.quote(listen_host)
        config_source_dir = shlex.quote(profile.config_source_dir)
        return f"""set -e
runtime={runtime}
session={session}
node_bin={node_bin}
peer_address={peer_address}
listen_address={listen_address}
config_source_dir={config_source_dir}
config_root="$runtime/config"
db_dir="$runtime/db"
socket_dir="$runtime/socket"
socket_path="$socket_dir/node.sock"
log_dir="$runtime/logs/node1"
log_path="$log_dir/stdout.log"
metadata_path="$runtime/runtime.json"
pid_file="$runtime/node.pid"
if tmux has-session -t "$session" 2>/dev/null; then
  echo "Session already exists: $session" >&2
  exit 3
fi
if [ -e "$db_dir" ] || [ -e "$config_root" ]; then
  echo "Runtime assets already exist under: $runtime" >&2
  exit 4
fi
python3 - "$peer_address" <<'PY'
import socket
import sys
peer = sys.argv[1]
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
with socket.create_connection((host, port), timeout=5):
    pass
PY
mkdir -p "$config_root" "$db_dir" "$socket_dir" "$log_dir"
cp "$config_source_dir"/config.json "$config_root"/
cp "$config_source_dir"/topology.json "$config_root"/
cp "$config_source_dir"/byron-genesis.json "$config_root"/
cp "$config_source_dir"/shelley-genesis.json "$config_root"/
cp "$config_source_dir"/alonzo-genesis.json "$config_root"/
cp "$config_source_dir"/conway-genesis.json "$config_root"/
for optional_file in checkpoints.json peer-snapshot.json; do
  [ -f "$config_source_dir/$optional_file" ] && cp "$config_source_dir/$optional_file" "$config_root"/
done
python3 - "$peer_address" "$config_root/topology.json" <<'PY'
import json
import pathlib
import sys

peer = sys.argv[1]
topology_path = pathlib.Path(sys.argv[2])
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
body = json.loads(topology_path.read_text(encoding="utf-8"))
body["bootstrapPeers"] = [{{"address": host, "port": port}}]
topology_path.write_text(json.dumps(body, indent=2) + "\\n", encoding="utf-8")
PY
cat > "$metadata_path" <<JSON
{{
  "profile_id": "{profile.id}",
  "target_implementation": "cardano-node",
  "network": "{public_network}",
  "upstream_peer_address": "{profile.upstream_peer_address or 'preview-node.play.dev.cardano.org:3001'}",
  "listen_address": "{profile.listen_address or '127.0.0.1:39100'}",
  "session": "{profile.compose_project}",
  "binary": "/home/dwarf/.local/bin/cardano-node",
  "chain_dir": "$db_dir",
  "log_path": "$log_dir/stdout.log",
  "pid_file": "$pid_file",
  "socket_path": "$socket_path",
  "config_root": "$config_root",
  "testbed": "{testbed}"
}}
JSON
tmux new-session -d -s "$session" "bash -lc 'cd $config_root; echo \\$$ > $pid_file; exec $node_bin run --config config.json --topology topology.json --database-path $db_dir --socket-path $socket_path --port {listen_port} --host-addr {listen_host_q} 2>&1 | tee -a $log_path'"
sleep 10
tmux ls
"""
    if deploy_mode == "amaru-only":
        runtime = shlex.quote(profile.remote_runtime_root)
        session = shlex.quote(profile.compose_project)
        amaru_bin = shlex.quote("/home/dwarf/amaru-verification/target/debug/amaru")
        amaru_network = shlex.quote(profile.amaru_network or "preview")
        public_network = _public_network(profile)
        testbed = _public_testbed(profile)
        peer_address = shlex.quote(profile.upstream_peer_address or "preview-node.play.dev.cardano.org:3001")
        listen_address = shlex.quote(profile.listen_address or "127.0.0.1:39000")
        return f"""set -e
runtime={runtime}
session={session}
amaru_bin={amaru_bin}
amaru_network={amaru_network}
peer_address={peer_address}
listen_address={listen_address}
state_root="$runtime/amaru1"
chain_dir="$state_root/chain.$amaru_network.db"
ledger_dir="$state_root/ledger.$amaru_network.db"
log_dir="$runtime/logs/amaru1"
log_path="$log_dir/stdout.log"
bootstrap_stdout="$log_dir/bootstrap.stdout.log"
bootstrap_stderr="$log_dir/bootstrap.stderr.log"
metadata_path="$runtime/runtime.json"
pid_file="$state_root/amaru.pid"
if tmux has-session -t "$session" 2>/dev/null; then
  echo "Session already exists: $session" >&2
  exit 3
fi
if [ -e "$state_root" ]; then
  echo "Runtime assets already exist under: $state_root" >&2
  exit 4
fi
python3 - "$peer_address" <<'PY'
import socket
import sys
peer = sys.argv[1]
host, port_text = peer.rsplit(":", 1)
port = int(port_text)
with socket.create_connection((host, port), timeout=5):
    pass
PY
mkdir -p "$state_root" "$log_dir"
cat > "$metadata_path" <<JSON
{{
  "profile_id": "{profile.id}",
  "target_implementation": "amaru",
  "network": "{public_network}",
  "upstream_peer_address": "{profile.upstream_peer_address or 'preview-node.play.dev.cardano.org:3001'}",
  "listen_address": "{profile.listen_address or '127.0.0.1:39000'}",
  "session": "{profile.compose_project}",
  "binary": "/home/dwarf/amaru-verification/target/debug/amaru",
  "chain_dir": "$state_root/chain.{profile.amaru_network or 'preview'}.db",
  "ledger_dir": "$state_root/ledger.{profile.amaru_network or 'preview'}.db",
  "log_path": "$log_dir/stdout.log",
  "pid_file": "$state_root/amaru.pid",
  "testbed": "{testbed}"
}}
JSON
cd "$runtime"
"$amaru_bin" bootstrap --network "$amaru_network" --ledger-dir "$ledger_dir" --chain-dir "$chain_dir" >"$bootstrap_stdout" 2>"$bootstrap_stderr"
tmux new-session -d -s "$session" "bash -lc 'cd $runtime; export RUST_BACKTRACE=full; $amaru_bin run --network $amaru_network --peer-address $peer_address --listen-address $listen_address --ledger-dir $ledger_dir --chain-dir $chain_dir --pid-file $pid_file 2>&1 | tee -a $log_path'"
sleep 10
tmux ls
"""
    runtime = shlex.quote(profile.remote_runtime_root)
    project = shlex.quote(profile.compose_project)
    peer_sharing = _bool_text(profile.peer_sharing)
    compose_yaml = compose_template(profile)
    compose_heredoc = compose_yaml.rstrip("\n")
    return f"""set -e
runtime={runtime}
project={project}
source_path={shlex.quote(REMOTE_SOURCE_PATH)}
dockerfile={shlex.quote(REMOTE_DOCKERFILE_PATH)}
if docker compose --project-name "$project" ls --format '{{{{.Name}}}}' 2>/dev/null | grep -q "^$project$"; then
  echo "Compose project already up: $project" >&2
  exit 3
fi
if [ -e "$runtime/env" ]; then
  echo "Runtime env already exists: $runtime/env" >&2
  exit 4
fi
mkdir -p "$runtime/logs"
sha=$(git -C "$source_path" rev-parse --short HEAD 2>/dev/null || echo latest)
echo "CARDANO_NODE_SHA=$sha" > "$runtime/.env"
docker build --file "$dockerfile" --tag "dwarf/cardano-node:$sha" --tag "dwarf/cardano-node:latest" "$source_path"
docker run --rm \\
  --user "$(id -u):$(id -g)" \\
  -v "$runtime:/work" \\
  --entrypoint /usr/local/bin/cardano-testnet \\
  "dwarf/cardano-node:$sha" \\
  create-env --output /work/env --num-pool-nodes {profile.node_count} --testnet-magic {profile.network_magic} --node-logging-format json
python3 - "$runtime/env/configuration.yaml" <<'PY'
import sys
path = sys.argv[1]
body = open(path, encoding="utf-8").read()
body = body.replace('"PeerSharing": true', '"PeerSharing": {peer_sharing}')
body = body.replace('"PeerSharing": false', '"PeerSharing": {peer_sharing}')
open(path, "w", encoding="utf-8").write(body)
PY
python3 - "$runtime/env" {profile.node_count} <<'PY'
import json, sys, os
env_dir = sys.argv[1]
node_count = int(sys.argv[2])
# Rewrite each node's topology.json to point at docker-network peers (node1..nodeN)
# instead of the host-loopback addresses that cardano-testnet generates by default.
for i in range(1, node_count + 1):
    topo_path = os.path.join(env_dir, "node-data", f"node{{i}}", "topology.json")
    if not os.path.exists(topo_path):
        continue
    data = json.load(open(topo_path))
    peers = [{{"address": f"node{{j}}", "port": 3001, "valency": 1}}
             for j in range(1, node_count + 1) if j != i]
    # Shape per cardano-node topology spec; keep the publicRoots/useLedgerAfterSlot keys
    # that were present in the original file if any.
    data["Producers" if "Producers" in data else "localRoots"] = peers
    json.dump(data, open(topo_path, "w"), indent=2)
PY
mkdir -p {{"$runtime/logs/node"}}1{{"$runtime/logs/node"}}2{{"$runtime/logs/node"}}3 2>/dev/null || true
for i in $(seq 1 {profile.node_count}); do mkdir -p "$runtime/logs/node$i"; done
cat > "$runtime/docker-compose.yml" <<'COMPOSE_EOF'
{compose_heredoc}
COMPOSE_EOF
docker compose --project-directory "$runtime" --project-name "$project" up -d
docker compose --project-directory "$runtime" --project-name "$project" ps
"""


def remove_command(remote_base_path):
    base = shlex.quote(remote_base_path)
    return f"""set -e
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
base_path={base}
archive_root="$base_path/archive"
mkdir -p "$archive_root"
# Stop and remove every Dwarf-managed compose project.
docker ps --filter 'label=ada2.managed=dwarf' --format '{{{{.Label "com.docker.compose.project"}}}}' 2>/dev/null | sort -u | while read -r project; do
  [ -n "$project" ] || continue
  container_id=$(docker ps -aq --filter "label=com.docker.compose.project=$project" | head -n 1)
  config_path=""
  if [ -n "$container_id" ]; then
    config_path=$(docker inspect --format '{{{{ index .Config.Labels "com.docker.compose.project.config_files" }}}}' "$container_id" 2>/dev/null || true)
    config_path=${{config_path%%,*}}
  fi
  if [ -n "$config_path" ] && [ -f "$config_path" ]; then
    docker compose -f "$config_path" --project-name "$project" down --volumes --remove-orphans 2>/dev/null || true
  else
    docker compose --project-name "$project" down --volumes --remove-orphans 2>/dev/null || true
  fi
done
# Kill stragglers (direct docker containers not in a compose project)
docker ps --filter 'label=ada2.managed=dwarf' --format '{{{{.ID}}}}' 2>/dev/null | xargs -r docker rm -f 2>/dev/null || true
# Stop host-process (tmux) devnets — generated-local profiles run cardano-node
# inside tmux sessions named dwarf-profile-<id>-node<N>, not docker containers.
# Scoped strictly to the dwarf-profile- prefix so unrelated tmux is untouched.
tmux ls 2>/dev/null | grep -oE '^dwarf-profile-[^:]+' | while read -r sess; do
  tmux kill-session -t "$sess" 2>/dev/null || true
done
# Archive every profile runtime regardless of the operator-chosen profile id.
find "$base_path" -mindepth 1 -maxdepth 1 -type d ! -name archive -print0 | while IFS= read -r -d '' path; do
  name=$(basename "$path")
  mv "$path" "$archive_root/${{name}}-$timestamp"
done
docker ps --filter 'label=ada2.managed=dwarf' --format 'table {{{{.Names}}}}' 2>/dev/null || true
"""
