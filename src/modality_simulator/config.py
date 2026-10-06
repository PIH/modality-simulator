"""The simulator's settings, read once from the environment at startup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from modality_simulator.errors import ConfigError

PREFIX = "MODALITY_SIMULATOR_"
SUPPORTED_MODALITIES = ("CR", "US", "CT")

# A DICOM AE title: 1-16 characters, with no backslash or control characters.
_AE_TITLE = re.compile(r"[^\\\x00-\x1f\x7f]{1,16}")
_TRUE = {"true", "1", "yes"}
_FALSE = {"false", "0", "no"}


@dataclass(frozen=True)
class Config:
    gateway_ae: str
    gateway_host: str = "host.docker.internal"
    gateway_port: int = 11112
    calling_ae: str = "SIM_MODALITY"
    modalities: tuple[str, ...] = SUPPORTED_MODALITIES
    station_ae_filter: str = ""
    institution: str = "OpenMRS Modality Simulator"
    auto_acquire: bool = False
    poll_seconds: int = 30
    image_dir: Path = Path("/images")
    data_dir: Path = Path("/data")


def load(env: Mapping[str, str]) -> Config:
    """Builds a Config from MODALITY_SIMULATOR_* variables; raises ConfigError naming a bad one."""

    def get(name: str, default: str) -> str:
        return env.get(PREFIX + name, "").strip() or default

    gateway_ae = get("GATEWAY_AE", "")
    if not gateway_ae:
        raise ConfigError(
            f"{PREFIX}GATEWAY_AE must be set: the gateway's AE title (its Local AE in AdvaPACS)"
        )
    return Config(
        gateway_ae=_ae_title("GATEWAY_AE", gateway_ae),
        gateway_host=get("GATEWAY_HOST", "host.docker.internal"),
        gateway_port=_int("GATEWAY_PORT", get("GATEWAY_PORT", "11112"), 1, 65535),
        calling_ae=_ae_title("CALLING_AE", get("CALLING_AE", "SIM_MODALITY")),
        modalities=_modalities(get("MODALITIES", ",".join(SUPPORTED_MODALITIES))),
        station_ae_filter=_ae_title("STATION_AE_FILTER", get("STATION_AE_FILTER", ""), allow_empty=True),
        institution=get("INSTITUTION", "OpenMRS Modality Simulator"),
        auto_acquire=_bool("AUTO_ACQUIRE", get("AUTO_ACQUIRE", "false")),
        poll_seconds=_int("POLL_SECONDS", get("POLL_SECONDS", "30"), 1, 86400),
    )


def _ae_title(name: str, value: str, *, allow_empty: bool = False) -> str:
    if not value and allow_empty:
        return ""
    if not _AE_TITLE.fullmatch(value):
        raise ConfigError(
            f"{PREFIX}{name} is not a valid AE title (1-16 characters, no backslash or control "
            f"characters): {value!r}"
        )
    return value


def _int(name: str, value: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{PREFIX}{name} must be a whole number: {value!r}") from None
    if not low <= number <= high:
        raise ConfigError(f"{PREFIX}{name} must be between {low} and {high}: {number}")
    return number


def _bool(name: str, value: str) -> bool:
    if value.lower() in _TRUE:
        return True
    if value.lower() in _FALSE:
        return False
    raise ConfigError(f"{PREFIX}{name} must be true or false: {value!r}")


def _modalities(value: str) -> tuple[str, ...]:
    listed = [m.strip().upper() for m in value.split(",") if m.strip()]
    if not listed:
        raise ConfigError(f"{PREFIX}MODALITIES must list at least one of {', '.join(SUPPORTED_MODALITIES)}")
    unsupported = [m for m in listed if m not in SUPPORTED_MODALITIES]
    if unsupported:
        raise ConfigError(
            f"{PREFIX}MODALITIES may only list {', '.join(SUPPORTED_MODALITIES)}: {', '.join(unsupported)}"
        )
    return tuple(dict.fromkeys(listed))
