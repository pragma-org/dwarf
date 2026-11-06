import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    rendered_command: str


def control_shim_enabled() -> bool:
    """Whether the deploy host runs the restricted forced-command control shim.

    When enabled, deploy/remove/status/active are sent to the host as bare
    verbs (e.g. ``deploy <profile-id>``) instead of full generated scripts —
    the host-side shim regenerates and runs the real command. This is what
    lets a locked-down dashboard container drive substrate lifecycle without
    holding a key that can run arbitrary shell.
    """
    return os.environ.get("ADA2_DWARF_CONTROL_SHIM", "").strip().lower() in ("1", "true", "yes")


def render_ssh_command(config, remote_command, verb=None):
    target = f"{config.ssh_user}@{config.host}"
    ssh_key_path = resolve_ssh_key_path(config)
    # In shim mode, send the verb tokens; the host shim regenerates the real
    # command. `remote_command` is still used for the non-shim path and for
    # dry-run rendering, so callers always pass it.
    wire_command = remote_command
    if verb and control_shim_enabled():
        wire_command = " ".join(shlex.quote(part) for part in verb)
    return [
        "ssh",
        "-n",
        "-o",
        "BatchMode=yes",
        "-i",
        ssh_key_path,
        target,
        wire_command,
    ]


def render_topology_health_command(config) -> list[str]:
    """Render the fixed read-only mixed-topology health command."""
    return render_ssh_command(
        config,
        "topology-health",
        verb=("topology-health",),
    )


def render_topology_redeploy_command(
    config, *, topology_id: str = "cardano_amaru"
) -> list[str]:
    """Render the fixed evidence-preserving topology repair command."""
    if topology_id != "cardano_amaru":
        raise ValueError(f"unsupported topology: {topology_id}")
    return render_ssh_command(
        config,
        f"topology-redeploy {topology_id}",
        verb=("topology-redeploy", topology_id),
    )


def render_launch_command(config, launch_id: str) -> list[str]:
    """Render the identifier-only command accepted by the forced-command shim."""
    from profile_manager.launch_store import validate_launch_id

    checked = validate_launch_id(launch_id)
    return render_ssh_command(
        config,
        f"launch {checked}",
        verb=("launch", checked),
    )


def render_launch_preflight_command(config, launch_id: str) -> list[str]:
    """Render the read-only exact-runtime check for one stored launch."""
    from profile_manager.launch_store import validate_launch_id

    checked = validate_launch_id(launch_id)
    return render_ssh_command(
        config,
        f"launch-preflight {checked}",
        verb=("launch-preflight", checked),
    )


def resolve_ssh_key_path(config) -> str:
    configured = Path(config.ssh_key_path).expanduser()
    if configured.exists():
        return str(configured)

    parts = configured.parts
    if (
        len(parts) >= 5
        and parts[0:2] == ("/", "home")
        and parts[-2] == ".ssh"
    ):
        candidates = [
            Path.home() / ".ssh" / configured.name,
            Path("/home/dwarf/.ssh") / configured.name,
            Path("/home") / str(config.ssh_user) / ".ssh" / configured.name,
            Path("/Users") / str(config.ssh_user) / ".ssh" / configured.name,
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)

    return str(configured)


def shell_join(argv):
    return " ".join(shlex.quote(part) for part in argv)


def run_moog_create_test(
    config,
    moog_config,
    command,
    timeout=900,
    *,
    repo,
    github_user,
    directory,
    commit,
    try_number,
    duration_hours,
    no_faults,
):
    """Run a prebuilt `moog requester create-test` command on the remote host,
    injecting the wallet passphrase + GitHub PAT from their on-host files at
    runtime (never logged). `config` is the deployment config (ssh target);
    `command` is from moog.build_moog_create_test_command."""
    from profile_manager.moog import normalize_moog_config

    cfg = normalize_moog_config(moog_config)
    if control_shim_enabled():
        verb = (
            "moog-create-test",
            str(repo),
            str(github_user),
            str(directory),
            str(commit),
            str(try_number),
            str(duration_hours),
            "no-faults" if no_faults else "faults",
        )
        return ssh_command(config, command, timeout=timeout, verb=verb)

    secrets_root = cfg["secrets_root"]
    host_config = "/home/dwarf/dwarf-v4/var/state/config.yaml"
    script = (
        "set -uo pipefail\n"
        f'export MOOG_WALLET_PASSPHRASE="$(cat {secrets_root}/requester/wallet.passphrase)"\n'
        "export MOOG_GITHUB_PAT=\"$(python3 -c 'import json;"
        f"print(json.load(open(\"{host_config}\"))[\"moog\"][\"github_pat\"])')\"\n"
        f"{command}\n"
    )
    return ssh_command(config, script, timeout=timeout)


def fetch_test_run_facts(config, moog_config, test_run_id, timeout=60):
    """Return parsed JSON from `moog facts test-runs --test-run-id <id>` on the host."""
    import json

    from profile_manager.moog import normalize_moog_config

    cfg = normalize_moog_config(moog_config)
    command = (
        f"MOOG_MPFS_HOST={shlex.quote(cfg['mpfs_host'])} "
        f"MOOG_TOKEN_ID={shlex.quote(cfg['token_id'])} "
        f"{shlex.quote(cfg['moog_binary'])} facts test-runs --test-run-id {shlex.quote(test_run_id)}"
    )
    result = ssh_command(config, command, timeout=timeout)
    try:
        return json.loads(result.stdout) if result.stdout.strip() else []
    except (ValueError, AttributeError):
        return []


