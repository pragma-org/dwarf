"""Run-scoped external resource tap for the actual Cardano-node process."""
from __future__ import annotations

from profile_manager.measurement_collectors.amaru_resources import AmaruResourceCollector


class CardanoResourceCollector(AmaruResourceCollector):
    """Use the shared Linux process/cgroup sampler with Cardano target identity."""
