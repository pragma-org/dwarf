#!/usr/bin/env python3
"""One-shot stock Amaru telemetry collector for component proof and diagnosis."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from profile_manager.measurement_collectors.amaru_stock import (
    DEFAULT_MAX_SOURCE_BYTES,
    STOCK_MEASUREMENT_IDS,
    AmaruStockCollector,
)
from profile_manager.measurement_runtime import CollectorContext


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurement-id", required=True, choices=STOCK_MEASUREMENT_IDS)
    parser.add_argument("--json-trace", action="append", default=[])
    parser.add_argument("--otlp-trace", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-source-bytes", type=int, default=DEFAULT_MAX_SOURCE_BYTES)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    run_dir = Path(args.output_dir)
    measurement_id = args.measurement_id
    entry = {
        "id": measurement_id,
        "parameters": {},
        "definition": {"id": measurement_id},
    }
    context = CollectorContext(
        measurement_id=measurement_id,
        definition=entry["definition"],
        parameters={},
        run_dir=run_dir,
        collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )
    collector = AmaruStockCollector(
        entry,
        json_trace_paths=args.json_trace,
        otlp_trace_paths=args.otlp_trace,
        include_existing=True,
        max_source_bytes=args.max_source_bytes,
    )
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
