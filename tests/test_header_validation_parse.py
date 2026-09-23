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
