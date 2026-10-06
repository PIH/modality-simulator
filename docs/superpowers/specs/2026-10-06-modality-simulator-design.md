# Modality Simulator — Design

Date: 2026-10-06
Status: Approved in conversation; awaiting written-spec review

## Purpose

A reusable fake imaging room (CR, US, CT) that behaves like a real modality toward an
AdvaPACS on-premises gateway: it reads the gateway's DICOM Modality Worklist and sends images
for a chosen worklist entry. It lets anyone running an OpenMRS + AdvaPACS instance exercise the
whole order → worklist → acquisition → study flow without real equipment.

It's attached to an instance with:

```
openmrs-docker <name> add-service modality-simulator
```

Success means: after an order placed in OpenMRS reaches the gateway's worklist, someone can
click Acquire in the simulator's console, and a study with the right patient, accession number
and modality arrives in AdvaPACS (not in its Validation Queue).

## Background

The code that does this today lives in imladris `sidecar/` (Python, about 3,400 lines:
`order_poller`, `fhir_mwl_poller`, `acquisition_loop`, `scp_relay`, `modality_console_web`,
`hl7_bridge`). It's bound to imladris: it writes `.wl` files, takes webhooks, relays to Qure,
and its configuration is read from the environment at module import. Its images come from
`/hospital-records`, which may contain real patient data and must never go into a published
image.

This project replaces it for the pure-modality case. The sidecar is a reference onl.. The
code here is written fresh, test-first.

## Scope

In scope:

- DICOM MWL C-FIND against the gateway's native worklist (see imladris
  `docs/issues/advapacs-dicom-mwl-native.md`). The gateway's AE title, host and port are always
  configuration, never hardcoded; the initial PIH setup uses AE `PIH_KOL-CI_GW`.
- C-STORE of images for a selected worklist entry back to the gateway.
- Generated images by default; optional mounted library of real DICOM files.
- A small web console with an Acquire button per entry; optional auto-acquire.
- A distro-tools service fragment.

Out of scope: Qure, SCP relaying, OpenMRS/FHIR order polling, HL7, writing `.wl` files, an
order-status overlay, MPPS, storage commitment.

## Repositories

| Repo | Contents |
|------|----------|
| `PIH/modality-simulator` (this one) | The simulator's code, tests, Dockerfile, CI; publishes `partnersinhealth/modality-simulator` to Docker Hub |
| `distro-tools` | Only `docker/services/modality-simulator.yaml` and `modality-simulator.env.defaults`, plus compose test coverage |

This is the same split as `openhim-advapacs-mediator`.

## Architecture

A single Python process (pynetdicom + pydicom + Flask), one container. It talks to the gateway
only over DICOM; it has no other dependencies.

```
             browser                         advapacs-gateway (network_mode: host)
                │ HTTP :8080                         ▲  MWL C-FIND / C-STORE
                ▼                                    │  host.docker.internal:11112
   ┌──────────────── modality-simulator ─────────────┴───┐
   │ web ──► acquire ──► images (synthetic | library)    │
   │  │         │                                        │
   │  └──► mwl ◄┘     auto (optional poll loop)          │
   │                                                     │
   │ config (read once in main)          /data (log)     │
   └─────────────────────────────────────────────────────┘
```

### Components

Each is a module with one job and a small interface. None reads the environment except `config`.

- **`config`** — Reads the `MODALITY_SIMULATOR_*` environment variables (listed under the
  fragment below) into a frozen dataclass. Validates them and raises with a clear message on anything missing or
  invalid (AE titles must be 1–16 characters with no backslash or control characters).
  Called once from `main`.
- **`mwl`** — `query_worklist(cfg) -> list[WorklistEntry]`. Opens an association to the gateway
  (passing the called AE), sends an MWL C-FIND filtered by the configured modalities and,
  optionally, scheduled station AE. Parses each match into a `WorklistEntry` (patient ID, name,
  birth date, sex, accession number, requested procedure, modality, study instance UID if the
  gateway supplies one, scheduled station AE). Any failure status is raised as an error, never
  returned as an empty list.
