from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


CONFIG_ENV = "ADA2_PROFILE_MANAGER_CONFIG"
RUNTIME_ROOT_ENV = "ADA2_DWARF_RUNTIME_ROOT"
STORAGE_ENV = {
    "runs": "ADA2_DWARF_RUNS_DIR",
    "state": "ADA2_DWARF_STATE_DIR",
    "bundles": "ADA2_DWARF_BUNDLES_DIR",
}
DEFAULT_CONFIG = Path.home() / ".local" / "share" / "dwarf" / "state" / "config.yaml"


class StorageConfigurationError(ValueError):
    """Raised when configured storage could create a split evidence store."""


@dataclass(frozen=True)
class StorageRoots:
    """Immutable effective storage identity shared by every runtime adapter."""

    runs: Path
    state: Path
    bundles: Path
    runtime_root: Path | None
    mode: Literal["explicit", "configured", "runtime", "xdg", "development"]
    sources: dict[str, str]


def _path(value: str | os.PathLike[str]) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def resolve_storage_roots(
    *,
    runs_dir: str | os.PathLike[str] | None = None,
    state_dir: str | os.PathLike[str] | None = None,
    bundles_dir: str | os.PathLike[str] | None = None,
    runtime_root: str | os.PathLike[str] | None = None,
    development: bool = False,
    checkout_root: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> StorageRoots:
    """Resolve one storage contract with deterministic, fail-closed precedence.

    Explicit function arguments override configured paths component by component.
    Environment component overrides are accepted only as a complete trio; a
    partial trio is rejected even when a runtime root is also present. Checkout
    storage is available only through the explicit ``development`` argument.
    """

    env = os.environ if environ is None else environ
    configured = {
        key: str(env.get(name, "")).strip()
        for key, name in STORAGE_ENV.items()
    }
    populated = {key for key, value in configured.items() if value}
    overrides = {"runs": runs_dir, "state": state_dir, "bundles": bundles_dir}
    complete_explicit_trio = all(value is not None for value in overrides.values())
    explicit_runtime = runtime_root
    if (
        explicit_runtime is None
        and not complete_explicit_trio
        and populated
        and len(populated) != len(STORAGE_ENV)
    ):
        missing = sorted(set(STORAGE_ENV) - populated)
        raise StorageConfigurationError(
            "ADA2_DWARF_RUNS_DIR, ADA2_DWARF_STATE_DIR, and "
            "ADA2_DWARF_BUNDLES_DIR must be configured as all three; "
            f"missing: {', '.join(missing)}"
        )

    configured_runtime = str(env.get(RUNTIME_ROOT_ENV, "")).strip()
    if explicit_runtime is not None:
        base_root = _path(explicit_runtime)
        base_paths = {key: base_root / key for key in STORAGE_ENV}
        mode: Literal["explicit", "configured", "runtime", "xdg", "development"] = "explicit"
        base_source = "explicit-runtime"
    elif complete_explicit_trio:
        assert runs_dir is not None and state_dir is not None and bundles_dir is not None
        base_root = None
        base_paths = {
            "runs": _path(runs_dir),
            "state": _path(state_dir),
            "bundles": _path(bundles_dir),
        }
        mode = "explicit"
        base_source = "explicit"
    elif populated:
        base_paths = {key: _path(value) for key, value in configured.items()}
        base_root = None
        mode = "configured"
        base_source = "environment"
    elif configured_runtime:
        base_root = _path(configured_runtime)
        base_paths = {key: base_root / key for key in STORAGE_ENV}
        mode = "runtime"
        base_source = "runtime-root"
    elif development:
        if checkout_root is None:
            raise StorageConfigurationError(
                "checkout_root is required when development storage mode is enabled"
            )
        base_root = _path(checkout_root)
        base_paths = {key: base_root / key for key in STORAGE_ENV}
        mode = "development"
        base_source = "development"
    else:
        xdg = str(env.get("XDG_DATA_HOME", "")).strip()
        data_home = _path(xdg) if xdg else Path.home() / ".local" / "share"
        base_root = data_home / "dwarf"
        base_paths = {key: base_root / key for key in STORAGE_ENV}
        mode = "xdg"
        base_source = "xdg"

    sources = {key: base_source for key in STORAGE_ENV}
    for key, value in overrides.items():
        if value is not None:
            base_paths[key] = _path(value)
            sources[key] = "explicit"
            mode = "explicit"

    return StorageRoots(
        runs=base_paths["runs"],
        state=base_paths["state"],
        bundles=base_paths["bundles"],
        runtime_root=base_root,
        mode=mode,
        sources=sources,
    )


def storage_roots(**kwargs) -> StorageRoots:
    """Short canonical accessor for runtime consumers."""

    return resolve_storage_roots(**kwargs)

CONFIG_FIELDS = {
    "deployment_name": {"type": "string", "default": "dwarf-devnet", "description": "Human-readable deployment label."},
    "host": {"type": "string", "default": "127.0.0.1", "description": "Remote SSH host or IP."},
    "ssh_user": {"type": "string", "default": "dwarf", "description": "Remote SSH username."},
    "ssh_key_path": {"type": "string", "default": "~/.ssh/cardano-box", "description": "SSH private-key path used for remote commands."},
    "remote_base_path": {"type": "string", "default": "/opt/dwarf/cardano-profiles", "description": "Remote base directory for deployment artifacts."},
    "remote_dwarf_root": {"type": "string", "default": "", "description": "Installed DWARF Python source root on the deployment host (the directory containing scripts/)."},
    "allow_prereq_install": {"type": "boolean", "default": False, "description": "Allow prerequisite installation via CLI."},
    "allow_sudo": {"type": "boolean", "default": False, "description": "Allow sudo-backed remote commands."},
    "log_level": {"type": "string", "default": "info", "description": "Operator-facing CLI log level."},
    "output_format": {"type": "string", "default": "text", "description": "Default human output format for future export-capable commands."},
    "docker_registry": {"type": "string", "default": "", "description": "Default Docker registry prefix for framework images."},
    "runs_retention_days": {"type": "integer", "default": 0, "description": "Run retention in days; 0 keeps runs until manually removed."},
    "bundles_retention_days": {"type": "integer", "default": 0, "description": "Bundle retention in days; 0 keeps bundles until manually removed."},
    "auto_redeploy_unhealthy_topology": {"type": "boolean", "default": False, "description": "Before an attached scenario only, preserve evidence and attempt one redeploy when the mixed topology is unhealthy. Never applies to status-page views or active runs."},
    "sarif_rules": {"type": "array[string]", "default": [], "description": "Optional SARIF rule filters or preferred rule identifiers."},
    "wallets": {"type": "array[object]", "default": [], "description": "Public wallet metadata shown on dashboard status; never store secrets here."},
    "moog": {"type": "object", "default": {}, "description": "Moog deployment, GitHub, Antithesis, and requester setup values."},
}


@dataclass(frozen=True)
class DeploymentConfig:
    deployment_name: str
    host: str
    ssh_user: str
    ssh_key_path: str
    remote_base_path: str
    remote_dwarf_root: str = ""
    allow_prereq_install: bool = False
    allow_sudo: bool = False
    log_level: str = "info"
    output_format: str = "text"
    docker_registry: str = ""
    runs_retention_days: int = 0
    bundles_retention_days: int = 0
    auto_redeploy_unhealthy_topology: bool = False
    sarif_rules: list[str] | None = None
    wallets: list[dict[str, Any]] | None = None
    moog: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data):
        normalized = {}
        for key, meta in CONFIG_FIELDS.items():
            normalized[key] = data.get(key, meta["default"])
        return cls(
            deployment_name=normalized["deployment_name"],
            host=normalized["host"],
            ssh_user=normalized["ssh_user"],
            ssh_key_path=normalized["ssh_key_path"],
            remote_base_path=normalized["remote_base_path"],
            remote_dwarf_root=str(normalized["remote_dwarf_root"]),
            allow_prereq_install=bool(normalized["allow_prereq_install"]),
            allow_sudo=bool(normalized["allow_sudo"]),
            log_level=str(normalized["log_level"]),
            output_format=str(normalized["output_format"]),
            docker_registry=str(normalized["docker_registry"]),
            runs_retention_days=int(normalized["runs_retention_days"]),
            bundles_retention_days=int(normalized["bundles_retention_days"]),
            auto_redeploy_unhealthy_topology=bool(
                normalized["auto_redeploy_unhealthy_topology"]
            ),
            sarif_rules=list(normalized["sarif_rules"] or []),
            wallets=[dict(item) for item in (normalized["wallets"] or []) if isinstance(item, dict)],
            moog=dict(normalized["moog"] or {}) if isinstance(normalized["moog"], dict) else {},
        )

    def to_dict(self):
        return {
            "deployment_name": self.deployment_name,
            "host": self.host,
            "ssh_user": self.ssh_user,
            "ssh_key_path": self.ssh_key_path,
            "remote_base_path": self.remote_base_path,
            "remote_dwarf_root": self.remote_dwarf_root,
            "allow_prereq_install": self.allow_prereq_install,
            "allow_sudo": self.allow_sudo,
            "log_level": self.log_level,
            "output_format": self.output_format,
            "docker_registry": self.docker_registry,
            "runs_retention_days": self.runs_retention_days,
            "bundles_retention_days": self.bundles_retention_days,
            "auto_redeploy_unhealthy_topology": self.auto_redeploy_unhealthy_topology,
            "sarif_rules": list(self.sarif_rules or []),
            "wallets": [dict(item) for item in (self.wallets or [])],
            "moog": dict(self.moog or {}),
        }


