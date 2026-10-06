from pathlib import Path

import pytest

from modality_simulator.config import Config, load
from modality_simulator.errors import ConfigError

P = "MODALITY_SIMULATOR_"


def env(**settings: str) -> dict[str, str]:
    return {P + "GATEWAY_AE": "PIH_KOL-CI_GW", **{P + k: v for k, v in settings.items()}}


def test_defaults_need_only_the_gateway_ae():
    cfg = load(env())
    assert cfg == Config(gateway_ae="PIH_KOL-CI_GW")
    assert cfg.gateway_host == "host.docker.internal"
    assert cfg.gateway_port == 11112
    assert cfg.calling_ae == "SIM_MODALITY"
    assert cfg.modalities == ("CR", "US", "CT")
    assert cfg.station_ae_filter == ""
    assert cfg.institution == "OpenMRS Modality Simulator"
    assert cfg.auto_acquire is False
    assert cfg.poll_seconds == 30
    assert cfg.image_dir == Path("/images")
    assert cfg.data_dir == Path("/data")


@pytest.mark.parametrize("value", [None, "", "   "])
def test_gateway_ae_is_required(value):
    settings = {} if value is None else {P + "GATEWAY_AE": value}
    with pytest.raises(ConfigError, match="MODALITY_SIMULATOR_GATEWAY_AE must be set"):
        load(settings)


def test_reads_every_setting():
    cfg = load(env(
        GATEWAY_HOST="10.0.0.5", GATEWAY_PORT="104", CALLING_AE="SIM_CR",
        MODALITIES=" cr, us ", STATION_AE_FILTER="SIM_CR", INSTITUTION="Test Hospital",
        AUTO_ACQUIRE="TRUE", POLL_SECONDS="5",
    ))
    assert cfg == Config(
        gateway_ae="PIH_KOL-CI_GW", gateway_host="10.0.0.5", gateway_port=104,
        calling_ae="SIM_CR", modalities=("CR", "US"), station_ae_filter="SIM_CR",
        institution="Test Hospital", auto_acquire=True, poll_seconds=5,
    )


@pytest.mark.parametrize("value", ["A" * 17, "BAD\\AE", "TAB\tAE"])
def test_rejects_invalid_ae_titles(value):
    with pytest.raises(ConfigError, match="MODALITY_SIMULATOR_CALLING_AE is not a valid AE title"):
        load(env(CALLING_AE=value))


def test_accepts_a_16_character_ae_title_with_hyphen_and_underscore():
    assert load(env(GATEWAY_AE="PIH_KOL-CI_GW_16")).gateway_ae == "PIH_KOL-CI_GW_16"


@pytest.mark.parametrize("value", ["abc", "0", "65536"])
def test_rejects_bad_ports(value):
    with pytest.raises(ConfigError, match="MODALITY_SIMULATOR_GATEWAY_PORT"):
        load(env(GATEWAY_PORT=value))


@pytest.mark.parametrize("value,message", [("MR", "may only list CR, US, CT: MR"), (",,", "must list at least one")])
def test_rejects_bad_modalities(value, message):
    with pytest.raises(ConfigError, match=f"MODALITY_SIMULATOR_MODALITIES {message}"):
        load(env(MODALITIES=value))


def test_repeated_modalities_are_listed_once():
    assert load(env(MODALITIES="CR,cr,US")).modalities == ("CR", "US")


def test_rejects_a_bad_boolean():
    with pytest.raises(ConfigError, match="MODALITY_SIMULATOR_AUTO_ACQUIRE must be true or false"):
        load(env(AUTO_ACQUIRE="maybe"))


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_rejects_bad_poll_seconds(value):
    with pytest.raises(ConfigError, match="MODALITY_SIMULATOR_POLL_SECONDS"):
        load(env(POLL_SECONDS=value))
