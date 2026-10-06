# Modality Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Docker image, `partnersinhealth/modality-simulator`, that acts as a fake CR/US/CT modality toward an AdvaPACS gateway (DICOM worklist query, then images sent for the chosen entry), plus the distro-tools fragment that attaches it to an instance.

**Architecture:** One Python process: pynetdicom for DICOM, pydicom + Pillow for images, Flask (served by waitress) for a small console. Modules each do one job (`config`, `dicom_net`, `mwl`, `images`, `acquire`, `results`, `auto`, `web`, `main`), and only `config` reads the environment. Tests: pytest unit tests written first, plus integration tests against a stock Orthanc with its worklist plugin standing in for the gateway.

**Tech Stack:** Python 3.12, pydicom 3, pynetdicom 3, numpy, Pillow ≥ 10.1, pylibjpeg (decoding JPEG library files), Flask 3, waitress, pytest, Docker (multi-arch), GitHub Actions, bats (distro-tools).

**Spec:** `docs/superpowers/specs/2026-10-06-modality-simulator-design.md`

**Ticket:** UHM-9564. Start every commit message with `UHM-9564: `.

## Global Constraints

- All settings are environment variables prefixed `MODALITY_SIMULATOR_`; only `modality_simulator/config.py` reads the environment.
- `MODALITY_SIMULATOR_GATEWAY_AE` is required, with no default. No AE title, host or port is hardcoded anywhere except as a default in `config.py` and in the distro-tools `.env.defaults`.
- Defaults: gateway host `host.docker.internal`, port `11112`, calling AE `SIM_MODALITY`, modalities `CR,US,CT`, station AE filter empty, institution `OpenMRS Modality Simulator`, auto-acquire `false`, poll every `30` seconds.
- AE titles: 1–16 characters, no backslash or control characters.
- Supported modalities: exactly `CR`, `US`, `CT`, with SOP classes Computed Radiography Image Storage, Ultrasound Image Storage and CT Image Storage (CT is a 3-slice series).
- The console listens on port 8080 in the container; the image library is mounted at `/images` (read-only) and data is kept in `/data`.
- An entry without an AccessionNumber is never sent (AdvaPACS would put it in its Validation Queue).
- An acquisition succeeds only if every image is stored (status `0x0000`, or a `0xBxxx` warning).
- Auto-acquire tries a failing accession at most 3 times in total.
- No real patient data in the repo, the tests or the image. Fixtures are generated.
- distro-tools fragments use `${VAR?}`; defaults live only in `.env.defaults`.
- CI follows `openhim-advapacs-mediator`: Docker Hub user `pihci` with secret `DOCKERHUB_PASSWORD`, ci-dashboard notifications, the distro-tools image scan. Images for `linux/amd64` and `linux/arm64`; `main` → `:latest`, `vX.Y.Z` tag → `:X.Y.Z`.

## Review Focus

1. **A library file whose pixels can't be decoded** (truncated, or a compression with no decoder installed): expected to be skipped with a warning and a generated image sent instead. Test: Task 4, `test_undecodable_library_file_is_skipped`.
2. **A library file copied from another patient that still carries that patient's other identifiers** (`OtherPatientIDsSequence`, `RequestAttributesSequence` holding the old accession number): expected to be removed when the file is stamped, so AdvaPACS doesn't match the study to the wrong order. Test: Task 5, `test_stamp_removes_the_source_patients_leftover_identifiers`.
3. **Patient names with HTML or non-Latin characters**: expected to be escaped in the console, drawn into generated images without crashing, and sent as UTF-8. Tests: Task 3, `test_non_latin_names_render`; Task 5, `test_stamp_sets_patient_study_and_new_uids` (checks `ISO_IR 192`); Task 8, `test_patient_names_are_escaped`.
4. **Sending the same accession twice** (double-clicking Acquire, or auto-acquire running after someone acquired it by hand or after a restart): the console disables the button on submit, and auto-acquire never re-sends an accession the log shows as stored. Test: Task 7, `test_skips_accessions_already_stored_even_by_hand_or_before_a_restart`.
5. **A gateway that accepts the association but not the SOP class** (for example, CT not configured): expected to be reported as a failure for each image, not to crash. Test: Task 5, `test_send_all_reports_an_unaccepted_sop_class_per_image`.

---

## File Structure

```
modality-simulator/
├── pyproject.toml                    # package, dependencies, pytest settings
├── Dockerfile
├── .dockerignore
├── .gitignore
├── README.md                         # usage, settings, AdvaPACS setup, manual e2e checklist
├── scripts/smoke-test-image.sh       # builds the image, checks bad config + /health
├── .github/workflows/ci.yml          # tests on PRs; publish + scan on main and tags
├── src/modality_simulator/
│   ├── __init__.py
│   ├── errors.py                     # ConfigError, GatewayError
│   ├── config.py                     # Config dataclass, load(env)
│   ├── dicom_net.py                  # associate(cfg, abstract_syntaxes)
│   ├── mwl.py                        # WorklistEntry, build_query, parse_entry, collect, query_worklist
│   ├── images.py                     # synthetic(entry), from_library(...), images_for(entry, cfg)
│   ├── acquire.py                    # InstanceStatus, AcquisitionResult, stamp, send_all, acquire
│   ├── results.py                    # AcquisitionLog, AccessionHistory
│   ├── auto.py                       # AutoAcquirer
│   ├── web.py                        # create_app(cfg, query, do_acquire, log)
│   └── main.py                       # build(cfg), main()
└── tests/
    ├── factories.py                  # config(), entry(), result(), write_library_file()
    ├── unit/test_config.py
    ├── unit/test_mwl.py
    ├── unit/test_images.py
    ├── unit/test_library.py
    ├── unit/test_acquire.py
    ├── unit/test_results_and_auto.py
    ├── unit/test_web.py
    ├── unit/test_main.py
    └── integration/
        ├── docker-compose.yml        # Orthanc + worklist plugin as the stand-in gateway
        ├── conftest.py               # writes .wl fixtures, starts/stops Orthanc
        └── test_gateway.py
```

distro-tools (`~/development/openmrs/contrib/distro-tools`, branch `modality-simulator`):

```
docker/services/modality-simulator.yaml
docker/services/modality-simulator.env.defaults
docs/services.md                      # new "Modality simulator" section
test/static/compose.bats              # placeholder GATEWAY_AE + a test for the fragment
```

---

### Task 1: Project scaffold and configuration

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/modality_simulator/__init__.py`, `src/modality_simulator/errors.py`, `src/modality_simulator/config.py`, `tests/factories.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `errors.ConfigError(Exception)`, `errors.GatewayError(Exception)`
  - `config.Config` (frozen dataclass): `gateway_ae: str`, `gateway_host: str = "host.docker.internal"`, `gateway_port: int = 11112`, `calling_ae: str = "SIM_MODALITY"`, `modalities: tuple[str, ...] = ("CR", "US", "CT")`, `station_ae_filter: str = ""`, `institution: str = "OpenMRS Modality Simulator"`, `auto_acquire: bool = False`, `poll_seconds: int = 30`, `image_dir: Path = Path("/images")`, `data_dir: Path = Path("/data")`
  - `config.load(env: Mapping[str, str]) -> Config`
  - `config.SUPPORTED_MODALITIES = ("CR", "US", "CT")`
  - `tests/factories.config(**overrides) -> Config` (gateway AE `TEST_GW`)

- [ ] **Step 1: Create the project files**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "modality-simulator"
version = "0.1.0"
description = "A fake CR/US/CT modality for testing an AdvaPACS gateway's worklist and image flow"
requires-python = ">=3.12"
dependencies = [
    "pydicom>=3.0,<4",
    "pynetdicom>=3.0,<4",
    "numpy>=1.26",
    "pillow>=10.1",
    "pylibjpeg[all]>=2.0",
    "flask>=3.0",
    "waitress>=3.0",
]

[project.optional-dependencies]
test = ["pytest>=8", "requests>=2.31"]

[project.scripts]
modality-simulator = "modality_simulator.main:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
markers = ["integration: needs the Orthanc stand-in gateway (docker compose)"]
addopts = "-m 'not integration'"
```

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
tests/integration/worklists/
```

`src/modality_simulator/__init__.py`: empty file.

`src/modality_simulator/errors.py`:

```python
"""Errors the simulator reports to a person, with messages they can act on."""


class ConfigError(Exception):
    """A setting is missing or invalid; the message names the MODALITY_SIMULATOR_* variable."""


class GatewayError(Exception):
    """The gateway couldn't be reached, refused us, or answered with a failure."""
```

`tests/factories.py`:

```python
"""Test data builders shared by the unit and integration tests."""

from modality_simulator.config import Config


def config(**overrides) -> Config:
    return Config(**{"gateway_ae": "TEST_GW", **overrides})
```

Then set up the environment:

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL, because `modality_simulator.config` can't be imported.

- [ ] **Step 4: Implement `config.py`**

`src/modality_simulator/config.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit/test_config.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "UHM-9564: Scaffold the project and read settings from the environment"
```

---

### Task 2: Worklist query

**Files:**
- Create: `src/modality_simulator/dicom_net.py`, `src/modality_simulator/mwl.py`
- Modify: `tests/factories.py` (add `entry()`)
- Test: `tests/unit/test_mwl.py`

**Interfaces:**
- Consumes: `Config`, `GatewayError` (Task 1).
- Produces:
  - `dicom_net.associate(cfg: Config, abstract_syntaxes: list[str]) -> pynetdicom.association.Association` (established, or raises `GatewayError` whose message contains `rejected the association` or `couldn't be reached`)
  - `mwl.WorklistEntry` (frozen dataclass): `patient_id`, `patient_name`, `birth_date`, `sex`, `accession_number`, `procedure`, `modality` (all `str`), `study_instance_uid: str = ""`, `station_ae: str = ""`, `scheduled_date: str = ""`; property `acquirable -> bool`
  - `mwl.build_query(cfg) -> Dataset`, `mwl.parse_entry(identifier: Dataset) -> WorklistEntry`, `mwl.collect(responses, cfg) -> list[WorklistEntry]`, `mwl.query_worklist(cfg) -> list[WorklistEntry]`
  - `tests/factories.entry(modality="CR", accession="ACC-1", **overrides) -> WorklistEntry`

`dicom_net` needs a real peer to test. Task 6 covers it against Orthanc: rejection, unreachable, and success.

- [ ] **Step 1: Add the `entry()` factory**

Append to `tests/factories.py`:

```python
from modality_simulator.mwl import WorklistEntry


def entry(modality: str = "CR", accession: str = "ACC-1", **overrides) -> WorklistEntry:
    values = dict(
        patient_id="P-1", patient_name="DOE^JANE", birth_date="19800101", sex="F",
        accession_number=accession, procedure=f"{modality} test procedure", modality=modality,
        study_instance_uid="", station_ae="SIM_CR", scheduled_date="20261006",
    )
    values.update(overrides)
    return WorklistEntry(**values)
```

(Put the `import` with the other imports at the top of the file.)

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_mwl.py`:

```python
import pytest
from pydicom.dataset import Dataset

from factories import config
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry, build_query, collect, parse_entry


def status(code: int) -> Dataset:
    ds = Dataset()
    ds.Status = code
    return ds


def identifier(accession="ACC-1", modality="CR", station="SIM_CR", name="DOE^JANE") -> Dataset:
    ds = Dataset()
    ds.PatientName = name
    ds.PatientID = "P-1"
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "F"
    ds.AccessionNumber = accession
    ds.RequestedProcedureDescription = "Chest PA"
    ds.StudyInstanceUID = "1.2.3"
    step = Dataset()
    step.Modality = modality
    step.ScheduledStationAETitle = station
    step.ScheduledProcedureStepStartDate = "20261006"
    ds.ScheduledProcedureStepSequence = [step]
    return ds


