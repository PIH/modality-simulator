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

## Logs

The console's **Recent log** box shows the last 500 log records, the same ones written to the
container's output (`docker logs`). Each worklist query is logged with the exact C-FIND it sent and
every raw response from the gateway, including entries the simulator then filters out by modality
or station, so you can see what the gateway itself returns (e.g. each step's Scheduled Procedure
Step Status). The responses include patient details, so treat the logs like the worklist itself.

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
