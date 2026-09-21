import json

from profile_manager.measurement_collectors.cardano_stock import (
    CARDANO_SOURCE_REVISION,
    CardanoStockCollector,
    _load_cardano_trace_bytes,
    build_cardano_stock_factories,
)
from profile_manager.measurement_runtime import CollectorContext


def _line(ns, kind, **data):
    return json.dumps(
        {
            "at": "2026-09-18T10:15:04.200705620Z",
            "ns": ns,
            "data": {"kind": kind, **data},
            "sev": "Info",
            "thread": "41",
            "host": "node1",
        },
        sort_keys=True,
    )


def _fixture():
    rows = [
        _line("ChainSync.Client.DownloadedHeader", "DownloadedHeader", slot=12, block="aa", blockNo=4),
        _line("ChainDB.AddBlockEvent.AddedToCurrentChain", "AddedToCurrentChain", newtip={"slot": 12, "hash": "aa"}, tipBlockHash="aa"),
        _line("BlockFetch.Client.CompletedBlockFetch", "CompletedBlockFetch", delay=0.0043, size=2048, block="aa", peer="peer-1"),
        _line("BlockFetch.Server.SendBlock", "BlockFetchServer", block="aa"),
        _line("Mempool.AttemptAdd", "TraceMempoolAttemptingAdd", tx={"txid": "tx1"}),
        _line("Mempool.AddedTx", "TraceMempoolAddedTx", tx={"txid": "tx1"}, mempoolSize={"numTxs": 1, "bytes": 256}),
        _line("Mempool.RejectedTx", "TraceMempoolRejectedTx", tx={"txid": "tx2"}, err="invalid", mempoolSize={"numTxs": 1, "bytes": 256}),
        _line("Mempool.Synced", "TraceMempoolSynced", enclosingTime=0.003),
        _line("KeepAlive.Remote.KeepAliveClient", "AddSample", address="peer-1", rtt=0.0012, sampleTime=1.0, outboundG=0.1, inboundG=0.2),
        _line("Forge.Loop.AdoptedBlock", "TraceAdoptedBlock", slot=12, block="aa", txIds=["tx1"]),
        _line("Resources", "ResourceStats", CentiCpu=123, RSS=4096, Heap=2048, NetRd=100, NetWr=200, Threads=9),
        "not-json",
        _line("Unrelated.Namespace", "Other", value=1),
    ]
    return ("\n".join(rows) + "\n").encode()


def test_exact_cardano_trace_shapes_are_normalized_without_host_timing_inference():
    loaded = _load_cardano_trace_bytes([("node1.ndjson", _fixture(), False)])
    kinds = {event["kind"] for event in loaded["events"]}
    assert {
        "chainsync-downloaded-header",
        "chain-adopted",
        "blockfetch-completed",
        "blockfetch-served",
        "mempool-attempt",
        "mempool-accepted",
        "mempool-rejected",
        "mempool-synced",
        "keepalive-sample",
        "forge-adopted-block",
        "resource-stats",
    } <= kinds
    completed = next(event for event in loaded["events"] if event["kind"] == "blockfetch-completed")
    assert completed["duration_micros"] == 4300
    assert completed["timing_source"] == "node-emitted-blockfetch-delay"
    assert completed["block_hash"] == "aa"
    accepted = next(event for event in loaded["events"] if event["kind"] == "mempool-accepted")
    assert accepted["tx_id"] == "tx1"
    assert loaded["source_record_count"] == 13
    assert loaded["rejected_record_count"] == 1
    assert loaded["ignored_record_count"] == 1


def test_duplicate_records_are_retained_once_and_truncation_is_explicit():
    row = _line("BlockFetch.Client.CompletedBlockFetch", "CompletedBlockFetch", delay=0.001, size=100, block="bb")
    loaded = _load_cardano_trace_bytes([("node1.ndjson", (row + "\n" + row + "\n").encode(), True)])
    assert len(loaded["events"]) == 1
    assert loaded["duplicate_record_count"] == 1
    assert loaded["truncated_sources"] == ["node1.ndjson"]


def test_collector_writes_bounded_raw_normalized_and_measurement_results(tmp_path):
    trace = tmp_path / "node1.ndjson"
    trace.write_bytes(_fixture())
    run_dir = tmp_path / "run"
    measurement_id = "cardano-stock-blockfetch"
    context = CollectorContext(
        measurement_id=measurement_id,
        definition={"id": measurement_id},
        parameters={},
        run_dir=run_dir,
        collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )
    collector = CardanoStockCollector(
        {"id": measurement_id, "definition": {"id": measurement_id}},
        trace_paths=[trace],
        include_existing=True,
    )
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)
    assert result["source_revision"] == CARDANO_SOURCE_REVISION
    assert result["measurements"]["blockfetch_duration"]["sample_count"] == 1
    assert result["measurements"]["blockfetch_duration"]["median"] == 4300
    assert result["measurements"]["blockfetch_bytes"]["value"] == 2048
    assert result["export"]["incomplete"] is True
    assert context.artifact_path("raw/cardano-node.ndjson").is_file()
    assert context.artifact_path("normalized.ndjson").is_file()
    assert context.artifact_path("result.json").is_file()


def test_factory_registry_binds_every_stock_id(tmp_path):
    trace = tmp_path / "node1.ndjson"
    trace.write_text("")
    factories = build_cardano_stock_factories(trace_paths=[trace])
    assert set(factories) == {
        "cardano-stock-chain-lifecycle",
        "cardano-stock-blockfetch",
        "cardano-stock-txsubmission-mempool",
        "cardano-stock-ledger-block-epoch",
        "cardano-stock-plutus-execution",
        "cardano-stock-network",
    }