- **`images`** — `images_for(entry, cfg) -> list[Dataset]`. If the library directory has a
  readable DICOM file of the entry's modality, use it (decompressing it if needed, so it can
  be sent uncompressed). Otherwise generate one: a correct SOP class per modality (CR: Computed
  Radiography Image Storage; US: Ultrasound Image Storage; CT: CT Image Storage, a short series)
  with patient, accession and procedure text burned into the pixels.
- **`acquire`** — `acquire(entry, cfg) -> AcquisitionResult`. Gets images, stamps them from the
  worklist entry (patient, accession, study UID — from the entry or newly generated — new series
  and SOP instance UIDs, dates, modality, institution, station AE), and C-STOREs each to the
  gateway on one association. Records the status of each instance. Succeeds only if every
  instance is stored. The caller appends the result to the log (see `results`).
- **`auto`** — Optional background loop: every `POLL_SECONDS`, query the worklist and acquire
  each entry not already acquired. A failed accession is retried for up to 3 polls, then marked
  failed. Which accessions are done or have failed is worked out from the acquisition log under
  `/data`, so restarts don't re-send and there's no second state file to keep in step.
- **`dicom_net`** — `associate(cfg, abstract_syntaxes)`: the one place that opens an association
  to the gateway (calling AE, called AE, timeouts) and turns a failed one into a `GatewayError`
  that says whether the connection was refused, rejected or aborted, and what to check.
- **`results`** — `AcquisitionLog`: appends each `AcquisitionResult` to
  `/data/acquisitions.jsonl` and reads back recent results and per-accession history.
- **`web`** — Flask app: a page listing worklist entries with an Acquire button each, recent
  acquisition results, and an error banner when the gateway can't be reached. `POST` endpoint
  to acquire one entry. `GET /health`.
- **`main`** — Loads config, starts `auto` if enabled, serves `web` on port 8080.

## distro-tools fragment

`docker/services/modality-simulator.yaml`:

- Service `modality-simulator`, `container_name: ${SERVICE_NAME}-modality-simulator`, image
  `${MODALITY_SIMULATOR_IMAGE_NAME?}:${MODALITY_SIMULATOR_IMAGE_TAG?}`, `restart: unless-stopped`.
- On the instance's bridge network, with
  `extra_hosts: ["host.docker.internal:host-gateway"]`, because the gateway uses
  `network_mode: host`.
- Ports: `${MODALITY_SIMULATOR_HOST_PORT?}:8080`.
- No `depends_on` on `advapacs-gateway`. A hard dependency would rule out using an external
  gateway; the gateway has no healthcheck; and it only listens on 11112 after AdvaPACS cloud has
  pushed its configuration. The simulator copes with an unreachable gateway instead (see Errors).
- Volumes: `modality-simulator-data:/data` and
  `${MODALITY_SIMULATOR_IMAGE_DIR?}:/images:ro`, with the default `./modality-simulator-images`
  in `.env.defaults` (distro-tools keeps every default there; `compose.bats` checks it). The
  relative default resolves to the instance directory, which is the compose project directory (same pattern as
  `SMOKE_TESTS_OUTPUT_DIR` in `openmrs-smoke-tests.yaml`). With no DICOM in it, images are
  generated.

`docker/services/modality-simulator.env.defaults`:

| Variable | Default |
|----------|---------|
| `MODALITY_SIMULATOR_IMAGE_NAME` | `partnersinhealth/modality-simulator` |
| `MODALITY_SIMULATOR_IMAGE_TAG` | `latest` |
| `MODALITY_SIMULATOR_HOST_PORT` | `8095` |
| `MODALITY_SIMULATOR_GATEWAY_HOST` | `host.docker.internal` |
| `MODALITY_SIMULATOR_GATEWAY_PORT` | `11112` |
| `MODALITY_SIMULATOR_GATEWAY_AE` | required, no default (`:?must be set: …`); e.g. `PIH_KOL-CI_GW` |
| `MODALITY_SIMULATOR_CALLING_AE` | `SIM_MODALITY` |
| `MODALITY_SIMULATOR_MODALITIES` | `CR,US,CT` |
| `MODALITY_SIMULATOR_STATION_AE_FILTER` | empty (no filter) |
| `MODALITY_SIMULATOR_INSTITUTION` | `OpenMRS Modality Simulator` |
| `MODALITY_SIMULATOR_AUTO_ACQUIRE` | `false` |
| `MODALITY_SIMULATOR_POLL_SECONDS` | `30` |
| `MODALITY_SIMULATOR_IMAGE_DIR` | `./modality-simulator-images` (in the instance directory) |

