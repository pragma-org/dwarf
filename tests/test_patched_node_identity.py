"""F108: a patched measurement-target node is not mistaken for an operator-registered local image.

Patched nodes must carry executable_digest. The same field is part of the operator-registration
local identity, and supplying any field of that set demanded all of them, so every patched-target
profile failed to deploy ("local artifact identity is incomplete", live: profile-v).
"""
from __future__ import annotations

import pytest

from scripts.runtime_substrate_common import normalize_substrate


def _patched_node(**extra):
    return {
        "id": "node1", "impl": "cardano-node", "version": "11.1.2", "role": "producer",
        "image": "dwarf/cardano-measurement@sha256:" + "6" * 64,
        "source_revision": "fef83fed01d7926f3de83b3b917be5a4a48768b5",
        "target_mode": "patched", "patch_set_sha256": "1" * 64,
        "image_digest": "sha256:" + "6" * 64, "executable_digest": "sha256:" + "5" * 64,
        "build_result_sha256": "sha256:" + "4" * 64, "runtime_probe_log_sha256": "sha256:" + "3" * 64,
        **extra,
    }


def test_patched_node_with_its_own_identity_normalizes():
    normalized = normalize_substrate({"profile_id": "profile-v-cardano-measurement-nanoseconds-v2",
                                      "nodes": [_patched_node()], "topology": {"edges": []}})
    node = normalized["nodes"][0]
    assert node["target_mode"] == "patched" and node["executable_digest"] == "sha256:" + "5" * 64


def test_a_partial_operator_registration_identity_is_still_refused():
    with pytest.raises(ValueError, match="local artifact identity is incomplete"):
        normalize_substrate({"profile_id": "profile-n-cardano-latest-confirmed",
                             "nodes": [_patched_node(target_mode="stock", operator_registration_id="reg-1")],
                             "topology": {"edges": []}})
