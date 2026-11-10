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
    assert result["artifacts"]["raw"].startswith("measurements/shared/") and result["artifacts"]["raw"].endswith("-cardano-node.ndjson")
    assert (context.run_dir / result["artifacts"]["raw"]).is_file()
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


def test_network_keepalive_rtt_derives_from_message_pairs_per_connection(tmp_path):
    connection = {"connectionId": "10.192.3.3:3001 10.192.3.2:3001"}
    other = {"connectionId": "10.192.3.3:3001 10.192.3.4:3001"}
    records = [
        {"at": "2026-11-04T02:07:05.554233748Z", "ns": "KeepAlive.Remote.Send.KeepAlive",
         "data": {"kind": "Send", "msg": {"agency": "SingClient", "kind": "KeepAlive"}, "peer": connection},
         "sev": "Debug", "thread": "126", "host": "node1"},
        {"at": "2026-11-04T02:07:05.554433748Z", "ns": "KeepAlive.Remote.Send.KeepAlive",
         "data": {"kind": "Send", "msg": {"agency": "SingClient", "kind": "KeepAlive"}, "peer": other},
         "sev": "Debug", "thread": "127", "host": "node1"},
        {"at": "2026-11-04T02:07:05.554643181Z", "ns": "KeepAlive.Remote.Receive.KeepAliveResponse",
         "data": {"kind": "Recv", "msg": {"agency": "SingServer", "kind": "KeepAliveResponse"}, "peer": connection},
         "sev": "Debug", "thread": "126", "host": "node1"},
        {"at": "2026-11-04T02:07:05.555433748Z", "ns": "KeepAlive.Remote.Receive.KeepAliveResponse",
         "data": {"kind": "Recv", "msg": {"agency": "SingServer", "kind": "KeepAliveResponse"}, "peer": other},
         "sev": "Debug", "thread": "127", "host": "node1"},
        # a responder-side exchange must not produce a sample
        {"at": "2026-11-04T02:07:06.000000000Z", "ns": "KeepAlive.Remote.Receive.KeepAlive",
         "data": {"kind": "Recv", "msg": {"agency": "SingClient", "kind": "KeepAlive"}, "peer": connection},
         "sev": "Debug", "thread": "126", "host": "node1"},
        {"at": "2026-11-04T02:07:06.000100000Z", "ns": "KeepAlive.Remote.Send.KeepAliveResponse",
         "data": {"kind": "Send", "msg": {"agency": "SingServer", "kind": "KeepAliveResponse"}, "peer": connection},
         "sev": "Debug", "thread": "126", "host": "node1"},
    ]
    trace = tmp_path / "node1.ndjson"
    trace.write_text("".join(json.dumps(record) + "\n" for record in records))
    run_dir = tmp_path / "run"
    measurement_id = "cardano-stock-network"
    context = CollectorContext(
        measurement_id=measurement_id, definition={"id": measurement_id}, parameters={},
        run_dir=run_dir, collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )
    collector = CardanoStockCollector(
        {"id": measurement_id, "definition": {"id": measurement_id}}, trace_paths=[trace], include_existing=True,
    )
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    result = collector.finalize(context)

    rtt = result["measurements"]["keepalive_rtt"]
    assert rtt["status"] == "available"
    assert rtt["sample_count"] == 2
    assert rtt["minimum"] == 410.0
    assert rtt["maximum"] == 1000.0
    assert "message-pair" in result["claims"]["keepalive_rtt"]


def test_mempool_rejected_zero_carries_the_attempt_count(tmp_path):
    from profile_manager.measurement_collectors import cardano_stock
    rows = [
        _line("Mempool.AttemptAdd", "TraceMempoolAttemptingAdd", tx={"txid": "tx1"}),
        _line("Mempool.AddedTx", "TraceMempoolAddedTx", tx={"txid": "tx1"}, mempoolSize={"numTxs": 1, "bytes": 256}),
        _line("Mempool.AttemptAdd", "TraceMempoolAttemptingAdd", tx={"txid": "tx2"}),
        _line("Mempool.AddedTx", "TraceMempoolAddedTx", tx={"txid": "tx2"}, mempoolSize={"numTxs": 2, "bytes": 512}),
    ]
    events = [event for event in (cardano_stock._normalize_record(json.loads(row)) for row in rows) if event]
    metrics, _claims = cardano_stock._build_measurements("cardano-stock-txsubmission-mempool", events)
    assert metrics["mempool_rejected"] == {"status": "available", "unit": "transactions", "value": 0, "offered_count": 2}


