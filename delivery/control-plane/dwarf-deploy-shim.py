#!/usr/bin/env python3
"""DWARF substrate control-channel shim (forced-command target).

This is the host-side half of the web-driven substrate control channel. It is
referenced from a restricted `authorized_keys` entry as:

    command="/path/to/dwarf-deploy-shim",no-pty,no-port-forwarding,... <pubkey>

so that a client presenting the paired key can ONLY invoke a fixed set of
whitelisted DWARF verbs — never an arbitrary shell command. sshd hands us the
client's requested command in $SSH_ORIGINAL_COMMAND; we parse it as
`<verb> [arg] [--dry-run]`, validate it hard, then generate the real command
via DWARF's own `profiles` module and run it locally on this (the deploy) host.

Because generation lives in DWARF (single source of truth) and only the verb
crosses the wire, a compromised dashboard cannot smuggle shell through this key:
anything that is not an allowed verb + well-formed profile id is rejected before
any command is built.

Config is read from `dwarf-control.conf` next to this script:
    DWARF_ROOT=/abs/path/to/<checkout>/dwarf
    CONFIG_PATH=/abs/path/to/runtime/state/config.yaml
    REMOTE_BASE_PATH=/home/<user>/cardano-profiles
    AUDIT_LOG=/abs/path/to/dwarf-control.log   # optional
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SHIM_DIR = Path(__file__).resolve().parent
CONF_PATH = SHIM_DIR / "dwarf-control.conf"

# Verbs the key is permitted to invoke. Everything else is rejected.
READ_VERBS = {
    "status",
    "active",
    "moog-health",
    "moog-facts",
    "topology-health",
}
WRITE_VERBS = {
    "deploy",
    "remove",
    "coverage",
    "smoke",
    "scenario",
    "moog-create-test",
    "topology-redeploy",
}
ALLOWED_VERBS = READ_VERBS | WRITE_VERBS

# A profile id / view token: starts alnum, then alnum/-/_ , bounded length.
_ARG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_GITHUB_USER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_DIRECTORY_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")
_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]*$")


def _load_conf() -> dict:
    conf: dict[str, str] = {}
    if CONF_PATH.exists():
        for raw in CONF_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            conf[key.strip()] = val.strip()
    return conf


def _audit(conf: dict, original: str, decision: str) -> None:
    log_path = conf.get("AUDIT_LOG")
    if not log_path:
        return
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    src = os.environ.get("SSH_CONNECTION", "?").split(" ")[0]
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{ts}\t{src}\t{decision}\t{original!r}\n")
    except OSError:
        pass


def _reject(conf: dict, original: str, reason: str) -> int:
    _audit(conf, original, f"REJECT:{reason}")
    sys.stderr.write(f"dwarf-deploy-shim: refused ({reason})\n")
    return 77  # EX_NOPERM


def _run_script(script: str, env: dict[str, str] | None = None) -> int:
    proc = subprocess.run(["bash", "-c", script], text=True, env=env)
    return proc.returncode


def _safe_directory(value: str) -> bool:
    if not _DIRECTORY_RE.fullmatch(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def main() -> int:
    conf = _load_conf()
    original = os.environ.get("SSH_ORIGINAL_COMMAND", "").strip()

    dwarf_root = conf.get("DWARF_ROOT")
    if not dwarf_root or not Path(dwarf_root).is_dir():
        return _reject(conf, original, "misconfigured-dwarf-root")
    if str(dwarf_root) not in sys.path:
        sys.path.insert(0, str(dwarf_root))

    # Point DWARF's profile loader + runtime-root normalizer at the same roots
    # the dashboard uses, BEFORE importing profiles. PROFILES_DIR = the writable
    # overlay (so GUI-created profiles are visible to the generator);
    # REMOTE_BASE_PATH = the single runtime-root base (deploy == remove).
    if conf.get("PROFILES_DIR"):
        os.environ["ADA2_DWARF_PROFILES_DIR"] = conf["PROFILES_DIR"]
    if conf.get("REMOTE_BASE_PATH"):
        os.environ["ADA2_DWARF_REMOTE_BASE"] = conf["REMOTE_BASE_PATH"]
    if conf.get("CONFIG_PATH"):
        os.environ["ADA2_PROFILE_MANAGER_CONFIG"] = conf["CONFIG_PATH"]
    for env_name, conf_name in (
        ("ADA2_DWARF_RUNS_DIR", "RUNS_DIR"),
        ("ADA2_DWARF_STATE_DIR", "STATE_DIR"),
        ("ADA2_DWARF_BUNDLES_DIR", "BUNDLES_DIR"),
        ("ADA2_DWARF_SCENARIOS_DIR", "SCENARIOS_DIR"),
    ):
        if conf.get(conf_name):
            os.environ[env_name] = conf[conf_name]

    if not original:
        return _reject(conf, original, "empty-command")

    # Parse strictly: verb, then at most two further tokens (arg, --dry-run).
    try:
        tokens = shlex.split(original)
    except ValueError:
        return _reject(conf, original, "unparseable")
    if not tokens:
        return _reject(conf, original, "bad-token-count")

    verb = tokens[0]
    launch = None
    run_env = None
    if verb == "moog-create-test":
        dry_run = tokens[-1] == "--dry-run"
        core = tokens[:-1] if dry_run else tokens
        if len(core) != 8:
            return _reject(conf, original, "bad-token-count")
        _, repo, github_user, directory, commit, try_number, duration_hours, fault_mode = core
        if not _REPO_RE.fullmatch(repo):
            return _reject(conf, original, "bad-repo")
        if not _GITHUB_USER_RE.fullmatch(github_user):
            return _reject(conf, original, "bad-github-user")
        if not _safe_directory(directory):
            return _reject(conf, original, "bad-directory")
        if not _COMMIT_RE.fullmatch(commit):
            return _reject(conf, original, "bad-commit")
        if not _POSITIVE_INT_RE.fullmatch(try_number):
            return _reject(conf, original, "bad-try")
        if not _POSITIVE_INT_RE.fullmatch(duration_hours):
            return _reject(conf, original, "bad-duration")
        if fault_mode not in {"faults", "no-faults"}:
            return _reject(conf, original, "bad-fault-mode")
        launch = {
            "repo": repo,
            "github_user": github_user,
            "directory": directory,
            "commit": commit.lower(),
            "try_number": int(try_number),
            "duration_hours": int(duration_hours),
            "no_faults": fault_mode == "no-faults",
        }
        rest = []
        arg = None
    else:
        if len(tokens) > 3:
            return _reject(conf, original, "bad-token-count")
        rest = tokens[1:]
        dry_run = False
        if "--dry-run" in rest:
            dry_run = True
            rest = [t for t in rest if t != "--dry-run"]
        arg = rest[0] if rest else None

    if verb not in ALLOWED_VERBS:
        return _reject(conf, original, "verb-not-allowed")
    if arg is not None and not _ARG_RE.match(arg):
        return _reject(conf, original, "bad-arg")
    if verb in {"moog-health", "moog-facts"} and arg is not None:
        return _reject(conf, original, f"{verb}-does-not-accept-arg")
    if verb == "topology-health" and arg is not None:
        return _reject(conf, original, "topology-health-does-not-accept-arg")
    if verb == "topology-redeploy" and arg != "cardano_amaru":
        return _reject(conf, original, "unsupported-topology")

    try:
        from profile_manager.profiles import (
            active_profile_command,
            deploy_command,
            find_profile,
            remove_command,
            status_command,
        )
        from profile_manager.config import load_config
        from profile_manager.moog import (
            build_moog_create_test_command,
            build_moog_facts_command,
            build_moog_health_command,
            normalize_moog_config,
        )
        from profile_manager.smoke import find_smoke_test, smoke_remote_command
    except Exception as exc:  # import surface is host-controlled, not client
        return _reject(conf, original, f"import-failed:{type(exc).__name__}")

    # Build the real command from the verb. Generation is DWARF's, not the
    # client's — the client only chose which verb+profile.
    if verb == "moog-create-test":
        try:
            config = load_config()
            moog_config = normalize_moog_config(config.moog)
            script = build_moog_create_test_command(moog_config, **launch)
            if not dry_run:
                passphrase_path = Path(moog_config["secrets_root"]) / "requester" / "wallet.passphrase"
                passphrase = passphrase_path.read_text(encoding="utf-8").strip()
                github_pat = str(moog_config.get("github_pat") or os.environ.get("MOOG_GITHUB_PAT") or "")
                if not passphrase:
                    return _reject(conf, original, "missing-wallet-passphrase")
                if not github_pat:
                    return _reject(conf, original, "missing-github-pat")
                run_env = dict(os.environ)
                run_env["MOOG_WALLET_PASSPHRASE"] = passphrase
                run_env["MOOG_GITHUB_PAT"] = github_pat
        except Exception as exc:
            return _reject(conf, original, f"moog-create-test-config-failed:{type(exc).__name__}")
    elif verb == "status":
        script = status_command()
    elif verb == "active":
        script = active_profile_command()
    elif verb == "moog-health":
        try:
            config = load_config()
            script = build_moog_health_command(normalize_moog_config(config.moog))
        except Exception as exc:
            return _reject(conf, original, f"moog-health-config-failed:{type(exc).__name__}")
    elif verb == "moog-facts":
        try:
            config = load_config()
            script = build_moog_facts_command(normalize_moog_config(config.moog))
        except Exception as exc:
            return _reject(conf, original, f"moog-facts-config-failed:{type(exc).__name__}")
    elif verb == "topology-health":
        state_dir = conf.get("STATE_DIR")
        if not state_dir:
            return _reject(conf, original, "missing-state-dir")
        output = Path(state_dir) / "topology-health" / "latest.json"
        probe = Path(dwarf_root) / "scripts" / "check_cardano_amaru_topology.py"
        script = (
            f"cd {shlex.quote(str(dwarf_root))} && "
            f"PYTHONPATH=. python3 {shlex.quote(str(probe))} "
            "--topology cardano_amaru "
            "--project cardano_amaru_relay_bootstrap_control "
            "--sample-seconds 10 "
            f"--output {shlex.quote(str(output))}"
        )
    elif verb == "topology-redeploy":
        state_dir = conf.get("STATE_DIR")
        if not state_dir:
            return _reject(conf, original, "missing-state-dir")
        package_dir = conf.get("TOPOLOGY_PACKAGE_DIR") or str(
            Path(dwarf_root).parent
            / "antithesis"
            / "cardano_amaru_relay_bootstrap_control"
        )
        repair = Path(dwarf_root) / "scripts" / "redeploy_cardano_amaru_topology.py"
        script = (
            f"cd {shlex.quote(str(dwarf_root))} && "
            f"ADA2_DWARF_STATE_DIR={shlex.quote(str(state_dir))} "
            f"ADA2_DWARF_TOPOLOGY_PACKAGE_DIR={shlex.quote(package_dir)} "
            f"PYTHONPATH=. python3 {shlex.quote(str(repair))} "
            "--topology cardano_amaru --confirm"
        )
    elif verb == "deploy":
        if not arg:
            return _reject(conf, original, "deploy-requires-profile")
        try:
            profile = find_profile(arg)
        except KeyError:
            return _reject(conf, original, "unknown-profile")
        script = deploy_command(profile)
    elif verb == "remove":
        base = conf.get("REMOTE_BASE_PATH")
        if not base:
            return _reject(conf, original, "missing-remote-base-path")
        script = remove_command(base)
    elif verb == "coverage":
        # Run an AFL coverage scenario on the HOST (the hardened dashboard
        # container can't run AFL's forkserver). arg is a scenario id; we run
        # DWARF's own CLI locally with the provisioned harness env exported.
        if not arg:
            return _reject(conf, original, "coverage-requires-scenario")
        scen_dir = conf.get("SCENARIOS_DIR")
        if not scen_dir:
            return _reject(conf, original, "missing-scenarios-dir")
        scen_path = Path(scen_dir) / f"{arg}.yaml"
        if not scen_path.exists():
            return _reject(conf, original, "unknown-scenario")
        harness = conf.get("AFL_HARNESS", "/opt/dwarf/afl-harness/dwarf-decode-any")
        aflfuzz = conf.get("AFL_FUZZ", "/opt/dwarf/afl-harness/afl-fuzz")
        script = (
            f"cd {shlex.quote(str(dwarf_root))} && "
            f"PYTHONPATH=. "
            f"DWARF_AFL_HARNESS={shlex.quote(harness)} "
            f"DWARF_AFL_FUZZ={shlex.quote(aflfuzz)} "
            f"python3 cardano-profile scenario run {shlex.quote(str(scen_path))}"
        )
    elif verb == "scenario":
        if not arg:
            return _reject(conf, original, "scenario-requires-id")
        scen_dir = conf.get("SCENARIOS_DIR")
        if not scen_dir:
            return _reject(conf, original, "missing-scenarios-dir")
        scen_path = Path(scen_dir) / f"{arg}.yaml"
        if not scen_path.is_file():
            return _reject(conf, original, "unknown-scenario")
        required_runtime_paths = {
            "ADA2_DWARF_RUNS_DIR": conf.get("RUNS_DIR"),
            "ADA2_DWARF_STATE_DIR": conf.get("STATE_DIR"),
            "ADA2_DWARF_BUNDLES_DIR": conf.get("BUNDLES_DIR"),
            "ADA2_DWARF_SCENARIOS_DIR": scen_dir,
        }
        missing = [name for name, value in required_runtime_paths.items() if not value]
        if missing:
            return _reject(conf, original, f"missing-runtime-path:{missing[0]}")
        runtime_env = " ".join(
            f"{name}={shlex.quote(str(value))}"
            for name, value in required_runtime_paths.items()
        )
        script = (
            f"cd {shlex.quote(str(dwarf_root))} && "
            f"{runtime_env} PYTHONPATH=. "
            f"python3 cardano-profile scenario run {shlex.quote(str(scen_path))}"
        )
    elif verb == "smoke":
        if not arg:
            return _reject(conf, original, "smoke-requires-test")
        try:
            smoke = find_smoke_test(arg)
        except KeyError:
            return _reject(conf, original, "unknown-smoke-test")
        script = smoke_remote_command(smoke)
    else:  # unreachable — guarded above
        return _reject(conf, original, "verb-not-allowed")

    if dry_run:
        _audit(conf, original, "DRYRUN")
        sys.stdout.write(script if script.endswith("\n") else script + "\n")
        return 0

    _audit(conf, original, "RUN")
    return _run_script(script, env=run_env)


if __name__ == "__main__":
    raise SystemExit(main())