def config_path(*, environ: Mapping[str, str] | None = None):
    env = os.environ if environ is None else environ
    override = env.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return resolve_storage_roots(environ=env).state / "config.yaml"


def config_exists():
    return config_path().exists()


def load_config():
    with config_path().open("r", encoding="utf-8") as handle:
        return DeploymentConfig.from_dict(json.load(handle))


def save_config(config):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def list_config_values(config: DeploymentConfig) -> dict[str, Any]:
    return config.to_dict()


def parse_config_value(key: str, raw: str) -> Any:
    if key not in CONFIG_FIELDS:
        raise KeyError(key)
    type_name = CONFIG_FIELDS[key]["type"]
    if type_name == "string":
        return raw
    if type_name == "boolean":
        value = raw.strip().lower()
        if value in {"true", "1", "yes", "y", "on"}:
            return True
        if value in {"false", "0", "no", "n", "off"}:
            return False
        raise ValueError(f"invalid boolean for {key}: {raw}")
    if type_name == "integer":
        return int(raw)
    if type_name == "array[string]":
        value = raw.strip()
        if not value:
            return []
        if value.startswith("["):
            parsed = json.loads(value)
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError(f"invalid array[string] for {key}")
            return parsed
        return [item.strip() for item in value.split(",") if item.strip()]
    if type_name == "array[object]":
        value = raw.strip()
        if not value:
            return []
        parsed = json.loads(value)
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            raise ValueError(f"invalid array[object] for {key}")
        return parsed
    if type_name == "object":
        value = raw.strip()
        if not value:
            return {}
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"invalid object for {key}")
        return parsed
    raise ValueError(f"unsupported config type for {key}: {type_name}")


def set_config_value(config: DeploymentConfig, key: str, raw: str) -> DeploymentConfig:
    values = config.to_dict()
    values[key] = parse_config_value(key, raw)
    return DeploymentConfig.from_dict(values)
