"""A primitive may record the time an event describes (an attempt's own clock) instead
of the moment the event was written; everything else about the entry is unchanged."""
import json

from profile_manager import forensic


def test_log_entry_takes_the_events_own_timestamp(tmp_path):
    handle = forensic.start_run(
        scenario_id="ts-test", scenario_yaml=b'{"spec_version":"v1","id":"ts-test"}\n',
        target={"implementation": "amaru", "version": "test"}, runtime="library", profile_id=None,
        profile_resolved=None, framework_version="test", framework_commit="test", seed=1,
        runs_dir=tmp_path / "runs", state_dir=tmp_path / "state",
    )
    handle.log(phase="load", primitive="p", level="info", event="workload_accounting",
               payload={"backlog": 1}, ts="2023-11-14T22:13:20.000000Z")
    handle.log(phase="load", primitive="p", level="info", event="workload_accounting", payload={"backlog": 0})
    handle.end(exit_status="pass")
    entries = [json.loads(line) for line in (tmp_path / "runs" / handle.run_id / "log.ndjson").read_text().splitlines()
               if '"workload_accounting"' in line]
    assert entries[0]["ts"] == "2023-11-14T22:13:20.000000Z"
    assert entries[1]["ts"] != entries[0]["ts"] and entries[1]["ts"].endswith("Z")
