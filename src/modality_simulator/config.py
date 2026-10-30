"""The simulator's settings, read once at startup from the stations file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

from modality_simulator.errors import ConfigError

CONFIG_PATH = Path("/config/stations.yaml")
SUPPORTED_MODALITIES = ("CR", "US", "CT")

# A DICOM AE title: 1-16 characters, with no backslash or control characters.
_AE_TITLE = re.compile(r"[^\\\x00-\x1f\x7f]{1,16}")
# A calling AE is also a URL path segment and a directory name, so it's held to fewer characters.
_CALLING_AE = re.compile(r"[A-Za-z0-9._-]{1,16}")

_TOP_KEYS = ("gateway", "institution", "poll_seconds", "stations")
_GATEWAY_KEYS = ("ae", "host", "port")
_STATION_KEYS = ("name", "calling_ae", "modalities", "station_ae_filter", "auto_acquire")


@dataclass(frozen=True)
class Station:
    """One simulated modality: its own AE title and worklist, with the shared gateway's settings."""

    name: str
    calling_ae: str
    gateway_ae: str
    gateway_host: str = "host.docker.internal"
    gateway_port: int = 11112
    modalities: tuple[str, ...] = SUPPORTED_MODALITIES
    station_ae_filter: str = ""
    institution: str = "OpenMRS Modality Simulator"
    auto_acquire: bool = False
    image_dir: Path = Path("/images")


@dataclass(frozen=True)
class Config:
    stations: tuple[Station, ...]
    poll_seconds: int = 30
    data_dir: Path = Path("/data")


def load(path: Path = CONFIG_PATH) -> Config:
    """Reads the stations file; raises ConfigError naming the file and the bad field."""
    try:
        if path.is_dir():
            raise ConfigError(f"{path} is a directory: it must be the stations file")
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(
            f"No stations file at {path}: with distro-tools, it's modality-simulator-config/stations.yaml "
            f"in the instance directory"
        ) from None
    except (OSError, UnicodeDecodeError) as e:  # e.g. a directory the simulator's user can't read
        raise ConfigError(f"Couldn't read the stations file {path}: {e}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} isn't valid YAML: {e}") from None
    try:
        return _config(data)
    except ConfigError as e:
        raise ConfigError(f"{path}: {e}") from None


def overlaps(stations: Sequence[Station]) -> list[str]:
    """A warning for each pair of stations that would see the same worklist entries: they share a
    modality, and neither has a station AE filter or both have the same one. (A filtered and an
    unfiltered station aren't flagged, though the unfiltered one sees the other's entries too.)"""
    warnings = []
    for i, a in enumerate(stations):
        for b in stations[i + 1:]:
            shared = [m for m in a.modalities if m in b.modalities]
            if shared and a.station_ae_filter == b.station_ae_filter:
                warnings.append(
                    f"Stations {a.calling_ae} and {b.calling_ae} would both see the {', '.join(shared)} entries"
                    + (f" for {a.station_ae_filter}" if a.station_ae_filter else "")
                    + ": with auto-acquire on, both would send them"
                )
    return warnings


def _config(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("the file must be a mapping with gateway and stations")
    _known_keys("", data, _TOP_KEYS)
    gateway = data.get("gateway") or {}
    if not isinstance(gateway, dict):
        raise ConfigError("gateway must be a mapping with at least ae")
    _known_keys("gateway.", gateway, _GATEWAY_KEYS)
    if gateway.get("ae") in (None, ""):
        raise ConfigError("gateway.ae must be set: the gateway's AE title (its Local AE in AdvaPACS)")
    shared = dict(
        gateway_ae=_ae_title("gateway.ae", gateway["ae"]),
        gateway_host=_text("gateway.host", gateway.get("host", "host.docker.internal")),
        gateway_port=_int("gateway.port", gateway.get("port", 11112), 1, 65535),
        institution=_text("institution", data.get("institution", "OpenMRS Modality Simulator")),
    )
    listed = data.get("stations")
    if not isinstance(listed, list) or not listed:
        raise ConfigError("stations must list at least one station")
    stations: list[Station] = []
    for number, item in enumerate(listed, start=1):
        s = _station(f"stations[{number}]", item, shared)
        if any(other.calling_ae == s.calling_ae for other in stations):
            raise ConfigError(f"stations[{number}].calling_ae {s.calling_ae} is already another station's")
        stations.append(s)
    return Config(
        stations=tuple(stations),
        poll_seconds=_int("poll_seconds", data.get("poll_seconds", 30), 1, 86400),
    )


def _station(where: str, item: Any, shared: dict[str, Any]) -> Station:
    if not isinstance(item, dict):
        raise ConfigError(f"{where} must be a mapping with name, calling_ae and modalities")
    _known_keys(f"{where}.", item, _STATION_KEYS)
    for required in ("name", "calling_ae", "modalities"):
        if item.get(required) in (None, ""):
            raise ConfigError(f"{where}.{required} must be set")
    calling_ae = _text(f"{where}.calling_ae", item["calling_ae"])
    if not _CALLING_AE.fullmatch(calling_ae):
        raise ConfigError(
            f"{where}.calling_ae must be 1-16 letters, digits, '.', '_' or '-' (it names the station's tab "
            f"and data directory): {calling_ae!r}"
        )
    return Station(
        name=_text(f"{where}.name", item["name"]),
        calling_ae=calling_ae,
        modalities=_modalities(f"{where}.modalities", item["modalities"]),
        station_ae_filter=_ae_title(f"{where}.station_ae_filter", item.get("station_ae_filter", ""), allow_empty=True),
        auto_acquire=_bool(f"{where}.auto_acquire", item.get("auto_acquire", False)),
        **shared,
    )


def _known_keys(prefix: str, mapping: dict, known: Sequence[str]) -> None:
    unknown = [str(k) for k in mapping if k not in known]
    if unknown:
        raise ConfigError(f"{prefix}{unknown[0]} isn't a setting; expected one of {', '.join(known)}")


def _text(name: str, value: Any) -> str:
    if isinstance(value, (dict, list, bool)) or value is None:
        raise ConfigError(f"{name} must be text: {value!r}")
    return str(value).strip()


def _ae_title(name: str, value: Any, *, allow_empty: bool = False) -> str:
    text = _text(name, value)
    if not text and allow_empty:
        return ""
    if not _AE_TITLE.fullmatch(text):
        raise ConfigError(
            f"{name} is not a valid AE title (1-16 characters, no backslash or control characters): {text!r}"
        )
    return text


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be a whole number: {value!r}")
    if not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}: {value}")
    return value


def _bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false: {value!r}")
    return value


def _modalities(name: str, value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{name} must list at least one of {', '.join(SUPPORTED_MODALITIES)}, e.g. [CR]")
    listed = [_text(name, m).upper() for m in value]
    unsupported = [m for m in listed if m not in SUPPORTED_MODALITIES]
    if unsupported:
        raise ConfigError(f"{name} may only list {', '.join(SUPPORTED_MODALITIES)}: {', '.join(unsupported)}")
    return tuple(dict.fromkeys(listed))
