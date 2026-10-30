from pathlib import Path

import pytest

from factories import station
from modality_simulator.config import Station, load, overlaps
from modality_simulator.errors import ConfigError

KOL_CI = """
gateway:
  ae: PIH_KOL-CI_GW
stations:
  - name: X-ray
    calling_ae: KOL-CI_CR
    modalities: [CR]
  - name: Ultrasound
    calling_ae: KOL-CI_US
    modalities: [US]
    auto_acquire: true
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "stations.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_stations_with_defaults(tmp_path):
    cfg = load(write(tmp_path, KOL_CI))
    assert cfg.poll_seconds == 30
    assert cfg.data_dir == Path("/data")
    cr, us = cfg.stations
    assert cr == Station(name="X-ray", calling_ae="KOL-CI_CR", gateway_ae="PIH_KOL-CI_GW", modalities=("CR",))
    assert cr.gateway_host == "host.docker.internal"
    assert cr.gateway_port == 11112
    assert cr.institution == "OpenMRS Modality Simulator"
    assert cr.station_ae_filter == ""
    assert cr.auto_acquire is False
    assert cr.image_dir == Path("/images")
    assert us.modalities == ("US",)
    assert us.auto_acquire is True


def test_reads_every_shared_and_station_setting(tmp_path):
    cfg = load(write(tmp_path, """
gateway: {ae: GW, host: 10.0.0.5, port: 104}
institution: Test Hospital
poll_seconds: 5
stations:
  - {name: Room 1, calling_ae: SIM_1, modalities: [" cr", us, CR], station_ae_filter: ROOM1}
  - {name: Room 2, calling_ae: SIM_2, modalities: [CT]}
"""))
    assert cfg.poll_seconds == 5
    one, two = cfg.stations
    assert one == Station(
        name="Room 1", calling_ae="SIM_1", gateway_ae="GW", gateway_host="10.0.0.5", gateway_port=104,
        modalities=("CR", "US"), station_ae_filter="ROOM1", institution="Test Hospital",
    )
    assert (two.gateway_ae, two.gateway_host, two.gateway_port, two.institution) == ("GW", "10.0.0.5", 104, "Test Hospital")


def test_a_missing_file_names_the_path(tmp_path):
    with pytest.raises(ConfigError, match=r"No stations file at .*stations.yaml"):
        load(tmp_path / "stations.yaml")


def test_a_directory_instead_of_the_file_is_explained(tmp_path):
    (tmp_path / "stations.yaml").mkdir()
    with pytest.raises(ConfigError, match=r"stations.yaml is a directory"):
        load(tmp_path / "stations.yaml")


def test_invalid_yaml_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="isn't valid YAML"):
        load(write(tmp_path, "gateway: ["))


ONE = "stations: [{name: X, calling_ae: A, modalities: [CR]}]"


@pytest.mark.parametrize("text, field", [
    ("", "the file"),
    ("- a\n- b", "the file"),
    ("stations: []\ngateway: {ae: GW}", "stations"),
    ("gateway: {ae: GW}", "stations"),
    (ONE, "gateway.ae"),
    ("gateway: GW\n" + ONE, "gateway"),
    ("gateway: {ae: GW}\nstations: [X-ray]", r"stations\[1\]"),
    ("gateway: {ae: GW}\nstations: [{calling_ae: A, modalities: [CR]}]", r"stations\[1\]\.name"),
    ("gateway: {ae: GW}\nstations: [{name: X, modalities: [CR]}]", r"stations\[1\]\.calling_ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: CR}]", r"stations\[1\]\.modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: []}]", r"stations\[1\]\.modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [MR]}]", r"stations\[1\]\.modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], auto_acquire: 'yes'}]",
     r"stations\[1\]\.auto_acquire"),
    ("gateway: {ae: GW, port: abc}\n" + ONE, "gateway.port"),
    ("gateway: {ae: GW, port: 0}\n" + ONE, "gateway.port"),
    ("gateway: {ae: GW, port: true}\n" + ONE, "gateway.port"),
    ("gateway: {ae: GW}\npoll_seconds: 0\n" + ONE, "poll_seconds"),
    ("gateway: {ae: 'BAD\\AE'}\n" + ONE, "gateway.ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: 'KOL/CR', modalities: [CR]}]", r"stations\[1\]\.calling_ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], station_ae_filter: 'BAD\\AE'}]",
     r"stations\[1\]\.station_ae_filter"),
    ("gateway: {ae: GW, aet: X}\n" + ONE, "gateway.aet"),
    ("gateway: {ae: GW}\nstation: []\n" + ONE, "station"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], modality: CR}]", r"stations\[1\]\.modality"),
])
def test_bad_settings_are_errors_naming_the_field(tmp_path, text, field):
    with pytest.raises(ConfigError, match=field):
        load(write(tmp_path, text))


def test_calling_aes_must_be_unique(tmp_path):
    text = "gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR]}, {name: Y, calling_ae: A, modalities: [US]}]"
    with pytest.raises(ConfigError, match=r"stations\[2\]\.calling_ae.*A.*already"):
        load(write(tmp_path, text))


def test_accepts_a_16_character_ae_title_with_hyphen_and_underscore(tmp_path):
    cfg = load(write(tmp_path, "gateway: {ae: PIH_KOL-CI_GW_16}\nstations: [{name: X, calling_ae: KOL-CI_CR_16CHAR, modalities: [CR]}]"))
    assert cfg.stations[0].gateway_ae == "PIH_KOL-CI_GW_16"
    assert cfg.stations[0].calling_ae == "KOL-CI_CR_16CHAR"


def test_overlaps_warns_about_stations_that_would_see_the_same_entries():
    cr = station(name="X-ray", calling_ae="CR1", modalities=("CR",))
    assert overlaps([cr, station(name="Ultrasound", calling_ae="US1", modalities=("US",))]) == []
    assert overlaps([cr, station(name="X-ray 2", calling_ae="CR2", modalities=("CR", "US"), station_ae_filter="ROOM2")]) == []
    [warning] = overlaps([cr, station(name="X-ray 2", calling_ae="CR2", modalities=("CR",))])
    assert "CR1" in warning and "CR2" in warning and "CR" in warning
    same_room = dict(modalities=("US",), station_ae_filter="ROOM")
    assert len(overlaps([station(calling_ae="A", **same_room), station(calling_ae="B", **same_room)])) == 1
