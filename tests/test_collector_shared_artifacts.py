"""Collectors retain large raw inputs once per run, content-addressed, so a run whose
eight collectors read the same 29 MB node trace does not archive eight copies
(run 20261104T115918Z-d411df5a inflated to 418 MB; the viewer caps expansion at 256 MB)."""
import hashlib
from pathlib import Path

import pytest

from profile_manager.measurement_runtime import CollectorContext, MeasurementArtifactPathError


def _context(run_dir: Path, measurement_id: str) -> CollectorContext:
    collector_dir = run_dir / "measurements" / "collectors" / measurement_id
    collector_dir.mkdir(parents=True, exist_ok=True)
    return CollectorContext(measurement_id=measurement_id, definition={}, parameters={}, run_dir=run_dir, collector_dir=collector_dir)


def test_shared_artifact_is_written_once_per_content_and_returned_run_relative(tmp_path):
    payload = b'{"kind":"trace"}\n' * 1000
    first = _context(tmp_path, "amaru-stock-mempool").shared_artifact("amaru-json.ndjson", payload)
    second = _context(tmp_path, "amaru-stock-network").shared_artifact("amaru-json.ndjson", payload)
    assert first == second
    digest = hashlib.sha256(payload).hexdigest()
    assert first == f"measurements/shared/{digest[:16]}-amaru-json.ndjson"
    assert (tmp_path / first).read_bytes() == payload
    assert len(list((tmp_path / "measurements" / "shared").iterdir())) == 1
    # Different content keeps its own file; nothing is overwritten.
    other = _context(tmp_path, "amaru-stock-network").shared_artifact("amaru-json.ndjson", payload + b"x")
    assert other != first and (tmp_path / other).read_bytes() == payload + b"x"


def test_shared_artifact_name_is_a_bare_file_name(tmp_path):
    context = _context(tmp_path, "amaru-stock-mempool")
    for bad in ("", "raw/amaru-json.ndjson", "../x", "a\\b"):
        with pytest.raises(MeasurementArtifactPathError):
            context.shared_artifact(bad, b"x")