def test_parse_entry_reads_patient_order_and_step():
    assert parse_entry(identifier()) == WorklistEntry(
        patient_id="P-1", patient_name="DOE^JANE", birth_date="19800101", sex="F",
        accession_number="ACC-1", procedure="Chest PA", modality="CR",
        study_instance_uid="1.2.3", station_ae="SIM_CR", scheduled_date="20261006",
    )


def test_parse_entry_without_a_step_sequence_has_no_modality():
    ds = identifier()
    del ds.ScheduledProcedureStepSequence
    assert parse_entry(ds).modality == ""


def test_collect_gathers_pending_matches_until_success():
    responses = [(status(0xFF00), identifier("A1")), (status(0xFF01), identifier("A2")), (status(0x0000), None)]
    assert [e.accession_number for e in collect(responses, config())] == ["A1", "A2"]


def test_collect_keeps_only_configured_modalities():
    responses = [
        (status(0xFF00), identifier("A1", "CR")), (status(0xFF00), identifier("A2", "MR")),
        (status(0xFF00), identifier("A3", "US")), (status(0x0000), None),
    ]
    entries = collect(responses, config(modalities=("CR",)))
    assert [e.accession_number for e in entries] == ["A1"]


def test_collect_applies_the_station_filter_even_if_the_gateway_ignores_it():
    responses = [
        (status(0xFF00), identifier("A1", station="SIM_CR")),
        (status(0xFF00), identifier("A2", station="OTHER")), (status(0x0000), None),
    ]
    entries = collect(responses, config(station_ae_filter="SIM_CR"))
    assert [e.accession_number for e in entries] == ["A1"]


def test_a_failure_status_is_an_error_not_an_empty_worklist():
    with pytest.raises(GatewayError, match="0xA700"):
        collect([(status(0xA700), None)], config())


def test_no_status_means_the_gateway_did_not_answer():
    with pytest.raises(GatewayError, match="didn't answer"):
        collect([(Dataset(), None)], config())


def test_responses_ending_without_a_final_status_are_an_error():
    with pytest.raises(GatewayError, match="without a final status"):
        collect([(status(0xFF00), identifier())], config())


def test_entry_without_accession_is_listed_but_not_acquirable():
    entries = collect([(status(0xFF00), identifier(accession="")), (status(0x0000), None)], config())
    assert len(entries) == 1
    assert not entries[0].acquirable


def test_build_query_asks_for_every_field_we_read_and_filters_by_station():
    query = build_query(config(station_ae_filter="SIM_US"))
    for keyword in ("PatientName", "PatientID", "PatientBirthDate", "PatientSex",
                    "AccessionNumber", "RequestedProcedureDescription", "StudyInstanceUID"):
        assert keyword in query
    step = query.ScheduledProcedureStepSequence[0]
    assert step.ScheduledStationAETitle == "SIM_US"
    assert step.Modality == ""
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/test_mwl.py -v`
Expected: FAIL, because `modality_simulator.mwl` can't be imported.

- [ ] **Step 4: Implement `dicom_net.py` and `mwl.py`**

`src/modality_simulator/dicom_net.py`:

```python
"""Opening associations to the gateway, with errors a person can act on."""

from __future__ import annotations

from pynetdicom import AE
from pynetdicom.association import Association

from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

CONNECT_TIMEOUT_SECONDS = 10
RESPONSE_TIMEOUT_SECONDS = 30


def associate(cfg: Config, abstract_syntaxes: list[str]) -> Association:
    """An established association from cfg.calling_ae to the gateway, proposing each syntax."""
    ae = AE(ae_title=cfg.calling_ae)
    ae.connection_timeout = CONNECT_TIMEOUT_SECONDS
    ae.acse_timeout = CONNECT_TIMEOUT_SECONDS
    ae.dimse_timeout = RESPONSE_TIMEOUT_SECONDS
    ae.network_timeout = RESPONSE_TIMEOUT_SECONDS
    for uid in abstract_syntaxes:
        ae.add_requested_context(uid)
    assoc = ae.associate(cfg.gateway_host, cfg.gateway_port, ae_title=cfg.gateway_ae)
    if assoc.is_established:
        return assoc
    where = f"{cfg.gateway_ae} at {cfg.gateway_host}:{cfg.gateway_port}"
    if assoc.is_rejected:
        raise GatewayError(
            f"The gateway ({where}) rejected the association from {cfg.calling_ae}: check that "
            f"{cfg.calling_ae} is set up as a Remote AE in AdvaPACS, and that {cfg.gateway_ae} is "
            f"the gateway's Local AE title"
        )
    raise GatewayError(
        f"The gateway ({where}) couldn't be reached, or closed the connection: check the host "
        f"and port, and that the gateway is running (it opens its DICOM port only after AdvaPACS "
        f"has sent it its configuration)"
    )
```

`src/modality_simulator/mwl.py`:

```python
"""Reading the gateway's DICOM Modality Worklist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pydicom.dataset import Dataset
from pynetdicom.sop_class import ModalityWorklistInformationFind

from modality_simulator import dicom_net
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

PENDING = (0xFF00, 0xFF01)
SUCCESS = 0x0000


@dataclass(frozen=True)
class WorklistEntry:
    patient_id: str
    patient_name: str
    birth_date: str
    sex: str
    accession_number: str
    procedure: str
    modality: str
    study_instance_uid: str = ""
    station_ae: str = ""
    scheduled_date: str = ""

    @property
    def acquirable(self) -> bool:
        """Images sent without an accession number land in AdvaPACS's Validation Queue."""
        return bool(self.accession_number)


def build_query(cfg: Config) -> Dataset:
    ds = Dataset()
    ds.PatientName = ""
    ds.PatientID = ""
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.AccessionNumber = ""
    ds.RequestedProcedureDescription = ""
    ds.StudyInstanceUID = ""
    step = Dataset()
    # Matched in collect() rather than at the gateway: a worklist query matches only one modality.
    step.Modality = ""
    step.ScheduledStationAETitle = cfg.station_ae_filter
    step.ScheduledProcedureStepStartDate = ""
    ds.ScheduledProcedureStepSequence = [step]
    return ds


def parse_entry(identifier: Dataset) -> WorklistEntry:
    steps = identifier.get("ScheduledProcedureStepSequence") or [Dataset()]
    step = steps[0]
    return WorklistEntry(
        patient_id=_text(identifier, "PatientID"),
        patient_name=_text(identifier, "PatientName"),
        birth_date=_text(identifier, "PatientBirthDate"),
        sex=_text(identifier, "PatientSex"),
        accession_number=_text(identifier, "AccessionNumber"),
        procedure=_text(identifier, "RequestedProcedureDescription"),
        modality=_text(step, "Modality").upper(),
        study_instance_uid=_text(identifier, "StudyInstanceUID"),
        station_ae=_text(step, "ScheduledStationAETitle"),
        scheduled_date=_text(step, "ScheduledProcedureStepStartDate"),
    )


def collect(responses: Iterable[tuple[Dataset, Dataset | None]], cfg: Config) -> list[WorklistEntry]:
    """The entries in a C-FIND's responses for cfg's modalities (and station, if filtered)."""
    entries = []
    for status, identifier in responses:
        if "Status" not in status:
            raise GatewayError(
                "The gateway didn't answer the worklist query (the association was aborted or timed out)"
            )
        code = int(status.Status)
        if code in PENDING:
            if identifier is None:
                continue
            entry = parse_entry(identifier)
            if entry.modality not in cfg.modalities:
                continue
            if cfg.station_ae_filter and entry.station_ae != cfg.station_ae_filter:
                continue
            entries.append(entry)
        elif code == SUCCESS:
            return entries
        else:
            raise GatewayError(f"The gateway's worklist query failed with status 0x{code:04X}")
    raise GatewayError("The worklist query ended without a final status")


def query_worklist(cfg: Config) -> list[WorklistEntry]:
    assoc = dicom_net.associate(cfg, [ModalityWorklistInformationFind])
    try:
        return collect(assoc.send_c_find(build_query(cfg), ModalityWorklistInformationFind), cfg)
    finally:
        assoc.release()


def _text(ds: Dataset, keyword: str) -> str:
    return str(ds.get(keyword, "") or "").strip()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Query the gateway's worklist"
```

---

### Task 3: Generated images

**Files:**
- Create: `src/modality_simulator/images.py`
- Test: `tests/unit/test_images.py`

**Interfaces:**
- Consumes: `WorklistEntry` (Task 2).
- Produces:
  - `images.SOP_CLASSES: dict[str, str]` (`"CR"`, `"US"`, `"CT"` → SOP class UID)
  - `images.CT_SLICES = 3`
  - `images.SYNTHETIC = "synthetic"`
  - `images.synthetic(entry: WorklistEntry) -> list[Dataset]`: unstamped datasets with `file_meta` (Explicit VR Little Endian), `SOPClassUID`, `Modality` and pixel data. No patient, study or instance UIDs: those are set in Task 5.

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_images.py`:

```python
import numpy as np
import pytest
from pydicom.uid import ExplicitVRLittleEndian
from pynetdicom.sop_class import CTImageStorage, ComputedRadiographyImageStorage, UltrasoundImageStorage

from factories import entry
from modality_simulator.images import CT_SLICES, synthetic


@pytest.mark.parametrize("modality,sop_class,count", [
    ("CR", ComputedRadiographyImageStorage, 1),
    ("US", UltrasoundImageStorage, 1),
    ("CT", CTImageStorage, CT_SLICES),
])
def test_synthetic_images_have_the_modality_sop_class_and_pixels(modality, sop_class, count):
    images = synthetic(entry(modality))
    assert len(images) == count
    for ds in images:
        assert ds.SOPClassUID == sop_class
        assert ds.file_meta.MediaStorageSOPClassUID == sop_class
        assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
        assert ds.Modality == modality
        assert ds.pixel_array.shape == (ds.Rows, ds.Columns)
        assert ds.pixel_array.max() > 0


def test_the_entry_is_burned_into_the_pixels():
    first = synthetic(entry("CR", accession="ACC-1"))[0].pixel_array
    second = synthetic(entry("CR", accession="ACC-2"))[0].pixel_array
    assert not np.array_equal(first, second)


def test_ct_slices_share_a_frame_of_reference_and_step_through_the_patient():
    slices = synthetic(entry("CT"))
    assert len({ds.FrameOfReferenceUID for ds in slices}) == 1
    assert [float(ds.ImagePositionPatient[2]) for ds in slices] == [0.0, 5.0, 10.0]


def test_non_latin_names_render():
    ds = synthetic(entry("US", patient_name="Ñoño^Zoë 李 <b>"))[0]
    assert ds.pixel_array.max() > 0


def test_unsupported_modality_is_an_error():
    with pytest.raises(ValueError, match="MR"):
        synthetic(entry("MR"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_images.py -v`
Expected: FAIL, because `modality_simulator.images` can't be imported.

- [ ] **Step 3: Implement `synthetic()` in `images.py`**

`src/modality_simulator/images.py`:

