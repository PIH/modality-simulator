# Multiple Stations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One simulator container runs several stations (simulated modalities), each with its own calling AE, worklist, acquisitions and console tab, configured by a stations file that puppet writes on kol-ci.

**Architecture:** Today's single-station `Config` becomes `Station`: one station's settings, with the shared gateway, institution and image directory copied in. Because `mwl`, `acquire`, `dicom_net` and `images` keep reading the same attribute names, they only change type names. The new `Config` holds the stations plus the process-wide settings (`poll_seconds`, `data_dir`) and is loaded from `/config/stations.yaml`. `main.build()` wires a query, an acquire function, a lock, a log and an optional auto-acquirer per station. `web.create_app()` serves one tab per station.

**Tech Stack:** Python 3.12, pydicom/pynetdicom, Flask + waitress, PyYAML (new), pytest. distro-tools (docker compose fragment + bash). Puppet (no stdlib in its Puppetfile: render YAML from an ERB template with Ruby's `to_yaml`).

**Spec:** `docs/superpowers/specs/2026-10-30-multi-station-design.md`

## Global Constraints

- The stations file is read from `/config/stations.yaml`; there are no `MODALITY_SIMULATOR_*` simulator settings any more.
- Defaults: gateway host `host.docker.internal`, port `11112`, institution `OpenMRS Modality Simulator`, `poll_seconds` `30` (1–86400), station `station_ae_filter` `""`, `auto_acquire` `false`.
- Required: `gateway.ae`, at least one station; per station `name`, `calling_ae`, `modalities` (non-empty, each one of `CR`, `US`, `CT`).
- Calling AEs are unique. An unknown key anywhere is an error. Every error names the field, e.g. `stations[1].calling_ae`.
- A bad or missing file: print `modality-simulator: <message>` to stderr and exit 2, as today.
- Per-station acquisition log: `/data/<calling_ae>/acquisitions.jsonl`, same record format as today.
- Console routes: `/` → 303 redirect to `/station/<first calling_ae>`; `GET /station/<calling_ae>`; `POST /station/<calling_ae>/acquire`; `/health` unchanged.
- In distro-tools and puppet the stations file is `modality-simulator-config/stations.yaml` under the instance dir, mounted read-only as the directory `/config`. Never a `*.yaml` directly in the instance dir.
- Match the surrounding code: its comment density, its messages a person can act on, and its test naming (`test_<behaviour_in_words>`).

**My call (not in the spec):** a `calling_ae` must also match `[A-Za-z0-9._-]{1,16}`, because it becomes a URL path segment and a directory name. The error says why. The gateway AE keeps today's looser DICOM rule.

## Review Focus

1. **A YAML file that isn't a mapping, or has the wrong types:** `stations:` empty, a station given as a string, `modalities: CR` instead of a list, `port: "abc"`, `auto_acquire: "yes"`. Each should be a clean `ConfigError` naming the field, not a `TypeError` traceback. (Task 1 tests.)
2. **`/config/stations.yaml` is a directory, or isn't readable:** Docker can create the mount source as an empty directory. The error should still say which path and that it must be the stations file. (Task 1 test.)
3. **One station's gateway error:** it must not appear on, or blank, another station's tab. (Task 2 test.)
4. **Posting an accession to the wrong station:** a US accession posted to `/station/SIM_CR/acquire` must not be acquired by the CR station. (Task 2 test.)
5. **An unknown station in the URL:** `/station/NOPE` and `POST /station/NOPE/acquire` give a 404 page with links to the real tabs, not a 500. (Task 2 test.)

---

### Task 1: Stations file and the `Station` type

**Files:**
- Modify: `src/modality_simulator/config.py` (rewrite)
- Modify: `src/modality_simulator/mwl.py`, `acquire.py`, `dicom_net.py`, `images.py`, `web.py`: `Config` → `Station` in imports and annotations only
- Modify: `src/modality_simulator/main.py`: `main()` loads the file, and `build()` takes a `Config` but still serves only `cfg.stations[0]` (Task 2 replaces this)
- Modify: `pyproject.toml`: add `"pyyaml>=6.0"` to `dependencies`
- Modify: `tests/factories.py`: `config(**overrides)` → `station(**overrides) -> Station`, plus `config(*stations, **overrides) -> Config`
- Modify: `tests/integration/conftest.py`: the `gateway` fixture yields a `Station`
- Test: `tests/unit/test_config.py` (rewrite), with the other unit tests updated for the renamed factory

**Interfaces:**
- Produces, in `modality_simulator.config`:
  - `CONFIG_PATH = Path("/config/stations.yaml")`
  - `SUPPORTED_MODALITIES = ("CR", "US", "CT")` (unchanged)
  - `@dataclass(frozen=True) class Station`, with fields in this order: `name: str`, `calling_ae: str`, `gateway_ae: str`, `gateway_host: str = "host.docker.internal"`, `gateway_port: int = 11112`, `modalities: tuple[str, ...] = SUPPORTED_MODALITIES`, `station_ae_filter: str = ""`, `institution: str = "OpenMRS Modality Simulator"`, `auto_acquire: bool = False`, `image_dir: Path = Path("/images")`
  - `@dataclass(frozen=True) class Config`: `stations: tuple[Station, ...]`, `poll_seconds: int = 30`, `data_dir: Path = Path("/data")`
  - `load(path: Path = CONFIG_PATH) -> Config`, which raises `ConfigError`
  - `overlaps(stations: Sequence[Station]) -> list[str]`: one warning per pair of stations that would see the same entries
- Produces, in `tests/factories.py`: `station(**overrides) -> Station`, with defaults `name="X-ray"`, `calling_ae="SIM_CR"`, `gateway_ae="TEST_GW"`; and `config(*stations: Station, **overrides) -> Config`, which defaults to `(station(),)`

- [ ] **Step 1: Write the failing tests in `tests/unit/test_config.py`**

Write each file with `tmp_path / "stations.yaml"` and a helper `write(tmp_path, text) -> Path`. The tests:

```python
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

def test_reads_stations_with_defaults(tmp_path):
    cfg = load(write(tmp_path, KOL_CI))
    assert cfg.poll_seconds == 30 and cfg.data_dir == Path("/data")
    cr, us = cfg.stations
    assert cr == Station(name="X-ray", calling_ae="KOL-CI_CR", gateway_ae="PIH_KOL-CI_GW", modalities=("CR",))
    assert cr.gateway_host == "host.docker.internal" and cr.gateway_port == 11112
    assert cr.institution == "OpenMRS Modality Simulator" and cr.auto_acquire is False
    assert us.modalities == ("US",) and us.auto_acquire is True

def test_reads_every_shared_and_station_setting(tmp_path):
    # gateway host/port, institution, poll_seconds, station_ae_filter, modalities [" cr", "us"] -> ("CR", "US"),
    # with duplicates dropped; every station gets the shared gateway and institution

def test_a_missing_file_names_the_path(tmp_path):
    with pytest.raises(ConfigError, match=r"No stations file at .*stations.yaml"):
        load(tmp_path / "stations.yaml")

def test_a_directory_instead_of_the_file_is_explained(tmp_path):   # Review Focus 2
    (tmp_path / "stations.yaml").mkdir()
    with pytest.raises(ConfigError, match=r"stations.yaml is a directory"):
        load(tmp_path / "stations.yaml")

def test_invalid_yaml_is_reported(tmp_path):            # "gateway: [" -> ConfigError matching "isn't valid YAML"

@pytest.mark.parametrize("text, field", [               # Review Focus 1
    ("", "the file"),                                    # empty file: must be a mapping
    ("- a\n- b", "the file"),
    ("stations: []\ngateway: {ae: GW}", "stations"),
    ("gateway: {ae: GW}", "stations"),
    ("stations: [{name: X, calling_ae: A, modalities: [CR]}]", "gateway.ae"),
    ("gateway: {ae: GW}\nstations: [X-ray]", r"stations\[1\]"),
    ("gateway: {ae: GW}\nstations: [{calling_ae: A, modalities: [CR]}]", r"stations\[1\].name"),
    ("gateway: {ae: GW}\nstations: [{name: X, modalities: [CR]}]", r"stations\[1\].calling_ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: CR}]", r"stations\[1\].modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: []}]", r"stations\[1\].modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [MR]}]", r"stations\[1\].modalities"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], auto_acquire: 'yes'}]", r"stations\[1\].auto_acquire"),
    ("gateway: {ae: GW, port: abc}\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "gateway.port"),
    ("gateway: {ae: GW, port: 0}\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "gateway.port"),
    ("gateway: {ae: GW}\npoll_seconds: 0\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "poll_seconds"),
    ("gateway: {ae: 'BAD\\\\AE'}\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "gateway.ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: 'KOL/CR', modalities: [CR]}]", r"stations\[1\].calling_ae"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], station_ae_filter: 'BAD\\\\AE'}]", r"stations\[1\].station_ae_filter"),
    ("gateway: {ae: GW, aet: X}\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "gateway.aet"),
    ("gateway: {ae: GW}\nstation: []\nstations: [{name: X, calling_ae: A, modalities: [CR]}]", "station"),
    ("gateway: {ae: GW}\nstations: [{name: X, calling_ae: A, modalities: [CR], modality: CR}]", r"stations\[1\].modality"),
])
def test_bad_settings_are_errors_naming_the_field(tmp_path, text, field):
    with pytest.raises(ConfigError, match=field):
        load(write(tmp_path, text))

def test_calling_aes_must_be_unique(tmp_path):   # two stations with calling_ae A -> match "stations\[2\].calling_ae.*A.*already"

def test_overlaps_warns_about_stations_that_would_see_the_same_entries():
    cr = station(name="X-ray", calling_ae="CR1", modalities=("CR",))
    assert overlaps([cr, station(name="Ultrasound", calling_ae="US1", modalities=("US",))]) == []
    assert overlaps([cr, station(name="X-ray 2", calling_ae="CR2", modalities=("CR", "US"),
                                 station_ae_filter="ROOM2")]) == []
    [warning] = overlaps([cr, station(name="X-ray 2", calling_ae="CR2", modalities=("CR",))])
    assert "CR1" in warning and "CR2" in warning and "CR" in warning
```

Two stations overlap when they share a modality and neither has a `station_ae_filter`, or both have the same one. One station with a filter and one without do **not** overlap. That's a deliberate simplification, since the unfiltered one sees both rooms' entries; write it down in `overlaps`'s docstring.

- [ ] **Step 2: Run the tests to make sure they fail**

Run: `pytest tests/unit/test_config.py -v`
Expected: FAIL with an ImportError on `Station` / `overlaps`

- [ ] **Step 3: Implement `config.py`**

Keep `_AE_TITLE` and the style of `_ae_title`, `_int` and `_modalities`, but take field paths (`"stations[1].calling_ae"`) instead of env names. Use `yaml.safe_load`. Check types explicitly. Note that YAML gives `bool` and `int`, and `bool` is a subclass of `int`, so reject `True` as a port. Reject unknown keys at the top level (`gateway`, `institution`, `poll_seconds`, `stations`), in `gateway` (`ae`, `host`, `port`) and in each station (`name`, `calling_ae`, `modalities`, `station_ae_filter`, `auto_acquire`). Messages start with the file's path, e.g. `/config/stations.yaml: stations[1].calling_ae ...`. The missing-file message also says that on distro-tools the file is `modality-simulator-config/stations.yaml` in the instance directory.

- [ ] **Step 4: Rename the uses**

In `mwl.py`, `acquire.py`, `dicom_net.py`, `images.py` and `web.py`, change `from modality_simulator.config import Config` to `Station`, and `cfg: Config` to `cfg: Station`. Keep the variable name `cfg`, so the diffs stay type-only. In `main.py`:
- `main()` calls `config.load()`, catching `ConfigError` as today.
- It logs `config.overlaps(cfg.stations)` as warnings.
- `build(cfg: Config, recent)` uses `station = cfg.stations[0]` where it used `cfg`, and `cfg.data_dir` / `cfg.poll_seconds` from the `Config`. That's temporary; Task 2 replaces it.

Update `tests/factories.py`, every `config(...)` call in `tests/unit/` (most become `station(...)`), and `tests/integration/conftest.py` (`Station(name="Test", calling_ae="SIM_TEST", gateway_ae="TEST_GW", gateway_host="localhost", gateway_port=14242)`). `test_main.py::test_missing_gateway_ae_exits_2_saying_why` becomes `test_a_missing_stations_file_exits_2_saying_why`: monkeypatch `config.CONFIG_PATH` (or `load`'s default) to a path under `tmp_path`, and assert exit 2 with "No stations file" on stderr.

- [ ] **Step 5: Run the whole unit suite**

Run: `pip install -e ".[test]" && pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add -A src tests pyproject.toml
git commit -m "Read the stations from a stations file"
```

---

### Task 2: One worklist, log, lock and tab per station

**Files:**
- Modify: `src/modality_simulator/main.py`
- Modify: `src/modality_simulator/web.py`
- Modify: `src/modality_simulator/auto.py`: add a `name` for log lines
- Test: `tests/unit/test_web.py`, `tests/unit/test_main.py`, `tests/unit/test_results_and_auto.py`

**Interfaces:**
- Consumes: `Station`, `Config`, and `factories.station` / `factories.config` from Task 1
- Produces, in `web.py`: `@dataclass(frozen=True) class StationConsole` with fields `station: Station`, `query: Callable[[], list[WorklistEntry]]`, `do_acquire: Callable[[WorklistEntry], AcquisitionResult]` and `log: AcquisitionLog`; and `create_app(consoles: Sequence[StationConsole], log_lines: Callable[[], list[str]] = list) -> Flask`
- Produces, in `auto.py`: `AutoAcquirer(query, do_acquire, log, name: str = "")`. Its log lines start with `name` (the calling AE) when given.
- Produces, in `main.py`: `build(cfg: Config, recent: RecentLogs | None = None) -> tuple[Flask, list[AutoAcquirer]]`, with one auto-acquirer per station that has `auto_acquire`. `main()` starts a thread for each, named `auto-acquire-<calling_ae>`.

- [ ] **Step 1: Write the failing web tests**

Rework `Console` in `test_web.py` to take several stations: a dict from calling AE to entries, and an optional per-station gateway error. It builds one `StationConsole` each, with its own `AcquisitionLog(tmp_path / ae / "acquisitions.jsonl")`. The existing single-station tests keep their assertions, but request `/station/SIM_CR` instead of `/`, and post to `/station/SIM_CR/acquire`. New tests:

```python
def test_root_redirects_to_the_first_station(tmp_path):
    r = two_stations(tmp_path).client.get("/")
    assert r.status_code == 303 and r.headers["Location"].endswith("/station/SIM_CR")

def test_each_station_has_a_tab_and_shows_only_its_own_worklist(tmp_path):
    html = two_stations(tmp_path).client.get("/station/SIM_US").get_data(as_text=True)
    assert 'href="/station/SIM_CR"' in html and 'href="/station/SIM_US"' in html
    assert 'aria-current="page"' in html          # on the Ultrasound tab only
    assert "ACC-US" in html and "ACC-CR" not in html
    assert "SIM_US" in html                        # the header names this station's calling AE

def test_acquire_uses_that_stations_acquirer_and_returns_to_its_tab(tmp_path):
    c = two_stations(tmp_path)
    r = c.client.post("/station/SIM_US/acquire", data={"accession": "ACC-US"})
    assert r.status_code == 303 and r.headers["Location"].endswith("/station/SIM_US")
    assert c.acquired == {"SIM_US": ["ACC-US"]}

def test_an_accession_from_another_station_is_not_acquired(tmp_path):          # Review Focus 4
    c = two_stations(tmp_path)
    r = c.client.post("/station/SIM_CR/acquire", data={"accession": "ACC-US"})
    assert r.status_code == 404 and c.acquired == {}

def test_a_gateway_error_shows_only_on_that_stations_tab(tmp_path):           # Review Focus 3
    c = two_stations(tmp_path, errors={"SIM_CR": "gateway down"})
    assert "gateway down" in c.client.get("/station/SIM_CR").get_data(as_text=True)
    us = c.client.get("/station/SIM_US").get_data(as_text=True)
    assert "gateway down" not in us and "ACC-US" in us

def test_recent_acquisitions_are_per_station(tmp_path):   # log a result in SIM_CR's log; it doesn't appear on SIM_US's tab

def test_an_unknown_station_is_a_404_with_the_tabs(tmp_path):                # Review Focus 5
    c = two_stations(tmp_path)
    for r in (c.client.get("/station/NOPE"), c.client.post("/station/NOPE/acquire", data={"accession": "ACC-CR"})):
        assert r.status_code == 404 and 'href="/station/SIM_CR"' in r.get_data(as_text=True)

def test_the_recent_log_is_on_every_tab(tmp_path):   # log_lines ["line one"] appear on both tabs
```

- [ ] **Step 2: Write the failing main and auto tests**

In `test_main.py`:
- `test_build_makes_an_auto_acquirer_only_for_stations_with_it_on`: two stations, one with `auto_acquire=True`, gives `len(autos) == 1`.
- `test_each_station_queries_and_acquires_as_itself`: monkeypatch `mwl.query_worklist` and `acquire.acquire` to record `cfg.calling_ae`. Drive `/station/SIM_US` and an acquire through the Flask test client, and assert that only `SIM_US` was used.
- `test_each_station_writes_its_own_log`: after an acquire on SIM_US, `tmp_path / "SIM_US" / "acquisitions.jsonl"` exists and `tmp_path / "SIM_CR"` doesn't.
- `test_acquisition_log_lines_name_the_station`: caplog contains `SIM_US` on the "Stored … image(s)" line.

The existing `test_main.py` tests keep their assertions on a one-station `config(station(...), data_dir=tmp_path)`, using the new URLs.

In `test_results_and_auto.py`, add `test_auto_acquire_log_lines_name_the_station`: an `AutoAcquirer(..., name="SIM_US")` whose acquire raises; caplog's exception line starts with `SIM_US`.

- [ ] **Step 3: Run them to make sure they fail**

Run: `pytest tests/unit -q`
Expected: the new tests FAIL (with a TypeError from the `create_app` signature, or a 404 on `/station/...`)

- [ ] **Step 4: Implement**

- **`main.build()`:** move today's body into a per-station helper. It builds the station's `AcquisitionLog(cfg.data_dir / station.calling_ae / "acquisitions.jsonl")`, its own `threading.Lock()`, its `query` and `do_acquire` closures over `station`, and a `StationConsole`, plus an `AutoAcquirer(..., name=station.calling_ae)` if `station.auto_acquire`. Prefix its log messages with the calling AE: `"%s: Stored %d image(s) for accession %s …"`.
- **`main()`:** logs one startup line per station (calling AE, modalities, auto-acquire) and the shared gateway once.
- **`web.py`:** the page gets a `<nav>` of tabs, one link per station labelled `station.name`, with `aria-current="page"` on the current one. Keep the styling as plain as the current page. The header and worklist use `console.station`. `<title>` becomes `<station name> — Modality simulator`. A 404 for an unknown station renders a short page with the tabs.

- [ ] **Step 5: Run the unit suite**

Run: `pytest tests/unit -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add -A src tests
git commit -m "Give each station its own worklist, log, lock and console tab"
```

---

### Task 3: Integration test, smoke test and README

**Files:**
- Modify: `tests/integration/test_gateway.py`
- Modify: `scripts/smoke-test-image.sh`
- Modify: `README.md`
- Modify: `pyproject.toml`: `description` stays as it is

**Interfaces:**
- Consumes: the `gateway` fixture (a `Station`, from Task 1) and `config.load`

- [ ] **Step 1: Add the two-station integration test**

```python
def test_two_stations_each_see_only_their_own_modality(gateway):
    cr = replace(gateway, name="X-ray", calling_ae="SIM_CR", modalities=("CR",))
    us = replace(gateway, name="Ultrasound", calling_ae="SIM_US", modalities=("US",))
    assert {e.accession_number for e in query_worklist(cr)} == {"ACC-IT-CR1", "ACC-IT-LIB1", ""}
    assert [e.accession_number for e in query_worklist(us)] == ["ACC-IT-US1"]
```

Add `test_images_carry_the_stations_calling_ae`, next to the existing acquire test. Acquire `ACC-IT-US1` as `us`, then use the Orthanc REST API at `REST` (as the existing acquire tests do) to read the stored instance's `StationName`, and assert it's `"SIM_US"`.

- [ ] **Step 2: Run the integration tests**

Run: `pytest tests/integration -v` (needs Docker)
Expected: all pass. If Orthanc rejects a calling AE that isn't `SIM_TEST`, allow unknown calling AEs in `tests/integration/docker-compose.yml`'s Orthanc config rather than changing the test.

- [ ] **Step 3: Update the smoke test**

- With no file mounted, the image exits 2, and the output contains `No stations file at /config/stations.yaml`.
- Then write a one-station stations file (`gateway: {ae: SMOKE_GW}`, one station `SMOKE_CR`/`[CR]`) into a `mktemp -d` directory and run the container with `-v "$dir":/config:ro`.
- `/health` answers, and `/` redirects to `/station/SMOKE_CR`: check with `curl -s -o /dev/null -w '%{redirect_url}'`.

Run: `scripts/smoke-test-image.sh`
Expected: `==> OK`

- [ ] **Step 4: Update `README.md`**

- Replace "Running it with distro-tools" and the "Settings" table with "The stations file". Use the spec's kol-ci example, the station field table, and where the file lives: `/config/stations.yaml` in the container, and `modality-simulator-config/stations.yaml` in a distro-tools instance (puppet writes it on kol-ci; otherwise you create it before `add-service`).
- "Setting up AdvaPACS": each station's `calling_ae` is its own Remote AE.
- "Auto-acquire" is per station, with shared `poll_seconds`.
- The acquisition log is `/data/<calling_ae>/acquisitions.jsonl`.
- The recent log names each line's station.
- Note the overlap warning.

- [ ] **Step 5: Commit**

```bash
git add -A tests scripts README.md
git commit -m "Test two stations against the gateway; document the stations file"
```

---

### Task 4: distro-tools: mount the stations directory

Repo: `~/development/openmrs/contrib/distro-tools`, on a new branch `modality-simulator-stations` off `main`.

**Files:**
- Modify: `docker/services/modality-simulator.yaml`
- Modify: `docker/services/modality-simulator.env.defaults`
- Modify: `docs/services.md` (the "Modality simulator" section)

**Interfaces:**
- Produces: the compose variable `MODALITY_SIMULATOR_CONFIG_DIR`, default `./modality-simulator-config`, mounted at `/config:ro`. Task 5 writes it.

- [ ] **Step 1: Edit the fragment and the defaults**

- In the fragment, remove the `environment:` entries `MODALITY_SIMULATOR_GATEWAY_HOST`, `_GATEWAY_PORT`, `_GATEWAY_AE`, `_CALLING_AE`, `_MODALITIES`, `_STATION_AE_FILTER`, `_INSTITUTION`, `_AUTO_ACQUIRE` and `_POLL_SECONDS`. Keep `TZ`.
- Add the volume `- ${MODALITY_SIMULATOR_CONFIG_DIR?}:/config:ro`, with a comment that it holds `stations.yaml` (see the simulator's README).
- Update the header comment: "A fake CR/US/CT modality" becomes "Fake CR/US/CT modalities, one console tab per station".
- In `.env.defaults`, drop the same variables and add `MODALITY_SIMULATOR_CONFIG_DIR="${MODALITY_SIMULATOR_CONFIG_DIR:-./modality-simulator-config}"`. Update its comment: settings are in `<config dir>/stations.yaml`, and the calling AEs must be Remote AEs.

- [ ] **Step 2: Update `docs/services.md`**

Replace the settings table and the `export MODALITY_SIMULATOR_GATEWAY_AE=...` example with:
- creating `<instance>/modality-simulator-config/stations.yaml`, with a two-station example;
- then `add-service` and `start`;
- a link to the simulator's README for the fields;
- the note that it's a directory because every `*.yaml` in the instance dir is a compose fragment.

- [ ] **Step 3: Verify**

Run: `bash -n` on any touched scripts (there should be none). Check that nothing else still refers to the removed variables: `grep -rn "MODALITY_SIMULATOR_\(GATEWAY\|CALLING\|MODALITIES\|STATION_AE\|INSTITUTION\|AUTO_ACQUIRE\|POLL\)" .` should print nothing. Then run the repo's own tests if they cover fragments; see its README or CI workflow.

- [ ] **Step 4: Commit**

```bash
git add docker/services/modality-simulator.yaml docker/services/modality-simulator.env.defaults docs/services.md
git commit -m "modality-simulator: mount a stations file instead of env settings"
```

---

### Task 5: Puppet: write the stations file and kol-ci's two stations

Repo: `~/development/openmrs/puppet` (default branch `master`), on a new branch `modality-simulator-stations`.

**Files:**
- Modify: `puppet/mirebalais-modules/openmrs_docker/manifests/service/modality_simulator.pp`
- Create: `puppet/mirebalais-modules/openmrs_docker/templates/modality-simulator-stations.yaml.erb`
- Modify: `puppet/manifests/site.pp` (the kol-ci declaration, around line 813)

**Interfaces:**
- Consumes: `MODALITY_SIMULATOR_CONFIG_DIR` (Task 4) and the stations file format (Task 1)

- [ ] **Step 1: Rework the define**

- **Parameters:**
  - kept: `$instance_name`, `$image_name`, `$image_tag`, `$host_port`, `$image_dir`
  - shared settings: `$gateway_ae` (required), `$gateway_host`, `$gateway_port`, `$institution`, `$poll_seconds`
  - `$stations` (required, a non-empty array of hashes)
  - removed: `$calling_ae`, `$modalities`, `$station_ae_filter`, `$auto_acquire`
- **The directory and the file:** `file { "${home_dir}/modality-simulator-config": ensure => directory }`, and `file { "${home_dir}/modality-simulator-config/stations.yaml": ensure => file, content => template('openmrs_docker/modality-simulator-stations.yaml.erb') }`. Both have the owner, group, mode (`0750`/`0640`) and `require` of the existing fragment `file`, plus `before => Exec["${instance_name} compose up"]`.
- **The template:** builds the same hash as the spec's example (`gateway: {ae, host, port}`, `institution`, `poll_seconds`, `stations`) and emits it with Ruby's `require 'yaml'` / `.to_yaml`. It writes each station's hash as given, so the simulator's unknown-key check catches typos in `site.pp`.
- **Restart on change:** `exec { "${instance_name} restart modality-simulator": command => "docker restart ${instance_name}-modality-simulator", refreshonly => true, subscribe => File[<stations.yaml>], require => Exec["${instance_name} compose up"], onlyif => "docker inspect ${instance_name}-modality-simulator", user => $instance_name, path => ['/usr/bin', '/bin'] }`. Copy the `environment` from `compose up` if `docker` needs `HOME` there.
- **The env fragment:** drop the removed variables and add `'MODALITY_SIMULATOR_CONFIG_DIR' => './modality-simulator-config'`.
- Update the define's header comment to match.

- [ ] **Step 2: kol-ci**

Replace the declaration in `site.pp` with the spec's example. Keep the `# somprinfappclstr-01's Tailscale IP` comment, and drop `calling_ae => 'KOL-CI_SIM'`.

- [ ] **Step 3: Verify**

Run `puppet parser validate` on the two `.pp` files, and `erb -P -x -T - <template> | ruby -c` on the template. Then render the template with a scratch Ruby script, binding `@gateway_ae` etc. to kol-ci's values, and check that the simulator loads the output: `python -c "from modality_simulator.config import load; from pathlib import Path; print(load(Path('<rendered>')))"`. Run the repo's own spec or lint tasks if the `Rakefile` has them.

- [ ] **Step 4: Commit**

```bash
git add puppet/mirebalais-modules/openmrs_docker puppet/manifests/site.pp
git commit -m "kol-ci: modality simulator with X-ray and Ultrasound stations"
```

---

## Rollout (not a task; the user does this)

1. In AdvaPACS, add the `KOL-CI_CR` and `KOL-CI_US` Remote AEs, allowed to query the worklist.
2. Open PRs for all three branches. Merge them together, since merging the simulator publishes `latest` and kol-ci runs `latest`.
3. Let puppet run on kol-ci, and check that both tabs at `http://100.90.25.20:8095` show their worklists.
4. Remove `KOL-CI_SIM` from AdvaPACS.
