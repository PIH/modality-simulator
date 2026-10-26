import logging

import pytest

from factories import config, entry, result
from modality_simulator import acquire, main as main_module, mwl
from modality_simulator.results import AcquisitionLog
from modality_simulator.auto import AutoAcquirer
from modality_simulator.main import build, main
from modality_simulator.recent_logs import RecentLogs


def test_missing_gateway_ae_exits_2_saying_why(monkeypatch, capsys):
    monkeypatch.delenv("MODALITY_SIMULATOR_GATEWAY_AE", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    assert "MODALITY_SIMULATOR_GATEWAY_AE must be set" in capsys.readouterr().err


def test_build_serves_the_console_without_auto_acquire_by_default(tmp_path):
    app, auto = build(config(data_dir=tmp_path))
    assert auto is None
    assert app.test_client().get("/health").get_json() == {"status": "ok"}


def test_build_makes_an_auto_acquirer_when_it_is_on(tmp_path):
    _, auto = build(config(data_dir=tmp_path, auto_acquire=True))
    assert isinstance(auto, AutoAcquirer)


def console(monkeypatch, tmp_path, **cfg):
    e = entry(accession="A1")
    monkeypatch.setattr(mwl, "query_worklist", lambda c: [e])
    app, auto = build(config(data_dir=tmp_path, **cfg))
    return e, app.test_client(), auto, AcquisitionLog(tmp_path / "acquisitions.jsonl")


def test_an_unexpected_error_while_acquiring_is_logged_as_a_failed_acquisition(monkeypatch, tmp_path):
    def odd_library_file(e, cfg):
        raise IndexError("list index out of range")
    monkeypatch.setattr(acquire, "acquire", odd_library_file)
    e, client, _, log = console(monkeypatch, tmp_path)
    assert client.post("/acquire", data={"accession": "A1"}).status_code == 303
    [record] = log.records()
    assert record["ok"] is False
    assert "Unexpected error: list index out of range" in record["error"]
    assert record["accession_number"] == "A1" and record["patient_id"] == e.patient_id


def test_a_failing_log_write_does_not_raise_out_of_an_acquisition(monkeypatch, tmp_path):
    monkeypatch.setattr(acquire, "acquire", lambda e, cfg: result(e))

    def unwritable(self, r):
        raise OSError("No space left on device")
    monkeypatch.setattr(AcquisitionLog, "append", unwritable)
    _, client, _, _ = console(monkeypatch, tmp_path)
    assert client.post("/acquire", data={"accession": "A1"}).status_code == 303


def test_auto_acquire_skips_what_was_stored_since_it_looked(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(acquire, "acquire", lambda e, cfg: sent.append(e) or result(e))
    e, _, auto, log = console(monkeypatch, tmp_path, auto_acquire=True)
    log.append(result(e))  # the console stored it after auto-acquire's own check
    auto._acquire(e, only_if_new=True)
    assert sent == []


def test_a_manual_acquire_can_send_again_deliberately(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(acquire, "acquire", lambda e, cfg: sent.append(e) or result(e))
    e, client, _, log = console(monkeypatch, tmp_path)
    log.append(result(e))
    client.post("/acquire", data={"accession": "A1"})
    assert len(sent) == 1


def test_build_shows_the_recent_log_in_the_console(monkeypatch, tmp_path):
    monkeypatch.setattr(mwl, "query_worklist", lambda c: [])
    recent = RecentLogs()
    recent.handle(logging.makeLogRecord({"msg": "hello from the log", "levelname": "INFO"}))
    app, _ = build(config(data_dir=tmp_path), recent)
    assert "hello from the log" in app.test_client().get("/").get_data(as_text=True)
