import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_amaru_plutus_adapter
from scripts import build_cardano_plutus_adapter


ROOT = Path(__file__).resolve().parents[1]
AMARU_REVISION = "b159172f25a9c389f82f20bca4f15e3032791638"
CARDANO_REVISION = "fef83fed01d7926f3de83b3b917be5a4a48768b5"


@pytest.mark.parametrize(
    ("builder", "implementation", "revision"),
    [
        (build_amaru_plutus_adapter, "amaru", AMARU_REVISION),
        (build_cardano_plutus_adapter, "cardano-node", CARDANO_REVISION),
    ],
)
def test_manifest_is_revision_locked_and_all_adapter_files_match(builder, implementation, revision):
    manifest = json.loads(builder.DEFAULT_MANIFEST.read_text())
    assert manifest["kind"] == "production-plutus-v2-conformance"
    assert manifest["implementation"] == implementation
    assert manifest["source"]["revision"] == revision
    assert manifest["measurement_boundary"] == "production-plutus-v2-vm-only"
    rows = []
    for item in manifest["adapter_files"]:
        path = builder.DEFAULT_MANIFEST.parent / item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        rows.append((item["path"], item["sha256"]))
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    assert hashlib.sha256(payload.encode()).hexdigest() == manifest["adapter_set_sha256"]


def test_amaru_adapter_uses_production_uplc_cost_model_and_monotonic_nanoseconds():
    root = build_amaru_plutus_adapter.DEFAULT_MANIFEST.parent
    source = (root / "plutus/src/main.rs").read_text()
    assert "flat::decode::<DeBruijn>" in source
    assert "CostModel::new(PlutusVersion::V2" in source
    assert "Instant::now()" in source
    assert "elapsed_nanos" in source
    assert "elapsed_micros" in source


def test_cardano_adapter_uses_production_v2_evaluator_and_monotonic_nanoseconds():
    root = build_cardano_plutus_adapter.DEFAULT_MANIFEST.parent
    source = (root / "plutus/src/Main.hs").read_text()
    assert "V2.deserialiseScript" in source
    assert "V2.mkEvaluationContext" in source
    assert "getMonotonicTimeNSec" in source
    assert "elapsed_nanos" in source
    assert "elapsed_micros" in source