def test_chain_lifecycle_rollback_zero_carries_the_chain_events_it_scanned():
    """A rollback count of zero over N chain events is a reading, not a gap
    (run 20261104T124102Z-22717c0b: rollback-count missed the coverage rule)."""
    from profile_manager.measurement_collectors.cardano_stock import _build_measurements
    events = [{"kind": "chain-adopted"}, {"kind": "chainsync-validated-header"}, {"kind": "keepalive-sample"}]
    measurements, _notes = _build_measurements("cardano-stock-chain-lifecycle", events)
    assert measurements["rollbacks"]["value"] == 0
    assert measurements["rollbacks"]["offered_count"] == 2 == measurements["chain_events"]["value"]


# Real cardano-node 11.1.2 lines (run 20261109T070545Z-26bb2c20, node1, Mempool at Debug).
# The node traces each sync as an enclosing-time pair: a "Starting" edge, then the
# elapsed seconds as the edge value. The collector read only a bare number, so the
# 2026-11-04 Pages runs (460 Synced records) and this run reported no samples.
REAL_MEMPOOL_SYNCED = (
    '{"at":"2026-11-09T07:05:48.702221361Z","ns":"Mempool.Synced","data":{"enclosingTime":{"edge":"Starting"},"kind":"TraceMempoolSynced"},"sev":"Debug","thread":"31","host":"node1"}\n'
    '{"at":"2026-11-09T07:05:48.702425602Z","ns":"Mempool.Synced","data":{"enclosingTime":{"edge":2.10363e-4},"kind":"TraceMempoolSynced"},"sev":"Debug","thread":"31","host":"node1"}\n'
)
# Real node1 line from the same run: one chain adoption.
REAL_ADOPTED = (
    '{"at":"2026-11-09T07:05:48.702055854Z","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":{"headers":[{"blockNo":"12","hash":"\\"aee1c6336c86302da89290b24185f89555068ffbf8b858c49ef40ae917c0bdf7\\"","kind":"ShelleyBlock","slotNo":"317"}],"kind":"AddedToCurrentChain","newSuffixSelectView":{"blockNo":12,"issueNo":0,"issuerHash":"12d74b9901f50f401196cb553c1c65b19c977d01d2235bc78ffabe11","kind":"PraosTiebreakerView","slotNo":317,"tieBreakVRF":"6b42fea74786ad06e7f51abf0a7508257e1e3d85625c9821d236e31775c29402d0735c0c29985b91d06bc1cc6e984a52306745231b71e597137c4e7716e0c5ee","weightBoost":0},"newtip":"aee1c6336c86302da89290b24185f89555068ffbf8b858c49ef40ae917c0bdf7@317","tipBlockHash":"aee1c6336c86302da89290b24185f89555068ffbf8b858c49ef40ae917c0bdf7","tipBlockIssuerVKeyHash":"12d74b9901f50f401196cb553c1c65b19c977d01d2235bc78ffabe11","tipBlockParentHash":"e7160aa16dfe73ffcb0aab8a0124b5f296bbaa55358674f732dd4af19249c91e"},"sev":"Notice","thread":"19","host":"node1"}\n'
)


def test_mempool_sync_duration_reads_the_enclosing_time_edge_of_real_11_1_2_records():
    from profile_manager.measurement_collectors import cardano_stock
    loaded = _load_cardano_trace_bytes([("node1.ndjson", REAL_MEMPOOL_SYNCED.encode(), False)])
    metrics, _claims = cardano_stock._build_measurements("cardano-stock-txsubmission-mempool", loaded["events"])
    sync = metrics["mempool_sync_duration"]
    # One completed sync; the "Starting" edge is not a sample.
    assert sync["status"] == "available"
    assert sync["sample_count"] == 1
    assert abs(sync["median"] - 210.363) < 1e-6