The fragment passes the settings the simulator reads (`GATEWAY_*`, `CALLING_AE`, `MODALITIES`,
`STATION_AE_FILTER`, `INSTITUTION`, `AUTO_ACQUIRE`, `POLL_SECONDS`) to the container under the same
names, so the image reads `MODALITY_SIMULATOR_*` directly. `IMAGE_NAME`, `IMAGE_TAG`, `HOST_PORT`
and `IMAGE_DIR` are used only by Compose.

The README and distro-tools `docs/services.md` note that the calling AE (`SIM_MODALITY`) must be
set up as a Remote AE in AdvaPACS (Configuration > Remote AEs, AE title matching exactly) before
the gateway accepts it.

## Error handling

- **Configuration:** invalid or missing settings stop the process at startup with a message
  naming the setting (for example, `MODALITY_SIMULATOR_GATEWAY_AE`).
- **Gateway unreachable or association rejected:** the console shows a banner saying whether the
  connection was refused, the association rejected or aborted, and what to check; the worklist is retried on each page load or poll.
- **C-FIND failure status:** shown as an error, never as an empty worklist.
- **Entries without an AccessionNumber:** listed but can't be acquired (button disabled, reason
  shown). Sending them would put the study in AdvaPACS's Validation Queue.
- **C-STORE:** each instance's status is logged under `/data`; the acquisition succeeds only if
  all instances are stored. A partial failure is reported with the failing statuses.
- **Auto mode:** a failed accession is retried for up to 3 polls, then marked failed and left
  alone.
- **Unreadable or unusable library file:** logged and skipped; falls back to a generated image.
- **`/health`:** reports only that the process is up, not the gateway's state.

## Testing

- **Unit tests (pytest, written first):** config parsing and validation; synthetic image
  generation per modality (SOP class, required tags, pixel data); tag stamping; library picker,
  including compressed files and fallback; parsing of MWL responses.
- **Integration tests:** a stock Orthanc with its worklist plugin, loaded with `.wl` fixtures,
  stands in for the gateway. Run with docker compose, locally and in GitHub Actions. Covers the
  C-FIND, a full acquire (studies appear in Orthanc with the right tags), and the
  unreachable-gateway and failure-status paths.
- **Console:** Flask test client — listing, acquire, disabled entries, error banner.
- **distro-tools:** add the fragment to `test/static/compose.bats`.
- **Manual end-to-end:** a README checklist for a real AdvaPACS gateway (order in OpenMRS →
  worklist entry → Acquire → study in AdvaPACS with the right accession, not in the Validation
  Queue).

Test fixtures are synthetic only; no real patient data anywhere in the repo or image.

## Publishing

- GitHub Actions runs unit and integration tests on every PR.
- Pushes to `main` publish `partnersinhealth/modality-simulator:latest`; `vX.Y.Z` tags publish
  that version.
- Multi-arch images: `linux/amd64` and `linux/arm64`.
- Same conventions as `openhim-advapacs-mediator`: Docker Hub login `pihci` with the
  `DOCKERHUB_PASSWORD` org secret, ci-dashboard notifications, and the distro-tools image scan.

## Fixes over the imladris sidecar

- Pass the called AE when associating.
- Check C-FIND failure statuses instead of treating them as no results.
- Handle compressed library files.
- No configuration read at module import.
- A small console instead of the 1,148-line one tied to `.wl` files, webhooks and Qure.

## Rejected alternatives

- **Lifting the sidecar code:** too entangled with imladris-specific paths and config.
- **Orthanc as the modality:** still needs Python to drive it; Orthanc is kept as the CI
  stand-in gateway instead.
- **dcm4che command-line tools:** a JVM in the image and shell glue for what pynetdicom does
  directly.