```python
"""Images to send: a real file from the library when there is one, otherwise a generated one."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import CTImageStorage, ComputedRadiographyImageStorage, UltrasoundImageStorage

from modality_simulator.mwl import WorklistEntry

SOP_CLASSES = {
    "CR": ComputedRadiographyImageStorage,
    "US": UltrasoundImageStorage,
    "CT": CTImageStorage,
}
CT_SLICES = 3
CT_SLICE_MM = 5.0
SYNTHETIC = "synthetic"


def synthetic(entry: WorklistEntry) -> list[Dataset]:
    """Generated images for entry's modality, with its details drawn on them. Not yet stamped."""
    lines = [
        entry.patient_name or "(no name)",
        f"ID {entry.patient_id}",
        f"ACC {entry.accession_number}",
        entry.procedure,
        "SIMULATED IMAGE - NOT FOR DIAGNOSIS",
    ]
    if entry.modality == "CR":
        return [_cr(lines)]
    if entry.modality == "US":
        return [_us(lines)]
    if entry.modality == "CT":
        frame = generate_uid()
        return [_ct(lines + [f"SLICE {n} OF {CT_SLICES}"], n, frame) for n in range(1, CT_SLICES + 1)]
    raise ValueError(f"No generated image for modality {entry.modality!r}")


def _render(lines: list[str], width: int, height: int) -> np.ndarray:
    """8-bit greyscale: a body-like ellipse with the lines of text over it."""
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.2, height * 0.15, width * 0.8, height * 0.85), fill=70)
    size = max(14, height // 24)
    font = ImageFont.load_default(size=size)
    for row, line in enumerate(lines):
        draw.text((20, 20 + row * (size + 8)), line, fill=255, font=font)
    return np.asarray(image, dtype=np.uint8)


def _dataset(modality: str, pixels: np.ndarray, bits_stored: int) -> Dataset:
    sop_class = SOP_CLASSES[modality]
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = sop_class
    ds.Modality = modality
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows, ds.Columns = pixels.shape
    ds.BitsAllocated = pixels.dtype.itemsize * 8
    ds.BitsStored = bits_stored
    ds.HighBit = bits_stored - 1
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    return ds


def _twelve_bit(pixels: np.ndarray) -> np.ndarray:
    return pixels.astype(np.uint16) * 16


def _cr(lines: list[str]) -> Dataset:
    ds = _dataset("CR", _twelve_bit(_render(lines, 1024, 1024)), 12)
    ds.BodyPartExamined = ""
    ds.ViewPosition = ""
    return ds


def _us(lines: list[str]) -> Dataset:
    return _dataset("US", _render(lines, 640, 480), 8)


def _ct(lines: list[str], number: int, frame_of_reference: str) -> Dataset:
    ds = _dataset("CT", _twelve_bit(_render(lines, 512, 512)), 12)
    ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
    ds.FrameOfReferenceUID = frame_of_reference
    ds.KVP = "120"
    ds.AcquisitionNumber = 1
    ds.SliceThickness = str(CT_SLICE_MM)
    ds.PixelSpacing = [0.7, 0.7]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [-179.2, -179.2, CT_SLICE_MM * (number - 1)]
    ds.RescaleIntercept = "-1024"
    ds.RescaleSlope = "1"
    return ds
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit/test_images.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Generate CR, US and CT images with the worklist entry drawn on them"
```

---

### Task 4: Image library

**Files:**
- Modify: `src/modality_simulator/images.py` (add `from_library`, `images_for`)
- Modify: `tests/factories.py` (add `write_library_file`)
- Test: `tests/unit/test_library.py`

**Interfaces:**
- Consumes: `synthetic`, `SYNTHETIC` (Task 3), `Config.image_dir`.
- Produces:
  - `images.from_library(image_dir: Path, modality: str, choose=random.choice) -> tuple[Dataset, Path] | None`: a readable, uncompressed library dataset of that modality, or `None`
  - `images.images_for(entry, cfg, choose=random.choice) -> tuple[list[Dataset], str]`: the datasets to send (unstamped) and their source (file name, or `"synthetic"`)
  - `tests/factories.write_library_file(path: Path, modality="CR", compressed=False) -> Dataset`

- [ ] **Step 1: Add the `write_library_file()` factory**

Append to `tests/factories.py` (imports at the top):

```python
from pathlib import Path

from pydicom.dataset import Dataset
from pydicom.uid import RLELossless, generate_uid

from modality_simulator.images import synthetic


def write_library_file(path: Path, modality: str = "CR", compressed: bool = False) -> Dataset:
    """A complete DICOM file for the image library, from a made-up patient."""
    source = entry(modality, accession="LIB-ACC", patient_id="LIB-1", patient_name="LIBRARY^SOURCE")
    ds = synthetic(source)[0]
    ds.PatientName = source.patient_name
    ds.PatientID = source.patient_id
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    if compressed:
        ds.compress(RLELossless)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)
    return ds
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_library.py`:

```python
import logging

import numpy as np
from pydicom.uid import ExplicitVRLittleEndian

from factories import config, entry, write_library_file
from modality_simulator.images import SYNTHETIC, from_library, images_for


def first(candidates):
    return candidates[0]


def test_no_library_directory_means_generated_images(tmp_path):
    datasets, source = images_for(entry("CR"), config(image_dir=tmp_path / "missing"))
    assert source == SYNTHETIC
    assert len(datasets) == 1


def test_picks_a_file_of_the_entrys_modality(tmp_path):
    write_library_file(tmp_path / "a-cr.dcm", "CR")
    write_library_file(tmp_path / "nested" / "b-us.dcm", "US")
    datasets, source = images_for(entry("US"), config(image_dir=tmp_path), choose=first)
    assert source == "b-us.dcm"
    assert datasets[0].Modality == "US"


def test_no_file_of_the_modality_means_generated_images(tmp_path):
    write_library_file(tmp_path / "a-cr.dcm", "CR")
    _, source = images_for(entry("CT"), config(image_dir=tmp_path))
    assert source == SYNTHETIC


def test_a_compressed_file_comes_back_uncompressed(tmp_path):
    original = write_library_file(tmp_path / "rle.dcm", "CR", compressed=True)
    ds, _ = from_library(tmp_path, "CR", choose=first)
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert np.array_equal(ds.pixel_array, original.pixel_array)


def test_non_dicom_and_non_image_files_are_ignored(tmp_path):
    (tmp_path / "README.txt").write_text("not DICOM")
    no_pixels = write_library_file(tmp_path / "no-pixels.dcm", "CR")
    del no_pixels.PixelData
    no_pixels.save_as(tmp_path / "no-pixels.dcm", enforce_file_format=True)
    assert from_library(tmp_path, "CR") is None


def test_undecodable_library_file_is_skipped(tmp_path, caplog):
    path = tmp_path / "truncated.dcm"
    write_library_file(path, "CR")
    path.write_bytes(path.read_bytes()[:-5000])
    with caplog.at_level(logging.WARNING):
        datasets, source = images_for(entry("CR"), config(image_dir=tmp_path))
    assert source == SYNTHETIC
    assert "truncated.dcm" in caplog.text


def test_a_bad_file_is_skipped_for_a_good_one(tmp_path):
    bad = tmp_path / "a-bad.dcm"
    write_library_file(bad, "CR")
    bad.write_bytes(bad.read_bytes()[:-5000])
    write_library_file(tmp_path / "b-good.dcm", "CR")
    _, source = images_for(entry("CR"), config(image_dir=tmp_path), choose=first)
    assert source == "b-good.dcm"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/test_library.py -v`
Expected: FAIL, because `from_library` and `images_for` can't be imported.

- [ ] **Step 4: Implement `from_library()` and `images_for()`**

Add to `src/modality_simulator/images.py`. Imports go at the top, merged with the existing ones; the functions go after `synthetic()`:

```python
import logging
import random
from pathlib import Path
from typing import Callable, Sequence

import pydicom

from modality_simulator.config import Config

log = logging.getLogger(__name__)

Chooser = Callable[[Sequence[Path]], Path]


def images_for(entry: WorklistEntry, cfg: Config, choose: Chooser = random.choice) -> tuple[list[Dataset], str]:
    """The datasets to send for entry, unstamped, and where they came from: a file name, or "synthetic"."""
    found = from_library(cfg.image_dir, entry.modality, choose)
    if found is not None:
        ds, path = found
        return [ds], path.name
    return synthetic(entry), SYNTHETIC


def from_library(image_dir: Path, modality: str, choose: Chooser = random.choice) -> tuple[Dataset, Path] | None:
    """A library file of this modality, decoded and uncompressed, or None if there's no usable one."""
    candidates = _files_of_modality(image_dir, modality)
    while candidates:
        path = choose(candidates)
        candidates.remove(path)
        ds = _load_uncompressed(path)
        if ds is not None:
            return ds, path
    return None


def _files_of_modality(image_dir: Path, modality: str) -> list[Path]:
    if not image_dir.is_dir():
        return []
    matches = []
    for path in sorted(p for p in image_dir.rglob("*") if p.is_file()):
        try:
            header = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception as e:  # anything that isn't readable DICOM: READMEs, partial copies
            log.debug("Not a DICOM file, skipping %s: %s", path, e)
            continue
        if "SOPClassUID" in header and str(header.get("Modality", "")).upper() == modality:
            matches.append(path)
    return matches


def _load_uncompressed(path: Path) -> Dataset | None:
    try:
        ds = pydicom.dcmread(path)
        if "PixelData" not in ds:
            log.warning("Skipping library file %s: it has no pixel data", path)
            return None
        if ds.file_meta.TransferSyntaxUID.is_compressed:
            ds.decompress()
        ds.pixel_array  # decodes the pixels, so a truncated file is caught here and not at the gateway
        return ds
    except Exception as e:
        log.warning("Skipping library file %s: %s", path, e)
        return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Send a real file from the image library when there is one"
```

---

### Task 5: Stamping and sending (acquire)

**Files:**
- Create: `src/modality_simulator/acquire.py`
- Modify: `tests/factories.py` (add `result()`)
- Test: `tests/unit/test_acquire.py`