def _finalize_stock(tmp_path, measurement_id, body: bytes):
    trace = tmp_path / "node1.ndjson"
    tmp_path.mkdir(parents=True, exist_ok=True)
    trace.write_bytes(body)
    run_dir = tmp_path / "run"
    context = CollectorContext(
        measurement_id=measurement_id, definition={"id": measurement_id}, parameters={},
        run_dir=run_dir, collector_dir=run_dir / "measurements" / "collectors" / measurement_id,
    )
    collector = CardanoStockCollector(
        {"id": measurement_id, "definition": {"id": measurement_id}}, trace_paths=[trace], include_existing=True,
    )
    collector.prepare(context)
    collector.start(context)
    collector.stop(context)
    return collector.finalize(context)


def test_an_empty_trace_window_reports_no_counter_as_a_measured_zero(tmp_path):
    """35 of 36 cardano runs of the 2026-11-09 campaign (e.g. 20261109T040750Z-62124416)
    retained a 1-byte trace (source_record_count 0) yet reported chain_events,
    adopted_blocks, served_blocks, mempool_accepted ... as available zeros."""
    from profile_manager.measurement_collectors.cardano_stock import STOCK_MEASUREMENT_IDS
    for index, measurement_id in enumerate(STOCK_MEASUREMENT_IDS):
        result = _finalize_stock(tmp_path / str(index), measurement_id, b"\n")
        assert result["export"]["source_record_count"] == 0
        for name, metric in result["measurements"].items():
            assert metric["status"] == "unavailable", (measurement_id, name, metric)
            assert metric.get("value") is None, (measurement_id, name)
            assert "no node trace record" in metric.get("reason", "") or name in {
                "handler_queue_residence", "txsubmission_residence",
                "epoch_transition_duration", "plutus_vm_duration",
            }, (measurement_id, name, metric)


def test_a_non_empty_window_keeps_its_counts(tmp_path):
    result = _finalize_stock(tmp_path, "cardano-stock-chain-lifecycle", REAL_ADOPTED.encode())
    assert result["measurements"]["adopted_blocks"] == {"status": "available", "unit": "blocks", "value": 1}
    assert result["measurements"]["rollbacks"]["value"] == 0


# Real line from a self-composed devnet node log (run 20261109T113615Z-5b45ab96,
# outputs/substrate-teardown/node-logs/node1.log): `docker logs -t` prefixes each
# JSON trace record with the container-runtime timestamp.
REAL_DOCKER_PREFIXED = (
    '2026-11-09T11:36:31.503327859Z {"at":"2026-11-09T11:36:31.502962731Z","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":{"kind":"AddedToCurrentChain","newSuffixSelectView":{"blockNo":0,"issueNo":0,"issuerHash":"247af59be0821c69b4872a37627302bb67091b5850b209e72f741677","kind":"PraosTiebreakerView","slotNo":5,"tieBreakVRF":"86a82346d6573a6bdd02b9367138e01d120e252547d9a420bf4c844aa6554037e6328ec84094064ad596830894c83c586e5780148c662bdd9312553c8ca44d60","weightBoost":0},"newtip":"31e24817a912dc5e32cc9f1860ecfd4ebfb4fd1ac1aab374b4ad0dbfc961910a@5"},"sev":"Notice","thread":"19","host":"node1"}\n'
    '2026-11-09T11:36:16.867974730Z Node configuration: NodeConfiguration {ncSocketConfig = SocketConfig {}}\n'
)


def test_docker_timestamp_prefixed_node_logs_normalize_like_plain_traces():
    loaded = _load_cardano_trace_bytes([("node1.log", REAL_DOCKER_PREFIXED.encode(), False)])
    assert [event["kind"] for event in loaded["events"]] == ["chain-adopted"]
    # The node's own trace time stays authoritative, not the runtime prefix.
    assert loaded["events"][0]["timestamp"] == "2026-11-09T11:36:31.502962731Z"
    # A prefixed non-JSON startup line is rejected, not silently dropped.
    assert loaded["rejected_record_count"] == 1
