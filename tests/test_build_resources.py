"""Build-container resource limits: conservative defaults for a shared host,
operator-raised limits for a host dedicated to DWARF."""
import os

import pytest

from controller import build_resources as resources


def test_defaults_keep_shared_host_limits(monkeypatch):
    monkeypatch.delenv('DWARF_BUILD_CPUS', raising=False)
    monkeypatch.delenv('DWARF_BUILD_MEMORY', raising=False)
    limits = resources.build_resources()
    assert limits == {'cpus': 2, 'memory': '8g', 'pids_limit': 512, 'jobs': 2}
    assert resources.docker_limit_arguments(limits) == ['--cpus', '2', '--memory', '8g', '--pids-limit', '512']


def test_dedicated_host_limits_follow_environment(monkeypatch):
    monkeypatch.setattr(os, 'cpu_count', lambda: 16)
    monkeypatch.setenv('DWARF_BUILD_CPUS', '16')
    monkeypatch.setenv('DWARF_BUILD_MEMORY', '20g')
    limits = resources.build_resources()
    assert limits == {'cpus': 16, 'memory': '20g', 'pids_limit': 4096, 'jobs': 16}
    assert resources.docker_limit_arguments(limits) == ['--cpus', '16', '--memory', '20g', '--pids-limit', '4096']


def test_cpus_are_capped_to_the_host(monkeypatch):
    monkeypatch.setattr(os, 'cpu_count', lambda: 8)
    monkeypatch.setenv('DWARF_BUILD_CPUS', '64')
    monkeypatch.delenv('DWARF_BUILD_MEMORY', raising=False)
    assert resources.build_resources()['cpus'] == 8


@pytest.mark.parametrize('cpus,memory', [('0', '8g'), ('-3', '8g'), ('lots', '8g'), ('2.5', '8g'), ('2', '20x'), ('2', ''), ('2', '20')])
def test_invalid_values_fall_back_to_defaults(monkeypatch, cpus, memory):
    monkeypatch.setenv('DWARF_BUILD_CPUS', cpus)
    monkeypatch.setenv('DWARF_BUILD_MEMORY', memory)
    limits = resources.build_resources()
    assert (limits['cpus'], limits['memory'], limits['pids_limit'], limits['jobs']) == (2, '8g', 512, 2)