**Interfaces:**
- Consumes: `dicom_net.associate` (Task 2), `images.images_for` (Task 4), `WorklistEntry`, `Config`, `GatewayError`.
- Produces:
  - `acquire.InstanceStatus` (frozen dataclass): `sop_instance_uid: str`, `status: int | None`, `detail: str = ""`; property `stored -> bool`
  - `acquire.AcquisitionResult` (frozen dataclass): `accession_number`, `patient_id`, `modality`, `study_instance_uid`, `source`, `at` (all `str`), `instances: tuple[InstanceStatus, ...] = ()`, `error: str = ""`; property `ok -> bool`
  - `acquire.stamp(datasets, entry, cfg, now: datetime) -> list[Dataset]`
  - `acquire.send_all(datasets, cfg) -> tuple[InstanceStatus, ...]` (raises `GatewayError` if there's no association)
  - `acquire.acquire(entry, cfg, *, now=datetime.now, pick_images=images.images_for, send=send_all) -> AcquisitionResult` (never raises `GatewayError`)
  - `tests/factories.result(entry, ok=True, error="") -> AcquisitionResult`

- [ ] **Step 1: Add the `result()` factory**

Append to `tests/factories.py` (import at the top):

```python
from modality_simulator.acquire import AcquisitionResult, InstanceStatus


def result(e: WorklistEntry, ok: bool = True, error: str = "") -> AcquisitionResult:
    return AcquisitionResult(
        accession_number=e.accession_number, patient_id=e.patient_id, modality=e.modality,
        study_instance_uid="1.2.3", source="synthetic", at="2026-10-06T12:00:00",
        instances=(InstanceStatus("1.2.3.4", 0x0000 if ok else 0xA700),),
        error="" if ok else (error or "1 of 1 images weren't stored: 0xA700"),
    )
```

- [ ] **Step 2: Write the failing tests**

`tests/unit/test_acquire.py`:

```python
import json
from dataclasses import asdict
from datetime import datetime

import pytest
from pydicom.dataset import Dataset

from factories import config, entry
from modality_simulator import acquire as acquire_module
from modality_simulator.acquire import InstanceStatus, acquire, send_all, stamp
from modality_simulator.errors import GatewayError
from modality_simulator.images import synthetic

NOW = datetime(2026, 10, 6, 14, 30, 5)


def sent_ok(datasets, cfg):
    return tuple(InstanceStatus(str(ds.SOPInstanceUID), 0x0000) for ds in datasets)


def test_stamp_sets_patient_study_and_new_uids():
    e = entry("CT", study_instance_uid="1.2.826.0.1.99", patient_name="Ñoño^Zoë")
    stamped = stamp(synthetic(e), e, config(institution="Test Hospital", calling_ae="SIM_CT"), NOW)
    assert len({ds.SOPInstanceUID for ds in stamped}) == len(stamped)
    assert len({ds.SeriesInstanceUID for ds in stamped}) == 1
    assert [ds.InstanceNumber for ds in stamped] == [1, 2, 3]
    for ds in stamped:
        assert ds.PatientName == "Ñoño^Zoë"
        assert ds.PatientID == e.patient_id
        assert ds.PatientBirthDate == e.birth_date
        assert ds.PatientSex == e.sex
        assert ds.AccessionNumber == e.accession_number
        assert ds.StudyInstanceUID == "1.2.826.0.1.99"
        assert ds.StudyDescription == e.procedure
        assert ds.StudyDate == "20261006" and ds.StudyTime == "143005"
        assert ds.Modality == "CT"
        assert ds.InstitutionName == "Test Hospital"
        assert ds.StationName == "SIM_CT"
        assert ds.SpecificCharacterSet == "ISO_IR 192"
        assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
        assert ds.file_meta.SourceApplicationEntityTitle == "SIM_CT"


def test_stamp_generates_a_study_uid_when_the_worklist_has_none():
    e = entry("CR", study_instance_uid="")
    first = stamp(synthetic(e), e, config(), NOW)[0].StudyInstanceUID
    second = stamp(synthetic(e), e, config(), NOW)[0].StudyInstanceUID
    assert first and second and first != second


def test_stamp_removes_the_source_patients_leftover_identifiers():
    e = entry("CR")
    ds = synthetic(e)[0]
    ds.OtherPatientIDs = "OLD-ID"
    ds.IssuerOfPatientID = "OLD-HOSPITAL"
    ds.PatientAge = "070Y"
    old_request = Dataset()
    old_request.AccessionNumber = "OLD-ACC"
    ds.RequestAttributesSequence = [old_request]
    stamped = stamp([ds], e, config(), NOW)[0]
    for keyword in ("OtherPatientIDs", "IssuerOfPatientID", "PatientAge", "RequestAttributesSequence"):
        assert keyword not in stamped


def test_acquire_succeeds_when_every_image_is_stored():
    result = acquire(entry("CT"), config(), now=lambda: NOW, send=sent_ok,
                     pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert result.ok
    assert len(result.instances) == 3
    assert result.source == "synthetic"
    assert result.at == "2026-10-06T14:30:05"
    assert result.error == ""


def test_warning_statuses_count_as_stored():
    def warned(datasets, cfg):
        return tuple(InstanceStatus(str(ds.SOPInstanceUID), 0xB000) for ds in datasets)
    result = acquire(entry("CR"), config(), send=warned, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert result.ok


def test_a_partial_failure_is_a_failure_with_the_statuses():
    def one_fails(datasets, cfg):
        codes = [0x0000, 0xA700, 0x0000]
        return tuple(InstanceStatus(str(ds.SOPInstanceUID), c) for ds, c in zip(datasets, codes))
    result = acquire(entry("CT"), config(), send=one_fails, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert not result.ok
    assert "1 of 3 images weren't stored" in result.error
    assert "0xA700" in result.error


def test_a_gateway_error_is_a_failed_result_not_an_exception():
    def unreachable(datasets, cfg):
        raise GatewayError("The gateway couldn't be reached")
    result = acquire(entry("CR"), config(), send=unreachable, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert not result.ok
    assert result.error == "The gateway couldn't be reached"


def test_an_entry_without_accession_is_never_sent():
    def must_not_send(datasets, cfg):
        raise AssertionError("sent")
    result = acquire(entry("CR", accession=""), config(), send=must_not_send)
    assert not result.ok
    assert "Validation Queue" in result.error


def test_results_serialize_to_json():
    result = acquire(entry("CR"), config(), send=sent_ok, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert json.loads(json.dumps(asdict(result)))["instances"][0]["status"] == 0


class FakeAssociation:
    def __init__(self, accepted_sop_classes):
        self.accepted = accepted_sop_classes
        self.released = False

    def send_c_store(self, ds):
        if ds.SOPClassUID not in self.accepted:
            raise ValueError(f"No presentation context for '{ds.SOPClassUID}' has been accepted")
        status = Dataset()
        status.Status = 0x0000
        return status

    def release(self):
        self.released = True


def test_send_all_reports_an_unaccepted_sop_class_per_image(monkeypatch):
    fake = FakeAssociation(accepted_sop_classes=set())
    monkeypatch.setattr(acquire_module.dicom_net, "associate", lambda cfg, syntaxes: fake)
    e = entry("CT")
    statuses = send_all(stamp(synthetic(e), e, config(), NOW), config())
    assert [s.stored for s in statuses] == [False, False, False]
    assert "No presentation context" in statuses[0].detail
    assert fake.released


def test_send_all_proposes_each_sop_class_once(monkeypatch):
    proposed = []
    def fake_associate(cfg, syntaxes):
        proposed.extend(syntaxes)
        return FakeAssociation(accepted_sop_classes=set(syntaxes))
    monkeypatch.setattr(acquire_module.dicom_net, "associate", fake_associate)
    e = entry("CT")
    statuses = send_all(stamp(synthetic(e), e, config(), NOW), config())
    assert all(s.stored for s in statuses)
    assert len(proposed) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/unit/test_acquire.py -v`
Expected: FAIL, because `modality_simulator.acquire` can't be imported.

- [ ] **Step 4: Implement `acquire.py`**

`src/modality_simulator/acquire.py`:

```python
"""Acquiring a worklist entry: its images, stamped with its details, stored to the gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from modality_simulator import dicom_net, images
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry

# Identifiers a library file may still carry from the patient it came from. Left in, they'd
# contradict the worklist entry; RequestAttributesSequence holds the old accession number, which
# AdvaPACS could match on.
_LEFTOVER_IDENTIFIERS = (
    "OtherPatientIDs", "OtherPatientIDsSequence", "OtherPatientNames", "IssuerOfPatientID",
    "PatientAge", "PatientAddress", "PatientTelephoneNumbers", "PatientComments",
    "RequestAttributesSequence", "ReferencedStudySequence",
)


@dataclass(frozen=True)
class InstanceStatus:
    sop_instance_uid: str
    status: int | None  # None: no response
    detail: str = ""

    @property
    def stored(self) -> bool:
        # 0xB000-0xBFFF are warnings: stored, possibly with changes the gateway made.
        return self.status is not None and (self.status == 0x0000 or 0xB000 <= self.status <= 0xBFFF)


@dataclass(frozen=True)
class AcquisitionResult:
    accession_number: str
    patient_id: str
    modality: str
    study_instance_uid: str
    source: str
    at: str
    instances: tuple[InstanceStatus, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.instances) and all(i.stored for i in self.instances)


def stamp(datasets: Sequence[Dataset], entry: WorklistEntry, cfg: Config, now: datetime) -> list[Dataset]:
    """Makes datasets one new series of entry's study, as if this modality had just taken them."""
    study_uid = entry.study_instance_uid or generate_uid()
    series_uid = generate_uid()
    date, time = now.strftime("%Y%m%d"), now.strftime("%H%M%S")
    for number, ds in enumerate(datasets, start=1):
        for keyword in _LEFTOVER_IDENTIFIERS:
            if keyword in ds:
                delattr(ds, keyword)
        ds.SpecificCharacterSet = "ISO_IR 192"
        ds.PatientName = entry.patient_name
        ds.PatientID = entry.patient_id
        ds.PatientBirthDate = entry.birth_date
        ds.PatientSex = entry.sex
        ds.AccessionNumber = entry.accession_number
        ds.ReferringPhysicianName = ""
        ds.StudyInstanceUID = study_uid
        ds.StudyID = "1"
        ds.StudyDate = date
        ds.StudyTime = time
        ds.StudyDescription = entry.procedure
        ds.SeriesInstanceUID = series_uid
        ds.SeriesNumber = 1
        ds.SeriesDate = date
        ds.SeriesTime = time
        ds.SeriesDescription = entry.procedure
        ds.SOPInstanceUID = generate_uid()
        ds.InstanceNumber = number
        ds.ContentDate = date
        ds.ContentTime = time
        ds.Modality = entry.modality
        ds.InstitutionName = cfg.institution
        ds.StationName = cfg.calling_ae
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.file_meta.SourceApplicationEntityTitle = cfg.calling_ae
    return list(datasets)


def send_all(datasets: Sequence[Dataset], cfg: Config) -> tuple[InstanceStatus, ...]:
    """C-STOREs each dataset on one association; raises GatewayError only if there's no association."""
    assoc = dicom_net.associate(cfg, sorted({str(ds.SOPClassUID) for ds in datasets}))
    statuses = []
    try:
        for ds in datasets:
            uid = str(ds.SOPInstanceUID)
            try:
                response = assoc.send_c_store(ds)
            except (ValueError, RuntimeError) as e:  # SOP class not accepted, or association gone
                statuses.append(InstanceStatus(uid, None, str(e)))
                continue
            if "Status" in response:
                statuses.append(InstanceStatus(uid, int(response.Status)))
            else:
                statuses.append(InstanceStatus(uid, None, "no response (the association was aborted or timed out)"))
    finally:
        assoc.release()
    return tuple(statuses)


def acquire(
    entry: WorklistEntry,
    cfg: Config,
    *,
    now: Callable[[], datetime] = datetime.now,
    pick_images: Callable[[WorklistEntry, Config], tuple[list[Dataset], str]] = images.images_for,
    send: Callable[[Sequence[Dataset], Config], tuple[InstanceStatus, ...]] = send_all,
) -> AcquisitionResult:
    at = now()
    about = dict(
        accession_number=entry.accession_number, patient_id=entry.patient_id,
        modality=entry.modality, at=at.isoformat(timespec="seconds"),
    )
    if not entry.acquirable:
        return AcquisitionResult(
            **about, study_instance_uid="", source="",
            error="The worklist entry has no accession number: AdvaPACS would put its images in the Validation Queue",
        )
    datasets, source = pick_images(entry, cfg)
    stamped = stamp(datasets, entry, cfg, at)
    study_uid = str(stamped[0].StudyInstanceUID)
    try:
        statuses = send(stamped, cfg)
    except GatewayError as e:
        return AcquisitionResult(**about, study_instance_uid=study_uid, source=source, error=str(e))
    failed = [s for s in statuses if not s.stored]
    error = ""
    if failed:
        reasons = ", ".join(
            s.detail or (f"0x{s.status:04X}" if s.status is not None else "no response") for s in failed
        )
        error = f"{len(failed)} of {len(statuses)} images weren't stored: {reasons}"
    return AcquisitionResult(
        **about, study_instance_uid=study_uid, source=source, instances=statuses, error=error,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Stamp images from the worklist entry and send them to the gateway"
```

---

### Task 6: Integration tests against Orthanc

**Files:**
- Create: `tests/integration/docker-compose.yml`, `tests/integration/conftest.py`, `tests/integration/test_gateway.py`

**Interfaces:**
- Consumes: `mwl.query_worklist`, `acquire.acquire`, `Config`, `GatewayError`, `factories.write_library_file`.
- Produces: the `gateway` session fixture (a `Config` pointing at Orthanc), and `pytest -m integration` as the integration command used by CI (Task 10).

Orthanc stands in for the gateway. `DicomCheckCalledAet` makes it reject a wrong called AE, as AdvaPACS does.

- [ ] **Step 1: Write the compose file**

`tests/integration/docker-compose.yml`:

```yaml
# Orthanc with its worklist plugin, standing in for the AdvaPACS gateway.
services:
  gateway:
    image: orthancteam/orthanc:latest
    environment:
      ORTHANC__NAME: stand-in-gateway
      ORTHANC__DICOM_AET: TEST_GW
      ORTHANC__DICOM_CHECK_CALLED_AET: "true"
      ORTHANC__DICOM_ALWAYS_ALLOW_FIND: "true"
      ORTHANC__DICOM_ALWAYS_ALLOW_STORE: "true"
      ORTHANC__AUTHENTICATION_ENABLED: "false"
      WORKLISTS_PLUGIN_ENABLED: "true"
      ORTHANC__WORKLISTS__ENABLE: "true"
      ORTHANC__WORKLISTS__DATABASE: /worklists
    volumes:
      - ./worklists:/worklists:ro
    ports:
      - "14242:4242"
      - "18042:8042"
```

- [ ] **Step 2: Write the fixtures**

`tests/integration/conftest.py`:

```python
"""Starts the Orthanc stand-in gateway once per session, with generated worklist entries."""

import subprocess
import time
from pathlib import Path

import pytest
import requests
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from modality_simulator.config import Config

HERE = Path(__file__).parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml"), "-p", "modality-simulator-it"]
WORKLISTS = HERE / "worklists"
REST = "http://localhost:18042"
MODALITY_WORKLIST_SOP_CLASS = "1.2.840.10008.5.1.4.31"

# accession, patient ID, patient name, modality, scheduled station AE
FIXTURES = [
    ("ACC-IT-CR1", "IT-P1", "TEST^CR", "CR", "SIM_CR"),
    ("ACC-IT-US1", "IT-P2", "TEST^US", "US", "SIM_US"),
    ("ACC-IT-CT1", "IT-P3", "TEST^CT", "CT", "SIM_CT"),
    ("ACC-IT-LIB1", "IT-P4", "TEST^LIB", "CR", "SIM_CR"),
    ("", "IT-P5", "TEST^NOACC", "CR", "SIM_CR"),
    ("ACC-IT-MR1", "IT-P6", "TEST^MR", "MR", "SIM_MR"),
]


def write_worklist(path: Path, accession: str, patient_id: str, name: str, modality: str, station: str) -> None:
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 100"
    ds.PatientName = name
    ds.PatientID = patient_id
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "F"
    ds.AccessionNumber = accession
    ds.RequestedProcedureID = "1"
    ds.RequestedProcedureDescription = f"{modality} test procedure"
    ds.StudyInstanceUID = generate_uid()
    step = Dataset()
    step.Modality = modality
    step.ScheduledStationAETitle = station
    step.ScheduledProcedureStepStartDate = "20261006"
    step.ScheduledProcedureStepID = "1"
    ds.ScheduledProcedureStepSequence = [step]
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = MODALITY_WORKLIST_SOP_CLASS
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.save_as(path, enforce_file_format=True)


def wait_for(url: str, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if requests.get(url, timeout=2).ok:
                return
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"{url} didn't answer within {seconds}s")
        time.sleep(1)


@pytest.fixture(scope="session")
def gateway():
    WORKLISTS.mkdir(exist_ok=True)
    for old in WORKLISTS.glob("*.wl"):
        old.unlink()
    for number, fixture in enumerate(FIXTURES):
        write_worklist(WORKLISTS / f"{number}.wl", *fixture)
    subprocess.run([*COMPOSE, "up", "-d"], check=True)
    try:
        wait_for(f"{REST}/system")
        yield Config(gateway_ae="TEST_GW", gateway_host="localhost", gateway_port=14242, calling_ae="SIM_TEST")
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], check=False)


def find_study(accession: str) -> dict | None:
    response = requests.post(
        f"{REST}/tools/find",
        json={"Level": "Study", "Query": {"AccessionNumber": accession}, "Expand": True},
        timeout=10,
    )
    response.raise_for_status()
    studies = response.json()
    return studies[0] if studies else None


def instance_count(study: dict) -> int:
    response = requests.get(f"{REST}/studies/{study['ID']}/instances", timeout=10)
    response.raise_for_status()
    return len(response.json())
```

- [ ] **Step 3: Write the tests**

`tests/integration/test_gateway.py`:

```python
from dataclasses import replace

import pytest

from conftest import find_study, instance_count
from factories import write_library_file
from modality_simulator.acquire import acquire
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import query_worklist

pytestmark = pytest.mark.integration


def entry_for(cfg, accession):
    return next(e for e in query_worklist(cfg) if e.accession_number == accession)


def test_worklist_lists_the_configured_modalities(gateway):
    by_name = {e.patient_name: e for e in query_worklist(gateway)}
    assert set(by_name) == {"TEST^CR", "TEST^US", "TEST^CT", "TEST^LIB", "TEST^NOACC"}
    cr = by_name["TEST^CR"]
    assert cr.accession_number == "ACC-IT-CR1"
    assert cr.patient_id == "IT-P1"
    assert cr.modality == "CR"
    assert cr.study_instance_uid
    assert not by_name["TEST^NOACC"].acquirable


def test_station_filter_is_applied_at_the_gateway(gateway):
    entries = query_worklist(replace(gateway, station_ae_filter="SIM_US"))
    assert [e.accession_number for e in entries] == ["ACC-IT-US1"]


def test_a_wrong_called_ae_is_rejected_with_advice(gateway):
    with pytest.raises(GatewayError, match="rejected the association from SIM_TEST"):
        query_worklist(replace(gateway, gateway_ae="NOT_THE_GW"))


def test_an_unreachable_gateway_is_reported(gateway):
    with pytest.raises(GatewayError, match="couldn't be reached"):
        query_worklist(replace(gateway, gateway_port=1))


@pytest.mark.parametrize("accession,count", [("ACC-IT-CR1", 1), ("ACC-IT-US1", 1), ("ACC-IT-CT1", 3)])
def test_acquire_stores_a_study_with_the_worklist_details(gateway, tmp_path, accession, count):
    cfg = replace(gateway, image_dir=tmp_path / "no-library")
    entry = entry_for(cfg, accession)
    result = acquire(entry, cfg)
    assert result.ok, result.error
    study = find_study(accession)
    assert study is not None
    assert study["MainDicomTags"]["StudyInstanceUID"] == entry.study_instance_uid
    assert study["PatientMainDicomTags"]["PatientID"] == entry.patient_id
    assert instance_count(study) == count


def test_acquire_sends_a_compressed_library_file(gateway, tmp_path):
    write_library_file(tmp_path / "library" / "chest.dcm", "CR", compressed=True)
    cfg = replace(gateway, image_dir=tmp_path / "library")
    result = acquire(entry_for(cfg, "ACC-IT-LIB1"), cfg)
    assert result.ok, result.error
    assert result.source == "chest.dcm"
    study = find_study("ACC-IT-LIB1")
    assert study["PatientMainDicomTags"]["PatientID"] == "IT-P4"
```

- [ ] **Step 4: Run the integration tests**

Run: `pytest -m integration -v`
Expected: all PASS. (The first run pulls the Orthanc image.)

If the worklist test finds no entries, run `docker compose -p modality-simulator-it -f tests/integration/docker-compose.yml logs gateway` and check that the worklist plugin loaded and is reading `/worklists`. If it isn't, check the current orthancteam image documentation for how the plugin is enabled, fix the environment in `docker-compose.yml`, and re-run.

If the unreachable test fails because the message differs, check which branch of `dicom_net.associate` ran (pynetdicom sets `is_aborted` when it can't connect). Change only the test's expectation if the message is still accurate and actionable.

- [ ] **Step 5: Run the unit tests too**

Run: `pytest -v`
Expected: all unit tests PASS; integration tests deselected.

- [ ] **Step 6: Commit**

```bash
git add tests/integration
git commit -m "UHM-9564: Test the worklist query and acquisition against Orthanc as a stand-in gateway"
```

---

### Task 7: Acquisition log and auto-acquire

**Files:**
- Create: `src/modality_simulator/results.py`, `src/modality_simulator/auto.py`
- Test: `tests/unit/test_results_and_auto.py`

**Interfaces:**
- Consumes: `AcquisitionResult` (Task 5), `WorklistEntry`, `GatewayError`.
- Produces:
  - `results.AccessionHistory` (dataclass): `stored: bool = False`, `failures: int = 0`
  - `results.AcquisitionLog(path: Path)`: `.append(result: AcquisitionResult) -> None`, `.records() -> list[dict]`, `.recent(limit: int = 20) -> list[dict]` (newest first; each dict has the `AcquisitionResult` fields plus `"ok"`), `.history() -> dict[str, AccessionHistory]`
  - `auto.MAX_ATTEMPTS = 3`
  - `auto.AutoAcquirer(query: Callable[[], list[WorklistEntry]], do_acquire: Callable[[WorklistEntry], AcquisitionResult], log: AcquisitionLog)`: `.run_once() -> None` (raises `GatewayError` if the query fails), `.run_forever(poll_seconds: float, stop: threading.Event) -> None`

`do_acquire` is expected to append its result to the log (`main` does this in Task 9; the tests' fake does the same).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_results_and_auto.py`:

```python
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

    def do_acquire(self, e):
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
    auto = AutoAcquirer(down, lambda e: None, AcquisitionLog(tmp_path / "acquisitions.jsonl"))
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_results_and_auto.py -v`
Expected: FAIL, because `modality_simulator.results` can't be imported.

- [ ] **Step 3: Implement `results.py` and `auto.py`**

`src/modality_simulator/results.py`:

```python
"""The acquisition log: every result, one JSON object per line, under /data."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from modality_simulator.acquire import AcquisitionResult


@dataclass
class AccessionHistory:
    stored: bool = False
    failures: int = 0


class AcquisitionLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, result: AcquisitionResult) -> None:
        record = asdict(result)
        record["ok"] = result.ok
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:  # a line cut short by a crash
                continue
            if isinstance(record, dict):
                records.append(record)
        return records

    def recent(self, limit: int = 20) -> list[dict]:
        return list(reversed(self.records()))[:limit]

    def history(self) -> dict[str, AccessionHistory]:
        histories: dict[str, AccessionHistory] = {}
        for record in self.records():
            h = histories.setdefault(record.get("accession_number", ""), AccessionHistory())
            if record.get("ok"):
                h.stored = True
            else:
                h.failures += 1
        return histories
```

`src/modality_simulator/auto.py`:

```python
"""Auto-acquire: every poll, acquire each worklist entry that hasn't been stored yet."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from modality_simulator.acquire import AcquisitionResult
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.results import AcquisitionLog

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class AutoAcquirer:
    def __init__(
        self,
        query: Callable[[], list[WorklistEntry]],
        do_acquire: Callable[[WorklistEntry], AcquisitionResult],
        log: AcquisitionLog,
    ):
        self._query = query
        self._acquire = do_acquire
        self._log = log

    def run_once(self) -> None:
        for entry in self._query():
            if not entry.acquirable:
                continue
            # Read the log for each entry: the console may have acquired it meanwhile.
            history = self._log.history().get(entry.accession_number)
            if history and (history.stored or history.failures >= MAX_ATTEMPTS):
                continue
            self._acquire(entry)

    def run_forever(self, poll_seconds: float, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except GatewayError as e:
                logger.warning("Auto-acquire couldn't read the worklist: %s", e)
            except Exception:
                logger.exception("Auto-acquire poll failed")
            stop.wait(poll_seconds)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Log acquisitions and optionally acquire new worklist entries automatically"
```

---

### Task 8: Web console

**Files:**
- Create: `src/modality_simulator/web.py`
- Test: `tests/unit/test_web.py`

**Interfaces:**
- Consumes: `Config`, `WorklistEntry`, `AcquisitionResult`, `AcquisitionLog`, `MAX_ATTEMPTS`, `GatewayError`.
- Produces: `web.create_app(cfg: Config, query: Callable[[], list[WorklistEntry]], do_acquire: Callable[[WorklistEntry], AcquisitionResult], log: AcquisitionLog) -> flask.Flask`, with routes `GET /`, `POST /acquire` (form field `accession`; 303 to `/` once acquired, 404 if the accession isn't in the worklist, 502 if the worklist can't be read), and `GET /health` (`{"status": "ok"}`).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_web.py`:

```python
from factories import config, entry, result
from modality_simulator.errors import GatewayError
from modality_simulator.results import AcquisitionLog
from modality_simulator.web import create_app


class Console:
    def __init__(self, tmp_path, entries=(), gateway_error=None, cfg=None):
        self.log = AcquisitionLog(tmp_path / "acquisitions.jsonl")
        self.entries = list(entries)
        self.gateway_error = gateway_error
        self.queries = 0
        self.acquired = []
        app = create_app(cfg or config(), self.query, self.do_acquire, self.log)
        self.client = app.test_client()

    def query(self):
        self.queries += 1
        if self.gateway_error:
            raise GatewayError(self.gateway_error)
        return list(self.entries)

    def do_acquire(self, e):
        self.acquired.append(e)
        r = result(e)
        self.log.append(r)
        return r


def test_lists_entries_with_acquire_buttons(tmp_path):
    page = Console(tmp_path, [entry("CR", accession="ACC-1")]).client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "ACC-1" in html
    assert 'name="accession" value="ACC-1"' in html
    assert "TEST_GW" in html


def test_patient_names_are_escaped(tmp_path):
    html = Console(tmp_path, [entry(patient_name="<script>x</script>")]).client.get("/").get_data(as_text=True)
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_an_entry_without_accession_cannot_be_acquired(tmp_path):
    html = Console(tmp_path, [entry(accession="")]).client.get("/").get_data(as_text=True)
    assert "<button disabled>Acquire</button>" in html
    assert "Validation Queue" in html


def test_a_gateway_error_shows_a_banner(tmp_path):
    page = Console(tmp_path, gateway_error="The gateway couldn't be reached").client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'role="alert"' in html
    assert "couldn&#39;t be reached" in html
    assert "The worklist is empty" not in html


def test_an_empty_worklist_says_so(tmp_path):
    assert "The worklist is empty" in Console(tmp_path).client.get("/").get_data(as_text=True)


def test_acquire_acquires_the_entry_and_redirects(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")])
    response = console.client.post("/acquire", data={"accession": "ACC-2"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/")
    assert [e.accession_number for e in console.acquired] == ["ACC-2"]


def test_acquiring_an_accession_no_longer_in_the_worklist_is_a_404(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1")])
    response = console.client.post("/acquire", data={"accession": "GONE"})
    assert response.status_code == 404
    assert "GONE" in response.get_data(as_text=True)
    assert console.acquired == []


def test_acquiring_with_the_gateway_down_is_a_502(tmp_path):
    console = Console(tmp_path, gateway_error="down")
    response = console.client.post("/acquire", data={"accession": "ACC-1"})
    assert response.status_code == 502
    assert console.acquired == []


def test_recent_results_and_status_are_shown(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")], cfg=config(auto_acquire=True))
    console.log.append(result(entry(accession="ACC-1")))
    for _ in range(3):
        console.log.append(result(entry(accession="ACC-2"), ok=False, error="0xA700 from the gateway"))
    html = console.client.get("/").get_data(as_text=True)
    assert "stored 1 image(s)" in html
    assert "0xA700 from the gateway" in html
    assert "auto-acquire gave up" in html


def test_the_button_is_disabled_once_clicked(tmp_path):
    html = Console(tmp_path, [entry()]).client.get("/").get_data(as_text=True)
    assert "this.querySelector('button').disabled = true" in html


def test_health_does_not_touch_the_gateway(tmp_path):
    console = Console(tmp_path, gateway_error="down")
    response = console.client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert console.queries == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_web.py -v`
Expected: FAIL, because `modality_simulator.web` can't be imported.

- [ ] **Step 3: Implement `web.py`**

`src/modality_simulator/web.py`:

```python
"""The console: the worklist with an Acquire button per entry, and recent results."""

from __future__ import annotations

from typing import Callable

from flask import Flask, redirect, render_template_string, request, url_for

from modality_simulator.acquire import AcquisitionResult
from modality_simulator.auto import MAX_ATTEMPTS
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.results import AcquisitionLog

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Modality simulator</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 1.5rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
  th, td { border-bottom: 1px solid #ddd; padding: .4rem .6rem; text-align: left; vertical-align: top; }
  .banner { background: #fde8e8; border: 1px solid #e0a0a0; padding: .75rem; margin-bottom: 1rem; }
  .why { color: #666; font-size: .9em; }
  .ok { color: #1a7f37; }
  .failed { color: #b42318; }
</style>
</head>
<body>
<h1>Modality simulator</h1>
<p>{{ cfg.calling_ae }} &rarr; {{ cfg.gateway_ae }} at {{ cfg.gateway_host }}:{{ cfg.gateway_port }}
  &middot; {{ cfg.modalities | join(", ") }}
  &middot; auto-acquire {{ "on" if cfg.auto_acquire else "off" }}</p>
{% if error %}<div class="banner" role="alert">{{ error }}</div>{% endif %}
{% if notice %}<div class="banner" role="status">{{ notice }}</div>{% endif %}

<h2>Worklist</h2>
{% if entries %}
<table>
  <tr><th>Patient</th><th>ID</th><th>Accession</th><th>Procedure</th><th>Modality</th>
      <th>Station</th><th>Scheduled</th><th>Status</th><th></th></tr>
  {% for e in entries %}
  {% set h = history.get(e.accession_number) if e.accession_number else none %}
  <tr>
    <td>{{ e.patient_name }}</td><td>{{ e.patient_id }}</td><td>{{ e.accession_number }}</td>
    <td>{{ e.procedure }}</td><td>{{ e.modality }}</td><td>{{ e.station_ae }}</td><td>{{ e.scheduled_date }}</td>
    <td>
      {% if h and h.stored %}<span class="ok">stored</span>
      {% elif h and h.failures %}<span class="failed">failed {{ h.failures }}&times;{% if cfg.auto_acquire and h.failures >= max_attempts %} (auto-acquire gave up){% endif %}</span>
      {% endif %}
    </td>
    <td>
      {% if e.acquirable %}
      <form method="post" action="{{ url_for('acquire_entry') }}" onsubmit="this.querySelector('button').disabled = true">
        <input type="hidden" name="accession" value="{{ e.accession_number }}"><button>Acquire</button>
      </form>
      {% else %}
      <button disabled>Acquire</button>
      <span class="why">No accession number: AdvaPACS would put the images in its Validation Queue</span>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
{% elif not error %}
<p>The worklist is empty.</p>
{% endif %}

<h2>Recent acquisitions</h2>
{% if recent %}
<table>
  <tr><th>When</th><th>Accession</th><th>Patient ID</th><th>Modality</th><th>Images from</th><th>Result</th></tr>
  {% for r in recent %}
  <tr>
    <td>{{ r.at }}</td><td>{{ r.accession_number }}</td><td>{{ r.patient_id }}</td>
    <td>{{ r.modality }}</td><td>{{ r.source }}</td>
    <td>{% if r.ok %}<span class="ok">stored {{ r.instances | length }} image(s)</span>
        {% else %}<span class="failed">{{ r.error }}</span>{% endif %}</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p>Nothing acquired yet.</p>
{% endif %}
</body>
</html>
"""


def create_app(
    cfg: Config,
    query: Callable[[], list[WorklistEntry]],
    do_acquire: Callable[[WorklistEntry], AcquisitionResult],
    log: AcquisitionLog,
) -> Flask:
    app = Flask(__name__)

    def worklist() -> tuple[list[WorklistEntry], str]:
        try:
            return query(), ""
        except GatewayError as e:
            return [], str(e)

    def page(entries: list[WorklistEntry], error: str = "", notice: str = "", status: int = 200):
        html = render_template_string(
            PAGE, cfg=cfg, entries=entries, error=error, notice=notice,
            history=log.history(), recent=log.recent(20), max_attempts=MAX_ATTEMPTS,
        )
        return html, status

    @app.get("/")
    def index():
        entries, error = worklist()
        return page(entries, error)

    @app.post("/acquire")
    def acquire_entry():
        accession = request.form.get("accession", "").strip()
        entries, error = worklist()
        if error:
            return page([], error, status=502)
        entry = next((e for e in entries if e.acquirable and e.accession_number == accession), None)
        if entry is None:
            return page(entries, notice=f"Accession {accession} isn't in the worklist any more", status=404)
        do_acquire(entry)
        return redirect(url_for("index"), code=303)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS. If `test_a_gateway_error_shows_a_banner` fails only on how the apostrophe is escaped, check which entity Jinja's escaping produced (`&#39;` or `&#x27;`) and match that in the test.

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "UHM-9564: Add the web console"
```

---

### Task 9: Entry point, Docker image and README

**Files:**
- Create: `src/modality_simulator/main.py`, `Dockerfile`, `.dockerignore`, `scripts/smoke-test-image.sh`, `README.md`
- Test: `tests/unit/test_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `main.build(cfg) -> tuple[Flask, AutoAcquirer | None]`, `main.main()` (the `modality-simulator` console script), the image, and `scripts/smoke-test-image.sh [image-tag]` (used by CI in Task 10).

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_main.py`:

```python
import pytest

from factories import config
from modality_simulator.auto import AutoAcquirer
from modality_simulator.main import build, main


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/unit/test_main.py -v`
Expected: FAIL, because `modality_simulator.main` can't be imported.

- [ ] **Step 3: Implement `main.py`**

`src/modality_simulator/main.py`:

```python
"""Entry point: read the settings, start auto-acquire if it's on, serve the console."""

from __future__ import annotations

import logging
import os
import sys
import threading

import waitress
from flask import Flask

from modality_simulator import acquire, config, mwl, web
from modality_simulator.auto import AutoAcquirer
from modality_simulator.errors import ConfigError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.results import AcquisitionLog

log = logging.getLogger("modality_simulator")

PORT = 8080


def build(cfg: config.Config) -> tuple[Flask, AutoAcquirer | None]:
    results = AcquisitionLog(cfg.data_dir / "acquisitions.jsonl")
    lock = threading.Lock()  # one acquisition at a time, from the console or auto-acquire

    def query() -> list[WorklistEntry]:
        return mwl.query_worklist(cfg)

    def do_acquire(entry: WorklistEntry) -> acquire.AcquisitionResult:
        with lock:
            result = acquire.acquire(entry, cfg)
            results.append(result)
        if result.ok:
            log.info("Stored %d image(s) for accession %s (images from %s)",
                     len(result.instances), result.accession_number, result.source)
        else:
            log.warning("Acquiring accession %s failed: %s", result.accession_number, result.error)
        return result

    auto = AutoAcquirer(query, do_acquire, results) if cfg.auto_acquire else None
    return web.create_app(cfg, query, do_acquire, results), auto


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("pynetdicom").setLevel(logging.WARNING)
    try:
        cfg = config.load(os.environ)
    except ConfigError as e:
        print(f"modality-simulator: {e}", file=sys.stderr)
        sys.exit(2)
    app, auto = build(cfg)
    if auto is not None:
        threading.Thread(
            target=auto.run_forever, args=(cfg.poll_seconds, threading.Event()),
            name="auto-acquire", daemon=True,
        ).start()
    log.info("Console on port %d; gateway %s at %s:%d; calling AE %s; auto-acquire %s",
             PORT, cfg.gateway_ae, cfg.gateway_host, cfg.gateway_port, cfg.calling_ae,
             "on" if cfg.auto_acquire else "off")
    waitress.serve(app, host="0.0.0.0", port=PORT)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/unit -v`
Expected: all PASS.

- [ ] **Step 5: Write the Dockerfile and the smoke test**

`.dockerignore`:

```
.git
.venv
.pytest_cache
**/__pycache__
*.egg-info
tests
docs
```

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# /data holds the acquisition log; a named volume mounted there takes this ownership.
RUN useradd --system --uid 1000 simulator && mkdir -p /data /images && chown simulator /data
USER simulator

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=3)"
CMD ["modality-simulator"]
```

`scripts/smoke-test-image.sh` (make it executable with `chmod +x`):

```bash
#!/usr/bin/env bash
# Builds the image, checks that it refuses to start without its required setting, then that it
# starts and answers /health. Run by CI, and worth running before tagging a release.
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE="${1:-modality-simulator:smoke}"
docker build -t "$IMAGE" .

echo "==> Without MODALITY_SIMULATOR_GATEWAY_AE it exits 2 and says why"
set +e
output=$(docker run --rm "$IMAGE" 2>&1)
status=$?
set -e
[ "$status" -eq 2 ] || { echo "expected exit status 2, got $status: $output"; exit 1; }
grep -q "MODALITY_SIMULATOR_GATEWAY_AE must be set" <<< "$output" || { echo "no explanation: $output"; exit 1; }

echo "==> With it, the console answers /health"
name="modality-simulator-smoke-$$"
docker run -d --name "$name" -e MODALITY_SIMULATOR_GATEWAY_AE=SMOKE_GW -p 127.0.0.1::8080 "$IMAGE" >/dev/null
trap 'docker rm -f "$name" >/dev/null' EXIT
port=$(docker port "$name" 8080/tcp | head -1 | sed 's/.*://')
for _ in $(seq 30); do
    if curl -fsS "http://127.0.0.1:$port/health"; then
        echo
        echo "==> OK"
        exit 0
    fi
    sleep 1
done
docker logs "$name"
exit 1
```

- [ ] **Step 6: Run the smoke test**

Run: `scripts/smoke-test-image.sh`
Expected: ends with `==> OK`.

- [ ] **Step 7: Write the README**

`README.md`:

````markdown
# Modality simulator

A fake CR, US and CT imaging room for testing the OpenMRS → AdvaPACS order flow without real
equipment. It reads an AdvaPACS gateway's DICOM Modality Worklist and, when you click **Acquire**
in its console, sends images for that worklist entry back to the gateway, stamped with the
entry's patient, accession number and study, as a real modality would.

Published as [`partnersinhealth/modality-simulator`](https://hub.docker.com/r/partnersinhealth/modality-simulator).

## Running it with distro-tools

```bash
export MODALITY_SIMULATOR_GATEWAY_AE=PIH_KOL-CI_GW   # the gateway's Local AE title
openmrs-docker <name> add-service modality-simulator
openmrs-docker <name> start
```

The console is at `http://<host>:8095`. See distro-tools' `docs/services.md` for the fragment.

## Settings

| Variable | Default | |
|---|---|---|
| `MODALITY_SIMULATOR_GATEWAY_AE` | must be set | the gateway's AE title: its Local AE in AdvaPACS |
| `MODALITY_SIMULATOR_GATEWAY_HOST` | `host.docker.internal` | the gateway's host |
| `MODALITY_SIMULATOR_GATEWAY_PORT` | `11112` | the gateway's DICOM port (its Local AE's port) |
| `MODALITY_SIMULATOR_CALLING_AE` | `SIM_MODALITY` | this simulator's AE title |
| `MODALITY_SIMULATOR_MODALITIES` | `CR,US,CT` | which worklist entries to show |
| `MODALITY_SIMULATOR_STATION_AE_FILTER` | empty | only entries scheduled for this station AE |
| `MODALITY_SIMULATOR_INSTITUTION` | `OpenMRS Modality Simulator` | written into every image |
| `MODALITY_SIMULATOR_AUTO_ACQUIRE` | `false` | acquire every new entry without clicking |
| `MODALITY_SIMULATOR_POLL_SECONDS` | `30` | how often auto-acquire reads the worklist |

The container serves its console on port 8080, keeps its acquisition log in `/data`, and reads
its image library from `/images`.

## Setting up AdvaPACS

- The simulator must be a **Remote AE** in AdvaPACS (Configuration > Remote AEs), with an AE title
  exactly matching `MODALITY_SIMULATOR_CALLING_AE`, case included, and allowed to query the
  worklist. If it isn't, the console says the gateway rejected the association.
- `MODALITY_SIMULATOR_GATEWAY_AE` and `MODALITY_SIMULATOR_GATEWAY_PORT` are the gateway's Local AE
  title and port (Configuration > Local AEs).

## Images

By default the simulator generates images: a CR or US image, or a 3-slice CT series, with the
patient, accession number and procedure drawn on them.

To send real images, put DICOM files in the image library (`/images`, or the instance's
`modality-simulator-images/` directory with distro-tools). For each acquisition it picks a
random file of the entry's modality, decompresses it if needed, and replaces its patient, study,
series and instance details. Files it can't read are skipped with a warning.

**Only use de-identified images.** The simulator replaces the patient and study details and
removes a few other patient identifiers, but it can't remove text burned into the pixels or every
private tag.

## Auto-acquire

With `MODALITY_SIMULATOR_AUTO_ACQUIRE=true`, every `POLL_SECONDS` it acquires each worklist entry
that has an accession number and hasn't been stored yet. An entry that fails is tried at most 3
times. The acquisition log in `/data` records what's been done, so a restart doesn't re-send.

## Errors

- An entry without an accession number can't be acquired: AdvaPACS would put its images in the
  Validation Queue.
- If the gateway can't be reached, rejects the association, or fails the worklist query, the
  console shows why and tries again on the next page load or poll.
- An acquisition succeeds only if every image is stored; otherwise the console shows each
  failing status.
- `/health` reports only that the simulator is running, not whether the gateway is reachable.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
pytest                    # unit tests
pytest -m integration     # against Orthanc as a stand-in gateway (needs Docker)
scripts/smoke-test-image.sh
```

## Checking it against a real gateway

1. Set up the simulator as a Remote AE in AdvaPACS (see above), and start it with the gateway's
   Local AE title and port.
2. Place a radiology order in OpenMRS for a test patient, with a CR, US or CT procedure.
3. Open the console: the order appears in the worklist with its accession number.
4. Click **Acquire**: the recent acquisitions table shows `stored N image(s)`.
5. In AdvaPACS, the study appears under that patient and accession number, and is **not** in the
   Validation Queue.
6. Repeat with auto-acquire on: a new order is stored within one poll, and only once.
````

- [ ] **Step 8: Commit**

```bash
git add src tests Dockerfile .dockerignore scripts README.md
git commit -m "UHM-9564: Add the entry point, Docker image and README"
```

---

### Task 10: CI and publishing

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `pytest`, `pytest -m integration`, `scripts/smoke-test-image.sh`.
- Produces: tests on every PR and push; `partnersinhealth/modality-simulator:latest` from `main`, `:X.Y.Z` from `vX.Y.Z` tags, for amd64 and arm64.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:
    branches:
      - main
    tags:
      - "v*.*.*"
  workflow_dispatch:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[test]"
      - run: pytest
      - run: pytest -m integration
      - run: scripts/smoke-test-image.sh

  publish:
    needs: test
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - name: Notify ci-dashboard (build started)
        continue-on-error: true
        uses: PIH/openmrs-contrib-distro-tools/.github/actions/notify-ci-dashboard@main
        with:
          token: ${{ secrets.GHA_WRITE_TOKEN }}
          phase: started
      - uses: actions/checkout@v4
      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          username: pihci
          password: ${{ secrets.DOCKERHUB_PASSWORD }}
      - id: meta
        uses: docker/metadata-action@v5
        with:
          images: partnersinhealth/modality-simulator
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
      - uses: docker/build-push-action@v6
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}

  notify-ci-dashboard:
    needs: publish
    if: always() && github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - name: Notify ci-dashboard (build finished)
        continue-on-error: true
        uses: PIH/openmrs-contrib-distro-tools/.github/actions/notify-ci-dashboard@main
        with:
          token: ${{ secrets.GHA_WRITE_TOKEN }}
          phase: finished

  scan:
    needs: publish
    uses: PIH/openmrs-contrib-distro-tools/.github/workflows/scan-docker-image.yml@main
    with:
      image_name: partnersinhealth/modality-simulator
    permissions:
      security-events: write
```

- [ ] **Step 2: Check the workflow's syntax**

Run: `python -c "import yaml, sys; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo ok` (`pip install pyyaml` first if needed). If `actionlint` is installed, also run `actionlint .github/workflows/ci.yml`.
Expected: `ok`, and no actionlint errors.

The workflow can only really run once the repo is on GitHub. Pushing, and creating `PIH/modality-simulator`, need the user's go-ahead (see Task 12).

- [ ] **Step 3: Commit**

```bash
git add .github
git commit -m "UHM-9564: Test on every PR; publish multi-arch images from main and version tags"
```

---

### Task 11: distro-tools fragment

**Files** (in `~/development/openmrs/contrib/distro-tools`):
- Create: `docker/services/modality-simulator.yaml`, `docker/services/modality-simulator.env.defaults`
- Modify: `test/static/compose.bats` (`setup_file` and a new test at the end), `docs/services.md` (intro line and a new section after "AdvaPACS gateway")

**Interfaces:**
- Consumes: the image `partnersinhealth/modality-simulator` and its settings (Tasks 1 and 9).
- Produces: `openmrs-docker <name> add-service modality-simulator`.

- [ ] **Step 1: Create a branch**

```bash
cd ~/development/openmrs/contrib/distro-tools
git checkout main && git pull --ff-only && git checkout -b modality-simulator
```

- [ ] **Step 2: Write the failing bats test**

In `test/static/compose.bats`, add this line to `setup_file()`, after the `ADVAPACS_GATEWAY_*` export:

```bash
    export MODALITY_SIMULATOR_GATEWAY_AE=PLACEHOLDER_GW
```

Append this test at the end of the file:

```bash
@test "modality-simulator reaches the gateway through the host, keeps its log in a volume, and mounts its image library read-only" {
    local dir="$OPENMRS_DOCKER_HOME/$CONFIG_INSTANCE" args=() f config
    for f in "$dir"/*.yaml; do args+=(-f "$f"); done
    run docker compose --env-file "$dir/env" "${args[@]}" config --format json
    assert_success
    config=$output
    # the gateway uses the host's network, so it's reached through the host; no hard dependency,
    # since the gateway may be outside the instance and opens its port only once configured
    run jq -c '.services["modality-simulator"].extra_hosts' <<< "$config"
    assert_output --partial 'host.docker.internal'
    assert_output --partial 'host-gateway'
    run jq -r '.services["modality-simulator"].depends_on // {} | has("advapacs-gateway")' <<< "$config"
    assert_output false
    run jq -r '.services["modality-simulator"].ports[] | "\(.published):\(.target)"' <<< "$config"
    assert_output '8095:8080'
    # the instance's MODALITY_SIMULATOR_* settings, under the same names; Compose-only ones not passed
    run jq -r '.services["modality-simulator"].environment
        | .MODALITY_SIMULATOR_GATEWAY_AE, .MODALITY_SIMULATOR_GATEWAY_HOST, .MODALITY_SIMULATOR_GATEWAY_PORT,
          has("MODALITY_SIMULATOR_IMAGE_DIR")' <<< "$config"
    assert_output $'PLACEHOLDER_GW\nhost.docker.internal\n11112\nfalse'
    run jq -r '.services["modality-simulator"].volumes[] | "\(.type) \(.target) \(.read_only // false)"' <<< "$config"
    assert_output $'volume /data false\nbind /images true'
    # the image library defaults to a directory in the instance
    run jq -r '.services["modality-simulator"].volumes[] | select(.target == "/images") | .source' <<< "$config"
    assert_output "$dir/modality-simulator-images"
}
```

Run: `test/run static/compose.bats`
Expected: the new test FAILS (there's no `modality-simulator` service yet); the others PASS.

- [ ] **Step 3: Write the fragment and its defaults**

`docker/services/modality-simulator.yaml`:

```yaml
name: ${SERVICE_NAME:?SERVICE_NAME must be set}

# A fake CR/US/CT modality for testing: it reads the AdvaPACS gateway's DICOM worklist and sends
# images for the entries picked in its console (or all of them, with auto-acquire). Its code and
# README: https://github.com/PIH/modality-simulator.
#
# No depends_on advapacs-gateway: the gateway may be outside this instance, and it opens its DICOM
# port only once AdvaPACS has sent it its configuration. The simulator shows why it can't connect
# and retries. The gateway uses the host's network, so it's reached through host.docker.internal.
services:
  modality-simulator:
    container_name: ${SERVICE_NAME:?SERVICE_NAME must be set}-modality-simulator
    image: ${MODALITY_SIMULATOR_IMAGE_NAME?}:${MODALITY_SIMULATOR_IMAGE_TAG?}
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"
    ports:
      - "${MODALITY_SIMULATOR_HOST_PORT?}:8080"
    environment:
      TZ: ${TZ?}
      MODALITY_SIMULATOR_GATEWAY_HOST: ${MODALITY_SIMULATOR_GATEWAY_HOST?}
      MODALITY_SIMULATOR_GATEWAY_PORT: ${MODALITY_SIMULATOR_GATEWAY_PORT?}
      MODALITY_SIMULATOR_GATEWAY_AE: ${MODALITY_SIMULATOR_GATEWAY_AE?}
      MODALITY_SIMULATOR_CALLING_AE: ${MODALITY_SIMULATOR_CALLING_AE?}
      MODALITY_SIMULATOR_MODALITIES: ${MODALITY_SIMULATOR_MODALITIES?}
      MODALITY_SIMULATOR_STATION_AE_FILTER: ${MODALITY_SIMULATOR_STATION_AE_FILTER?}
      MODALITY_SIMULATOR_INSTITUTION: ${MODALITY_SIMULATOR_INSTITUTION?}
      MODALITY_SIMULATOR_AUTO_ACQUIRE: ${MODALITY_SIMULATOR_AUTO_ACQUIRE?}
      MODALITY_SIMULATOR_POLL_SECONDS: ${MODALITY_SIMULATOR_POLL_SECONDS?}
    volumes:
      - modality-simulator-data:/data
      # De-identified DICOM files to send instead of generated images; with none, it generates them.
      - ${MODALITY_SIMULATOR_IMAGE_DIR?}:/images:ro

volumes:
  modality-simulator-data:
```

`docker/services/modality-simulator.env.defaults`:

```bash
# See docker/services/modality-simulator.yaml. The gateway's AE title and port are its Local AE's,
# from AdvaPACS (Configuration > Local AEs); the calling AE must be a Remote AE there.
MODALITY_SIMULATOR_IMAGE_NAME="${MODALITY_SIMULATOR_IMAGE_NAME:-partnersinhealth/modality-simulator}"
MODALITY_SIMULATOR_IMAGE_TAG="${MODALITY_SIMULATOR_IMAGE_TAG:-latest}"
MODALITY_SIMULATOR_HOST_PORT="${MODALITY_SIMULATOR_HOST_PORT:-8095}"
MODALITY_SIMULATOR_GATEWAY_HOST="${MODALITY_SIMULATOR_GATEWAY_HOST:-host.docker.internal}"
MODALITY_SIMULATOR_GATEWAY_PORT="${MODALITY_SIMULATOR_GATEWAY_PORT:-11112}"
MODALITY_SIMULATOR_GATEWAY_AE="${MODALITY_SIMULATOR_GATEWAY_AE:?must be set: the gateway's AE title, its Local AE in AdvaPACS (Configuration > Local AEs)}"
MODALITY_SIMULATOR_CALLING_AE="${MODALITY_SIMULATOR_CALLING_AE:-SIM_MODALITY}"
MODALITY_SIMULATOR_MODALITIES="${MODALITY_SIMULATOR_MODALITIES:-CR,US,CT}"
MODALITY_SIMULATOR_STATION_AE_FILTER="${MODALITY_SIMULATOR_STATION_AE_FILTER:-}"
MODALITY_SIMULATOR_INSTITUTION="${MODALITY_SIMULATOR_INSTITUTION:-OpenMRS Modality Simulator}"
MODALITY_SIMULATOR_AUTO_ACQUIRE="${MODALITY_SIMULATOR_AUTO_ACQUIRE:-false}"
MODALITY_SIMULATOR_POLL_SECONDS="${MODALITY_SIMULATOR_POLL_SECONDS:-30}"
MODALITY_SIMULATOR_IMAGE_DIR="${MODALITY_SIMULATOR_IMAGE_DIR:-./modality-simulator-images}"
```

- [ ] **Step 4: Run the bats tests**

Run: `test/run static/compose.bats`
Expected: all PASS, including the existing "defaults only from .env.defaults" and "every variable says what happens when it's unset" tests.

If `create` stores the image directory as an absolute path rather than `./modality-simulator-images`, the source assertion still holds, because the path resolves to the instance directory. If it fails, print the `.source` value and check how `create` writes relative paths into `env` (compare `SMOKE_TESTS_OUTPUT_DIR`), then fix the assertion to what is correct, not to whatever the output happens to be.

- [ ] **Step 5: Document the service**

In `docs/services.md`, change the intro sentence to:

```markdown
Besides `openmrs-db` and `openmrs`, `docker/services/` has OpenHIM and its mediators, the AdvaPACS
gateway, a modality simulator, PETL, SQL Server, and the smoke tests ([CI workflows](ci-workflows.md#running-smoke-tests-locally)).
```

Insert this section after the "AdvaPACS gateway" section, before "SQL Server":

````markdown
## Modality simulator

`modality-simulator` is a fake CR, US and CT imaging room, for testing the order → worklist →
images flow without real equipment, from
[`partnersinhealth/modality-simulator`](https://github.com/PIH/modality-simulator). It reads the
AdvaPACS gateway's DICOM worklist; click **Acquire** in its console (`http://<host>:8095`) and it
sends images stamped with that entry's patient and accession number back to the gateway.

| Variable | Default | |
|---|---|---|
| `MODALITY_SIMULATOR_GATEWAY_AE` | must be set | the gateway's Local AE title in AdvaPACS |
| `MODALITY_SIMULATOR_GATEWAY_HOST`, `MODALITY_SIMULATOR_GATEWAY_PORT` | `host.docker.internal`, `11112` | where the gateway listens: its Local AE's port, on this host by default |
| `MODALITY_SIMULATOR_CALLING_AE` | `SIM_MODALITY` | the simulator's AE title |
| `MODALITY_SIMULATOR_MODALITIES` | `CR,US,CT` | which worklist entries it shows |
| `MODALITY_SIMULATOR_STATION_AE_FILTER` | empty | only entries scheduled for this station AE |
| `MODALITY_SIMULATOR_INSTITUTION` | `OpenMRS Modality Simulator` | written into every image |
| `MODALITY_SIMULATOR_AUTO_ACQUIRE`, `MODALITY_SIMULATOR_POLL_SECONDS` | `false`, `30` | acquire every new entry on each poll, without clicking |
| `MODALITY_SIMULATOR_HOST_PORT` | `8095` | the console's port on the host |
| `MODALITY_SIMULATOR_IMAGE_DIR` | `./modality-simulator-images` | DICOM files to send instead of generated images; relative to the instance directory |
| `MODALITY_SIMULATOR_IMAGE_NAME`, `MODALITY_SIMULATOR_IMAGE_TAG` | `partnersinhealth/modality-simulator`, `latest` | |

In AdvaPACS, add the simulator as a Remote AE (Configuration > Remote AEs) whose AE title exactly
matches `MODALITY_SIMULATOR_CALLING_AE`, and let it query the worklist. Until then, its console
says the gateway rejected the association.

It doesn't depend on the `advapacs-gateway` service, so it also works with a gateway elsewhere
(set `MODALITY_SIMULATOR_GATEWAY_HOST`). With no files in its image directory it generates
images; any files you add there must be de-identified.

```bash
export MODALITY_SIMULATOR_GATEWAY_AE=PIH_KOL-CI_GW
openmrs-docker <name> add-service modality-simulator
openmrs-docker <name> start
```
````

Run: `test/run static/docs.bats`
Expected: PASS (all links resolve).

- [ ] **Step 6: Run the whole static suite**

Run: `test/run static`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add docker/services/modality-simulator.yaml docker/services/modality-simulator.env.defaults \
    test/static/compose.bats docs/services.md
git commit -m 'UHM-9564: distro-tools: add a "modality-simulator" service'
```

---

### Task 12: Publish (needs the user's go-ahead)

Nothing in this task runs without the user's explicit OK. Each step is outward-facing.

- [ ] **Step 1: Ask the user** whether to: create `PIH/modality-simulator` on GitHub and push `main`; check that the `DOCKERHUB_PASSWORD` and `GHA_WRITE_TOKEN` org secrets are available to the new repo; push the distro-tools `modality-simulator` branch and open a PR; and whether to add a `deploy-to-kol-ci` job like the mediator's (the spec doesn't include it).
- [ ] **Step 2: Do only what they approved.** After the push, check that the CI run passes and `partnersinhealth/modality-simulator:latest` exists for amd64 and arm64 (`docker buildx imagetools inspect partnersinhealth/modality-simulator:latest`).
- [ ] **Step 3: Run the README's "Checking it against a real gateway" checklist** with the user, against the gateway whose Local AE is `PIH_KOL-CI_GW`.