_ANTITHESIS_SUBCOMMANDS = frozenset({"runs", "run", "properties", "events", "logs", "build-logs"})
_ANTITHESIS_NDJSON = frozenset({"events", "logs", "build-logs"})
_ANTITHESIS_EXIT = {2: "authorization or SSO failure", 3: "proxy or network failure", 4: "invalid JSON from proxy"}


def fetch_antithesis(
    config, moog_config, subcommand, *, run_id=None, limit=None, cursor=None, query=None,
    input_hash=None, vtime=None, timeout=60,
):
    """Read Antithesis run data through the MOOG proxy (`moog antithesis …`) on the host.

    No MOOG_* environment is passed: the host's OAuth file authorises the proxy.
    Returns {'ok', 'payload', 'exit_code', 'error'}; `payload` is the parsed JSON
    (a list of objects for the NDJSON subcommands) and `error` is a bounded,
    DWARF-authored text — proxy output is never echoed into it."""
    import json

    from profile_manager.moog import normalize_moog_config

    if subcommand not in _ANTITHESIS_SUBCOMMANDS:
        raise ValueError(f"unsupported moog antithesis subcommand: {subcommand}")
    cfg = normalize_moog_config(moog_config)
    argv = [cfg["moog_binary"], "antithesis", subcommand]
    for flag, value in (("--run-id", run_id), ("--limit", limit), ("--cursor", cursor), ("--q", query),
                        ("--input-hash", input_hash), ("--vtime", vtime)):
        if value is not None:
            argv += [flag, str(value)]
    result = ssh_command(config, " ".join(shlex.quote(part) for part in argv), timeout=timeout)

    def failure(error):
        return {"ok": False, "payload": None, "exit_code": result.returncode, "error": str(error)[:200]}

    if result.returncode != 0:
        label = _ANTITHESIS_EXIT.get(result.returncode, "moog antithesis failed")
        return failure(f"{label} (exit {result.returncode})")
    text = result.stdout or ""
    if subcommand in _ANTITHESIS_NDJSON:
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except ValueError as exc:
                return failure(f"invalid NDJSON: {exc}")
            if not isinstance(parsed, dict):
                return failure("invalid NDJSON: row is not an object")
            rows.append(parsed)
        return {"ok": True, "payload": rows, "exit_code": 0, "error": ""}
    try:
        parsed = json.loads(text)
    except ValueError as exc:
        return failure(f"invalid JSON: {exc}")
    return {"ok": True, "payload": parsed, "exit_code": 0, "error": ""}


def find_antithesis_run(config, moog_config, test_run_id, *, max_pages=5, limit=50, timeout=60):
    """Find the Antithesis run launched for one MOOG test run.

    The proxy's run rows carry MOOG's launch document in `description`; its
    `testRunId` is the join key. Exactly one match is 'found'; two or more are
    'ambiguous' (a retried launch) and never silently chosen."""
    import json

    cursor = None
    matches = []
    pages = 0
    while pages < max_pages:
        out = fetch_antithesis(config, moog_config, "runs", limit=limit, cursor=cursor, timeout=timeout)
        pages += 1
        if not out["ok"]:
            return {"status": "error", "run": None, "matches": len(matches), "pages": pages, "error": out["error"]}
        payload = out["payload"] if isinstance(out["payload"], dict) else {}
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            try:
                description = json.loads(row.get("description") or "")
            except (ValueError, TypeError):
                continue
            if isinstance(description, dict) and description.get("testRunId") == test_run_id:
                matches.append(row)
        cursor = payload.get("next_cursor")
        if not cursor or len(matches) >= 2:
            break
    if len(matches) == 1:
        return {"status": "found", "run": matches[0], "matches": 1, "pages": pages, "error": ""}
    if matches:
        return {"status": "ambiguous", "run": None, "matches": len(matches), "pages": pages, "error": ""}
    return {"status": "not_found", "run": None, "matches": 0, "pages": pages, "error": ""}


def ssh_command(config, remote_command, timeout=None, dry_run=False, verb=None):
    argv = render_ssh_command(config, remote_command, verb=verb)
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        completed.returncode,
        completed.stdout,
        completed.stderr,
        rendered,
    )


def rsync_to(config, local_path, remote_path, dry_run=False):
    ssh_key_path = resolve_ssh_key_path(config)
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(ssh_key_path)}",
        str(local_path),
        f"{config.ssh_user}@{config.host}:{remote_path}",
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)


def rsync_from(config, remote_path, local_path, dry_run=False):
    ssh_key_path = resolve_ssh_key_path(config)
    argv = [
        "rsync",
        "-a",
        "-e",
        f"ssh -i {shlex.quote(ssh_key_path)}",
        f"{config.ssh_user}@{config.host}:{remote_path}",
        str(local_path),
    ]
    rendered = shell_join(argv)
    if dry_run:
        return CommandResult(0, rendered + "\n", "", rendered)
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, rendered)
