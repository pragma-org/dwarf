import hashlib
import json

from profile_manager.protocol_transcript import ProtocolTranscriptWriter


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_transcript_retains_full_payload_metadata_and_hash(tmp_path):
    path = tmp_path / "transcript.ndjson"
    writer = ProtocolTranscriptWriter(
        path, session_byte_limit=1024, total_byte_limit=4096
    )

    writer.append(
        session_id="peer-a/chainsync",
        direction="inbound",
        protocol="ChainSync",
        message_type="MsgRollForward",
        payload=b"full-cbor-payload",
        elapsed_seconds=1.25,
        metadata={"peer_id": "peer-a", "trace_id": "trace-1"},
    )

    record = _records(path)[0]
    assert record["session_id"] == "peer-a/chainsync"
    assert record["payload_encoding"] == "base64"
    assert record["payload_size_bytes"] == len(b"full-cbor-payload")
    assert record["retained_payload_size_bytes"] == len(b"full-cbor-payload")
    assert record["payload_sha256"] == hashlib.sha256(b"full-cbor-payload").hexdigest()
    assert record["truncated"] is False
    assert record["metadata"] == {"peer_id": "peer-a", "trace_id": "trace-1"}


def test_transcript_enforces_per_session_and_total_byte_limits(tmp_path):
    path = tmp_path / "transcript.ndjson"
    writer = ProtocolTranscriptWriter(
        path, session_byte_limit=4, total_byte_limit=6
    )

    writer.append(
        session_id="s1", direction="inbound", protocol="BlockFetch",
        message_type="MsgBlock", payload=b"1234",
    )
    writer.append(
        session_id="s1", direction="inbound", protocol="BlockFetch",
        message_type="MsgBlock", payload=b"5",
    )
    writer.append(
        session_id="s2", direction="outbound", protocol="TxSubmission2",
        message_type="ReplyTxs", payload=b"12",
    )

    records = _records(path)
    assert records[0]["truncated"] is False
    assert records[1]["truncated"] is True
    assert records[1]["truncation_reasons"] == ["session-byte-limit"]
    assert records[1]["payload"] is None
    assert records[2]["truncated"] is False
    assert writer.retained_payload_bytes == 6


def test_transcript_redacts_sensitive_structured_fields_recursively(tmp_path):
    path = tmp_path / "transcript.ndjson"
    writer = ProtocolTranscriptWriter(
        path,
        session_byte_limit=4096,
        total_byte_limit=4096,
        redact_fields={"authorization", "secret", "token"},
    )
    payload = {
        "message": "hello",
        "authorization": "Bearer private",
        "nested": {"token": "private", "keep": 7},
    }

    writer.append(
        session_id="s1", direction="inbound", protocol="Handshake",
        message_type="ProposeVersions", payload=payload,
    )

    record = _records(path)[0]
    decoded = json.loads(record["payload"])
    assert decoded == {
        "authorization": "[REDACTED]",
        "message": "hello",
        "nested": {"keep": 7, "token": "[REDACTED]"},
    }
    assert record["redacted_fields"] == ["authorization", "nested.token"]
    assert "private" not in path.read_text()

