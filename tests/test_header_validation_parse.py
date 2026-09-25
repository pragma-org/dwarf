from scripts import header_validation_parse as hv


def test_cardano_rejected_header_reason():
    line = ('{"at":"2026-09-23T00:00:01Z","ns":"ChainDB.AddBlockEvent.AddBlockValidation.InvalidBlock",'
            '"data":{"block":{"hash":"abcd"},"error":"...CounterTooSmallOCERT..."}}')
    events = hv.parse_cardano_header_events([line])
    assert events == [{"header_hash": "abcd", "verdict": "rejected",
                       "reason": "CounterTooSmallOCERT", "at": "2026-09-23T00:00:01Z"}]


def test_cardano_accepted_header():
    line = ('{"at":"2026-09-23T00:00:02Z","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain",'
            '"data":{"newtip":"ef01"}}')
    assert hv.parse_cardano_header_events([line]) == [
        {"header_hash": "ef01", "verdict": "accepted", "reason": None, "at": "2026-09-23T00:00:02Z"}]


def test_amaru_rejected_header_reason():
    line = ('{"timestamp":"2026-09-23T00:00:03Z","level":"ERROR","fields":{'
            '"error":"header validation failed: SequenceNumberTooSmall","header_hash":"beef",'
            '"outcome":"invalid_header","message":"chain.header_rejected"},"target":"amaru::consensus"}')
    assert hv.parse_amaru_header_events([line]) == [
        {"header_hash": "beef", "verdict": "rejected",
         "reason": "SequenceNumberTooSmall", "at": "2026-09-23T00:00:03Z"}]


def test_amaru_accepted_header():
    line = ('{"timestamp":"2026-09-23T00:00:04Z","level":"INFO","fields":{'
            '"message":"chain.tip_accepted","outcome":"new_tip","header_hash":"cafe"},"target":"amaru::consensus"}')
    assert hv.parse_amaru_header_events([line]) == [
        {"header_hash": "cafe", "verdict": "accepted", "reason": None, "at": "2026-09-23T00:00:04Z"}]


def test_verdict_by_hash_last_wins():
    events = [{"header_hash": "aa", "verdict": "rejected", "reason": "R", "at": "1"},
              {"header_hash": "aa", "verdict": "accepted", "reason": None, "at": "2"}]
    assert hv.verdict_by_hash(events)["aa"]["verdict"] == "accepted"


def test_unparseable_lines_are_skipped():
    assert hv.parse_cardano_header_events(["not json", ""]) == []
    assert hv.parse_amaru_header_events(["not json", ""]) == []


def test_cardano_accept_with_bare_string_block_does_not_crash():
    line = ('{"at":"t","ns":"ChainDB.AddBlockEvent.SwitchedToAFork",'
            '"data":{"block":"9f9f"}}')
    assert hv.parse_cardano_header_events([line]) == [
        {"header_hash": "9f9f", "verdict": "accepted", "reason": None, "at": "t"}]


def test_cardano_non_dict_data_is_skipped():
    line = '{"at":"t","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain","data":["x"]}'
    assert hv.parse_cardano_header_events([line]) == []


def test_cardano_accepted_real_shape_strips_slot_and_quotes():
    # Real cardano-node 11.1.2 AddedToCurrentChain: newtip carries "@slot",
    # tipBlockHash is bare; the parser must return the bare 64-hex hash.
    line = ('{"at":"2026-09-24T10:50:46Z","ns":"ChainDB.AddBlockEvent.AddedToCurrentChain",'
            '"data":{"newtip":"644a813052a20a5fa9a1be3d12351da0881fe55986a9aa875492dba0ba10009f@111000",'
            '"tipBlockHash":"644a813052a20a5fa9a1be3d12351da0881fe55986a9aa875492dba0ba10009f"}}')
    events = hv.parse_cardano_header_events([line])
    assert events == [{"header_hash": "644a813052a20a5fa9a1be3d12351da0881fe55986a9aa875492dba0ba10009f",
                       "verdict": "accepted", "reason": None, "at": "2026-09-24T10:50:46Z"}]


def test_cardano_chainsync_headererror_ocert_rejection():
    # Praos header rejections surface in the ChainSync client, not ChainDB.
    line = ('{"at":"2026-09-23T00:00:05Z","ns":"ChainSync.Client.Exception",'
            '"data":{"kind":"HeaderError","error":"HeaderError (BlockPoint (SlotNo 210310) '
            '(blockPointHash = f90ac6570000000000000000000000000000000000000000000000000000abcd)) '
            '(unwrapValidationErr = CounterTooSmallOCERT 1 0)"}}')
    events = hv.parse_cardano_header_events([line])
    assert events == [{
        "header_hash": "f90ac6570000000000000000000000000000000000000000000000000000abcd",
        "verdict": "rejected", "reason": "CounterTooSmallOCERT", "at": "2026-09-23T00:00:05Z"}]

def test_cardano_chainsync_headererror_without_hash_is_skipped():
    line = ('{"at":"t","ns":"ChainSync.Client.Exception",'
            '"data":{"kind":"HeaderError","error":"HeaderError something with no hash"}}')
    assert hv.parse_cardano_header_events([line]) == []


def test_merge_verdicts_sticky_reject_not_overwritten_by_later_accept():
    # A served deviant header hash rejected, then accepted on a canonical
    # re-serve of the SAME hash (both land in one lagging poll): reject sticks.
    acc = {}
    hv.merge_verdicts_sticky(acc, [
        {"header_hash": "H", "verdict": "rejected", "reason": "DecodeError"},
        {"header_hash": "H", "verdict": "accepted", "reason": None},
    ])
    assert acc["H"]["verdict"] == "rejected"


def test_merge_verdicts_sticky_reject_wins_regardless_of_order_and_across_calls():
    acc = {}
    # accept first, reject later (still reject wins)
    hv.merge_verdicts_sticky(acc, [{"header_hash": "H", "verdict": "accepted", "reason": None}])
    hv.merge_verdicts_sticky(acc, [{"header_hash": "H", "verdict": "rejected", "reason": "e"}])
    # and a subsequent accept on the same hash never revives it
    hv.merge_verdicts_sticky(acc, [{"header_hash": "H", "verdict": "accepted", "reason": None}])
    assert acc["H"]["verdict"] == "rejected"


def test_merge_verdicts_sticky_accept_last_wins_and_distinct_hashes_independent():
    acc = {}
    hv.merge_verdicts_sticky(acc, [
        {"header_hash": "A", "verdict": "accepted", "reason": None},
        {"header_hash": "B", "verdict": "rejected", "reason": "e"},
        {"header_hash": "A", "verdict": "accepted", "reason": None},
    ])
    assert acc["A"]["verdict"] == "accepted" and acc["B"]["verdict"] == "rejected"
    # a canonical (different) header accept never touches the deviant reject B
    hv.merge_verdicts_sticky(acc, [{"header_hash": "C", "verdict": "accepted", "reason": None}])
    assert acc["B"]["verdict"] == "rejected"
