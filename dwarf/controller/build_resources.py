"""Resource limits for DWARF build containers.

The defaults are deliberately conservative so a shared host is never starved
by a node compile. A host dedicated to DWARF raises them through
``DWARF_BUILD_CPUS`` and ``DWARF_BUILD_MEMORY`` (documented in INSTALL.md);
the values travel from ``.env`` through the compose file into the controller.
Invalid values fall back to the defaults, and the CPU count never exceeds what
the host reports. The same limits drive the docker ``run`` flags, the Amaru
``CARGO_BUILD_JOBS`` and the Cardano toolchain parallelism, so one knob moves
every stage of a build together.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping

DEFAULT_CPUS = 2
DEFAULT_MEMORY = "8g"
DEFAULT_PIDS_LIMIT = 512
_PIDS_PER_CPU = 256
_CPUS = re.compile(r"[1-9][0-9]*")
_MEMORY = re.compile(r"[1-9][0-9]*[kmg]")


def build_resources(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Effective build limits: ``cpus``, ``memory``, ``pids_limit`` and the
    compiler ``jobs`` count (equal to ``cpus``)."""
    env = os.environ if environ is None else environ
    cpus = DEFAULT_CPUS
    raw_cpus = (env.get("DWARF_BUILD_CPUS") or "").strip()
    if _CPUS.fullmatch(raw_cpus):
        cpus = int(raw_cpus)
        host = os.cpu_count()
        if host:
            cpus = min(cpus, host)
    memory = DEFAULT_MEMORY
    raw_memory = (env.get("DWARF_BUILD_MEMORY") or "").strip().lower()
    if _MEMORY.fullmatch(raw_memory):
        memory = raw_memory
    pids_limit = max(DEFAULT_PIDS_LIMIT, cpus * _PIDS_PER_CPU)
    return {"cpus": cpus, "memory": memory, "pids_limit": pids_limit, "jobs": cpus}


def docker_limit_arguments(limits: Mapping[str, Any]) -> list[str]:
    """The ``docker run`` flags for a build container under ``limits``."""
    return [
        "--cpus", str(limits["cpus"]),
        "--memory", str(limits["memory"]),
        "--pids-limit", str(limits["pids_limit"]),
    ]
