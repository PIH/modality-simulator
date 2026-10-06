import threading

import pytest

from factories import entry, result
from modality_simulator.auto import MAX_ATTEMPTS, AutoAcquirer
from modality_simulator.errors import GatewayError
from modality_simulator.results import AccessionHistory, AcquisitionLog


def test_log_round_trips_results_newest_first(tmp_path):
    log = AcquisitionLog(tmp_path / "data" / "acquisitions.jsonl")
    assert log.recent() == []
    log.append(result(entry(accession="A1")))
    log.append(result(entry(accession="A2"), ok=False))
    recent = log.recent()
    assert [r["accession_number"] for r in recent] == ["A2", "A1"]
    assert recent[0]["ok"] is False and recent[1]["ok"] is True
    assert recent[0]["error"]


def test_log_skips_a_line_cut_short_by_a_crash(tmp_path):
    log = AcquisitionLog(tmp_path / "acquisitions.jsonl")
    log.append(result(entry(accession="A1")))
    with log.path.open("a") as f:
        f.write('{"accession_number": "A2", "ok"')
    assert [r["accession_number"] for r in log.records()] == ["A1"]


def test_history_per_accession(tmp_path):
    log = AcquisitionLog(tmp_path / "acquisitions.jsonl")
    log.append(result(entry(accession="A1"), ok=False))
    log.append(result(entry(accession="A1")))
    log.append(result(entry(accession="A2"), ok=False))
    assert log.history() == {
        "A1": AccessionHistory(stored=True, failures=1),
        "A2": AccessionHistory(stored=False, failures=1),
    }


class Harness:
    """A worklist and an acquirer that logs, as main wires them."""

    def __init__(self, tmp_path, entries, failing=()):
        self.log = AcquisitionLog(tmp_path / "acquisitions.jsonl")
        self.entries = list(entries)
        self.failing = set(failing)
        self.acquired = []

    def query(self):
        return list(self.entries)

    def do_acquire(self, e, only_if_new=False):
        self.only_if_new = only_if_new
        self.acquired.append(e.accession_number)
        r = result(e, ok=e.accession_number not in self.failing)
        self.log.append(r)
        return r

    def auto(self):
        return AutoAcquirer(self.query, self.do_acquire, self.log)


def test_acquires_each_new_entry_once(tmp_path):
    h = Harness(tmp_path, [entry(accession="A1"), entry(accession="A2")])
    auto = h.auto()
    auto.run_once()
    auto.run_once()
    assert h.acquired == ["A1", "A2"]


def test_skips_entries_without_accession(tmp_path):
    h = Harness(tmp_path, [entry(accession="")])
    h.auto().run_once()
    assert h.acquired == []


def test_retries_a_failing_accession_then_gives_up(tmp_path):
    h = Harness(tmp_path, [entry(accession="BAD")], failing={"BAD"})
    auto = h.auto()
    for _ in range(MAX_ATTEMPTS + 2):
        auto.run_once()
    assert h.acquired == ["BAD"] * MAX_ATTEMPTS


def test_skips_accessions_already_stored_even_by_hand_or_before_a_restart(tmp_path):
    h = Harness(tmp_path, [entry(accession="A1"), entry(accession="A2")])
    h.log.append(result(entry(accession="A1")))  # acquired from the console, or before a restart
    h.auto().run_once()
    assert h.acquired == ["A2"]
    h.auto().run_once()  # a new AutoAcquirer, as after a restart
    assert h.acquired == ["A2"]


def test_run_once_raises_when_the_worklist_cant_be_read(tmp_path):
    def down():
        raise GatewayError("down")
    auto = AutoAcquirer(down, lambda e, only_if_new=False: None, AcquisitionLog(tmp_path / "acquisitions.jsonl"))
    with pytest.raises(GatewayError):
        auto.run_once()


def test_run_forever_keeps_polling_through_errors_until_stopped(tmp_path):
    stop = threading.Event()
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise GatewayError("not yet")
        if len(calls) == 3:
            stop.set()
        return []

    AutoAcquirer(flaky, lambda e: None, AcquisitionLog(tmp_path / "a.jsonl")).run_forever(0, stop)
    assert len(calls) == 3


def test_auto_acquire_asks_to_skip_what_is_already_stored(tmp_path):
    h = Harness(tmp_path, [entry(accession="A1")])
    h.auto().run_once()
    assert h.only_if_new is True


def test_an_unexpected_error_on_one_entry_does_not_starve_the_rest(tmp_path):
    h = Harness(tmp_path, [entry(accession="BOOM"), entry(accession="A2")])
    real = h.do_acquire

    def acquire(e, only_if_new=False):
        if e.accession_number == "BOOM":
            raise RuntimeError("odd file")
        return real(e, only_if_new)

    AutoAcquirer(h.query, acquire, h.log).run_once()
    assert h.acquired == ["A2"]


def test_an_accession_whose_log_cant_be_written_is_still_attempted_at_most_max_attempts_times(tmp_path):
    attempts = []

    def unlogged_failure(e, only_if_new=False):
        attempts.append(e.accession_number)
        return result(e, ok=False)  # as if the log write failed: nothing reaches the log

    auto = AutoAcquirer(lambda: [entry(accession="BAD")], unlogged_failure, AcquisitionLog(tmp_path / "a.jsonl"))
    for _ in range(MAX_ATTEMPTS + 2):
        auto.run_once()
    assert attempts == ["BAD"] * MAX_ATTEMPTS


def test_an_accession_stored_but_not_logged_is_not_sent_again(tmp_path):
    attempts = []

    def unlogged_success(e, only_if_new=False):
        attempts.append(e.accession_number)
        return result(e)

    auto = AutoAcquirer(lambda: [entry(accession="A1")], unlogged_success, AcquisitionLog(tmp_path / "a.jsonl"))
    auto.run_once()
    auto.run_once()
    assert attempts == ["A1"]


def test_an_unexpected_error_counts_as_an_attempt(tmp_path):
    attempts = []

    def boom(e, only_if_new=False):
        attempts.append(1)
        raise RuntimeError("x")

    auto = AutoAcquirer(lambda: [entry(accession="A1")], boom, AcquisitionLog(tmp_path / "a.jsonl"))
    for _ in range(MAX_ATTEMPTS + 2):
        auto.run_once()
    assert len(attempts) == MAX_ATTEMPTS
