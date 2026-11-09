"""F127: range pressure reads the proxy after a block passed, not at the first segment.

The step returned as soon as any segment was intercepted, so it saw the range request but not
the block reply (live 30109fb5: block_range_requests_observed 1, blocks_fetched 0).
"""
from scripts import runtime_chainsync_blockfetch_fault as fault


def test_range_pressure_is_not_ready_on_the_request_alone():
    assert not fault.proxy_activity_ready(mode="blockfetch_range_pressure",
                                          stats={"intercepted_segments": 3, "block_range_requests_observed": 1})


def test_range_pressure_is_ready_once_a_block_passed():
    assert fault.proxy_activity_ready(mode="blockfetch_range_pressure",
                                      stats={"intercepted_segments": 9, "block_range_requests_observed": 1,
                                             "blocks_fetched": 1})


def test_other_modes_keep_the_first_segment_rule():
    assert fault.proxy_activity_ready(mode="generic", stats={"intercepted_segments": 1})
