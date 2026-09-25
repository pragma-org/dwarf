"""Bounded, redacted full protocol transcript evidence writer."""
from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def _redact(
    value: Any,
    fields: set[str],
    *,
    prefix: str = "",
) -> tuple[Any, list[str]]:
    redacted_paths: list[str] = []
    if isinstance(value, dict):
        output = {}
        for key, nested in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if key_text.lower() in fields:
                output[key] = "[REDACTED]"
                redacted_paths.append(path)
            else:
                output[key], nested_paths = _redact(nested, fields, prefix=path)
                redacted_paths.extend(nested_paths)
        return output, redacted_paths
    if isinstance(value, list):
        output = []
        for index, nested in enumerate(value):
            path = f"{prefix}[{index}]"
            item, nested_paths = _redact(nested, fields, prefix=path)
            output.append(item)
            redacted_paths.extend(nested_paths)
        return output, redacted_paths
    return value, redacted_paths


class ProtocolTranscriptWriter:
    def __init__(
        self,
        path: str | Path,
        *,
        session_byte_limit: int,
        total_byte_limit: int,
        redact_fields: Iterable[str] = (),
    ) -> None:
        if session_byte_limit < 0 or total_byte_limit < 0:
            raise ValueError("transcript byte limits must be non-negative")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.session_byte_limit = int(session_byte_limit)
        self.total_byte_limit = int(total_byte_limit)
        self.redact_fields = {str(field).lower() for field in redact_fields}
        self._session_bytes: Counter[str] = Counter()
        self.retained_payload_bytes = 0
        self.record_count = 0

    def _payload(self, payload: Any) -> tuple[bytes, str, str, list[str]]:
        if isinstance(payload, bytes):
            return payload, base64.b64encode(payload).decode("ascii"), "base64", []
        redacted, paths = _redact(payload, self.redact_fields)
        encoded = json.dumps(
            redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return encoded.encode("utf-8"), encoded, "json", paths

    def append(
        self,
        *,
        session_id: str,
        direction: str,
        protocol: str,
        message_type: str,
        payload: Any,
        elapsed_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_bytes, retained_form, encoding, redacted_paths = self._payload(payload)
        size = len(payload_bytes)
        reasons = []
        if self._session_bytes[session_id] + size > self.session_byte_limit:
            reasons.append("session-byte-limit")
        if self.retained_payload_bytes + size > self.total_byte_limit:
            reasons.append("total-byte-limit")

        clean_metadata, metadata_redactions = _redact(
            metadata or {}, self.redact_fields, prefix="metadata"
        )
        truncated = bool(reasons)
        retained_size = 0 if truncated else size
        if not truncated:
            self._session_bytes[session_id] += size
            self.retained_payload_bytes += size
        record = {
            "record_index": self.record_count,
            "session_id": session_id,
            "direction": direction,
            "protocol": protocol,
            "message_type": message_type,
            "elapsed_seconds": elapsed_seconds,
            "payload_encoding": encoding,
            "payload_size_bytes": size,
            "retained_payload_size_bytes": retained_size,
            "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "payload": None if truncated else retained_form,
            "truncated": truncated,
            "truncation_reasons": reasons,
            "redacted_fields": redacted_paths + metadata_redactions,
            "metadata": clean_metadata,
        }
        with self.path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        self.record_count += 1
        return record

