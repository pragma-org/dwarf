import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from profile_manager.profile_shapes import shape_from_profile_dict


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


# Default locations on the remote build host.
REMOTE_SOURCE_PATH = "/home/dwarf/cardano-node"
REMOTE_DOCKERFILE_PATH = "/home/dwarf/dwarf-fw/devnet-build/cardano-node.Dockerfile"


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
    measurement_patch_revision: str | None = None
    measurement_patch_set_sha256: str | None = None
    amaru_json_traces: bool = False
    cardano_measurement_traces: bool = False

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
            measurement_patch_revision=data.get("measurement_patch_revision"),
            measurement_patch_set_sha256=data.get("measurement_patch_set_sha256"),
            amaru_json_traces=bool(data.get("amaru_json_traces", False)),
            cardano_measurement_traces=bool(
                data.get("cardano_measurement_traces", False)
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
        "measurement_patch_revision",
        "measurement_patch_set_sha256",
        "amaru_json_traces",
        "cardano_measurement_traces",
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
    return r"""docker ps --filter 'label=ada2.managed=dwarf' --format 'DWARF_NODE docker {{.Names}} {{.Status}}' 2>/dev/null || true
tmux ls 2>/dev/null | grep -oE '^dwarf-profile-[^:]+' | sed 's/^/DWARF_NODE tmux /' || true"""


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
    return {
        "id": node_id,
        "impl": release["implementation"],
        "version": release["version"],
        "role": role,
        "image": _exact_oci_reference(release),
        "source_revision": release["source_revision"],
        "supporting": supporting,
        "target_mode": "stock",
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
        if profile.amaru_node_count < 1:
            raise ValueError("patched Amaru measurement target requires at least one Amaru node")
        from profile_manager.measurement_targets import resolve_patched_amaru_target

        target = resolve_patched_amaru_target(profile)
        for node in nodes:
            if node["impl"] != "amaru":
                continue
            node.update(
                {
                    "image": target["image_reference"],
                    "image_digest": target["image_digest"],
                    "executable_digest": target["executable_digest"],
                    "target_mode": "patched",
                    "patch_set_sha256": target["patch_set_sha256"],
                    "build_result_sha256": target["build_result_sha256"],
                    "runtime_probe_image": target["runtime_probe_image"],
                    "runtime_probe_log_sha256": target[
                        "runtime_probe_log_sha256"
                    ],
                }
            )

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
    return {
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
        "network_magic": (
            None
            if deployment_adapter in {"cardano-public-peer", "amaru-public-peer"}
            else profile.network_magic
        ),
        "config_source_dir": profile.config_source_dir,
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
        "nodes": nodes,
        "topology": {"edges": edges},
    }


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
                    else None
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
                    )
                    if key in amaru
                },
                "healthy_timeout_seconds": 1800,
            }
        )
    config_json = json.dumps(config_body, indent=2, sort_keys=True)
    image_refs = sorted(
        {node["image"] for node in substrate["nodes"] if node["target_mode"] == "stock"}
    )
    image_refs.extend(
        node["runtime_probe_image"]
        for node in substrate["nodes"]
        if node["target_mode"] == "patched"
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
        probe_image = shlex.quote(node["runtime_probe_image"])
        local_image_checks.append(
            f'''actual_id=$(docker image inspect --format '{{{{.Id}}}}' {image} 2>/dev/null || true)
if [ "$actual_id" != {expected_id} ]; then
  echo "Patched Amaru image is missing or has the wrong image id: {node['image']}" >&2
  exit 8
fi
actual_revision=$(docker image inspect --format '{{{{index .Config.Labels "org.opencontainers.image.revision"}}}}' {image})
actual_patch=$(docker image inspect --format '{{{{index .Config.Labels "org.dwarf.measurement.patch-sha256"}}}}' {image})
if [ "$actual_revision" != {revision} ] || [ "$actual_patch" != {patch_set} ]; then
  echo "Patched Amaru image labels do not match the audited target identity" >&2
  exit 9
fi
probe_dir=$(mktemp -d)
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
        )
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
