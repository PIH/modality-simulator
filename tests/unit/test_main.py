import logging

import pytest

from factories import config, entry, result, station
from modality_simulator import acquire, config as config_module, main as main_module, mwl
from modality_simulator.results import AcquisitionLog
from modality_simulator.auto import AutoAcquirer
from modality_simulator.main import build, main
from modality_simulator.recent_logs import RecentLogs


def test_a_missing_stations_file_exits_2_saying_why(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "stations.yaml")
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    assert "No stations file at" in capsys.readouterr().err


def test_build_serves_the_console_without_auto_acquire_by_default(tmp_path):
    app, autos = build(config(data_dir=tmp_path))
    assert autos == []
    assert app.test_client().get("/health").get_json() == {"status": "ok"}


def test_build_makes_an_auto_acquirer_only_for_stations_with_it_on(tmp_path):
    _, autos = build(config(station(calling_ae="SIM_CR"), station(calling_ae="SIM_US", auto_acquire=True),
                            data_dir=tmp_path))
    [auto] = autos
    assert isinstance(auto, AutoAcquirer)


def console(monkeypatch, tmp_path, **cfg):
    e = entry(accession="A1")
    monkeypatch.setattr(mwl, "query_worklist", lambda c: [e])
    app, autos = build(config(station(**cfg), data_dir=tmp_path))
    return e, app.test_client(), (autos or [None])[0], AcquisitionLog(tmp_path / "SIM_CR" / "acquisitions.jsonl")


def test_an_unexpected_error_while_acquiring_is_logged_as_a_failed_acquisition(monkeypatch, tmp_path):
    def odd_library_file(e, cfg):
        raise IndexError("list index out of range")
    monkeypatch.setattr(acquire, "acquire", odd_library_file)
    e, client, _, log = console(monkeypatch, tmp_path)
    assert client.post("/station/SIM_CR/acquire", data={"accession": "A1"}).status_code == 303
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
    assert client.post("/station/SIM_CR/acquire", data={"accession": "A1"}).status_code == 303


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
    client.post("/station/SIM_CR/acquire", data={"accession": "A1"})
    assert len(sent) == 1


def test_build_shows_the_recent_log_in_the_console(monkeypatch, tmp_path):
    monkeypatch.setattr(mwl, "query_worklist", lambda c: [])
    recent = RecentLogs()
    recent.handle(logging.makeLogRecord({"msg": "hello from the log", "levelname": "INFO"}))
    app, _ = build(config(data_dir=tmp_path), recent)
    assert "hello from the log" in app.test_client().get("/station/SIM_CR").get_data(as_text=True)


def two_stations(monkeypatch, tmp_path):
    """build() over SIM_CR and SIM_US, recording which station queried and acquired."""
    used = {"query": [], "acquire": []}
    entries = {"SIM_CR": [entry("CR", accession="ACC-CR")], "SIM_US": [entry("US", accession="ACC-US")]}

    def query(cfg):
        used["query"].append(cfg.calling_ae)
        return entries[cfg.calling_ae]

    def acquired(e, cfg):
        used["acquire"].append(cfg.calling_ae)
        return result(e)
    monkeypatch.setattr(mwl, "query_worklist", query)
    monkeypatch.setattr(acquire, "acquire", acquired)
    app, _ = build(config(station(name="X-ray", calling_ae="SIM_CR", modalities=("CR",)),
                          station(name="Ultrasound", calling_ae="SIM_US", modalities=("US",)), data_dir=tmp_path))
    return app.test_client(), used


def test_each_station_queries_and_acquires_as_itself(monkeypatch, tmp_path):
    client, used = two_stations(monkeypatch, tmp_path)
    assert "ACC-US" in client.get("/station/SIM_US").get_data(as_text=True)
    assert client.post("/station/SIM_US/acquire", data={"accession": "ACC-US"}).status_code == 303
    assert set(used["query"]) == {"SIM_US"}
    assert used["acquire"] == ["SIM_US"]


def test_each_station_writes_its_own_log(monkeypatch, tmp_path):
    client, _ = two_stations(monkeypatch, tmp_path)
    client.post("/station/SIM_US/acquire", data={"accession": "ACC-US"})
    assert (tmp_path / "SIM_US" / "acquisitions.jsonl").exists()
    assert not (tmp_path / "SIM_CR").exists()


def test_acquisition_log_lines_name_the_station(monkeypatch, tmp_path, caplog):
    client, _ = two_stations(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO, logger="modality_simulator"):
        client.post("/station/SIM_US/acquire", data={"accession": "ACC-US"})
    [stored] = [r.getMessage() for r in caplog.records if "Stored" in r.getMessage()]
    assert stored.startswith("SIM_US: Stored 1 image(s) for accession ACC-US")
