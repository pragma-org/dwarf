import json
from pathlib import Path

import pytest

from profile_manager import antithesis_sdk as sdk
from profile_manager.antithesis_conventions import SETUP_COMPLETE_SH


def lines(path):
    return path.read_text(encoding='utf-8').splitlines()


def test_output_path_precedence(tmp_path):
    assert sdk.output_path({'ANTITHESIS_OUTPUT_DIR': str(tmp_path), 'ANTITHESIS_SDK_LOCAL_OUTPUT': '/x/y.jsonl'}) == tmp_path / 'sdk.jsonl'
    assert sdk.output_path({'ANTITHESIS_SDK_LOCAL_OUTPUT': '/x/y.jsonl'}) == Path('/x/y.jsonl')
    assert sdk.output_path({}) == Path('/tmp/antithesis_sdk.jsonl')


def test_always_line_matches_the_haskell_shape(tmp_path):
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path / 'out')}
    sdk.emit_assert('always', 'dwarf_all_nodes_responsive', True, details={'result': 'pass'}, environ=env)
    [line] = lines(tmp_path / 'out' / 'sdk.jsonl')
    assert json.loads(line) == {'antithesis_assert': {
        'id': 'dwarf_all_nodes_responsive', 'message': 'dwarf_all_nodes_responsive', 'condition': True,
        'display_type': 'Always', 'hit': True, 'must_hit': True, 'assert_type': 'always',
        'details': {'result': 'pass'},
        'location': {'file': '', 'function': '', 'class': '', 'begin_line': 0, 'begin_column': 0}}}
    assert '\n' not in line


@pytest.mark.parametrize('kind,assert_type,display', [
    ('always', 'always', 'Always'), ('sometimes', 'sometimes', 'Sometimes'), ('reachability', 'reachability', 'Reachable')])
def test_kinds_map_to_sdk_types(tmp_path, kind, assert_type, display):
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path)}
    sdk.emit_assert(kind, 'dwarf_x', False, environ=env)
    row = json.loads(lines(tmp_path / 'sdk.jsonl')[0])['antithesis_assert']
    assert (row['assert_type'], row['display_type'], row['condition'], row['details']) == (assert_type, display, False, {})


def test_setup_complete_line_equals_the_pinned_convention(tmp_path):
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path)}
    sdk.setup_complete(environ=env)
    expected = [l for l in SETUP_COMPLETE_SH.splitlines() if l.startswith("echo '")][0]
    expected = expected[len("echo '"):expected.index("' >>")]
    assert lines(tmp_path / 'sdk.jsonl') == [expected]


def test_invalid_identifier_or_kind_is_refused(tmp_path):
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path)}
    with pytest.raises(ValueError):
        sdk.emit_assert('always', 'Mixed_Case', True, environ=env)
    with pytest.raises(ValueError):
        sdk.emit_assert('always', 'no_prefix', True, environ=env)
    with pytest.raises(ValueError):
        sdk.emit_assert('never', 'dwarf_x', True, environ=env)
    assert not (tmp_path / 'sdk.jsonl').exists()


def test_non_json_details_are_stringified_and_lines_append(tmp_path):
    env = {'ANTITHESIS_OUTPUT_DIR': str(tmp_path)}
    sdk.emit_assert('sometimes', 'dwarf_a', True, details={'path': Path('/p'), 'n': 1}, environ=env)
    sdk.emit_assert('sometimes', 'dwarf_b', True, environ=env)
    rows = [json.loads(l)['antithesis_assert'] for l in lines(tmp_path / 'sdk.jsonl')]
    assert [r['id'] for r in rows] == ['dwarf_a', 'dwarf_b']
    assert rows[0]['details'] == {'path': '/p', 'n': 1}


def test_write_failure_is_swallowed(tmp_path):
    blocked = tmp_path / 'file'
    blocked.write_text('x')
    sdk.emit_assert('always', 'dwarf_x', True, environ={'ANTITHESIS_OUTPUT_DIR': str(blocked)})
    sdk.setup_complete(environ={'ANTITHESIS_OUTPUT_DIR': str(blocked)})
