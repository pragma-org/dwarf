"""Fallback Antithesis SDK writer: one NDJSON line per event, same shape as the
Haskell adversary's ``SDK.hs`` and the pinned ``setup-complete.sh`` convention.

Writes are best-effort (``OSError`` is swallowed) because the assertion stream
must never abort the scenario it reports on. Identifiers are DWARF property
names: ``dwarf_`` plus snake_case, which is what AT-4's normaliser classifies
as a DWARF property.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

PROPERTY_ID = re.compile(r'^dwarf_[a-z0-9_]+$')
KINDS = {'always': ('always', 'Always'), 'sometimes': ('sometimes', 'Sometimes'),
         'reachability': ('reachability', 'Reachable')}
SETUP_COMPLETE_LINE = '{"antithesis_setup":{"status":"complete","details":{"message":"ready to go"}}}'
_LOCATION = {'file': '', 'function': '', 'class': '', 'begin_line': 0, 'begin_column': 0}


def output_path(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('ANTITHESIS_OUTPUT_DIR'):
        return Path(environ['ANTITHESIS_OUTPUT_DIR']) / 'sdk.jsonl'
    if environ.get('ANTITHESIS_SDK_LOCAL_OUTPUT'):
        return Path(environ['ANTITHESIS_SDK_LOCAL_OUTPUT'])
    return Path('/tmp/antithesis_sdk.jsonl')


def _write_line(line, environ):
    path = output_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
            handle.flush()
    except OSError:
        return


def emit_assert(kind, identifier, condition, *, details=None, environ=None):
    if kind not in KINDS:
        raise ValueError('unsupported assertion kind: ' + str(kind))
    if not isinstance(identifier, str) or not PROPERTY_ID.fullmatch(identifier):
        raise ValueError('assertion identifier must match ' + PROPERTY_ID.pattern)
    assert_type, display_type = KINDS[kind]
    event = {'antithesis_assert': {
        'id': identifier, 'message': identifier, 'condition': bool(condition),
        'display_type': display_type, 'hit': True, 'must_hit': True, 'assert_type': assert_type,
        'details': details if isinstance(details, dict) else {}, 'location': dict(_LOCATION)}}
    _write_line(json.dumps(event, separators=(',', ':'), default=str, allow_nan=False), environ)


def setup_complete(environ=None):
    _write_line(SETUP_COMPLETE_LINE, environ)
