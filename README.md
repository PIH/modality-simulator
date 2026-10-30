# Modality simulator

Fake CR, US and CT imaging rooms for testing the OpenMRS → AdvaPACS order flow without real
equipment. Each room is a **station** with its own AE title and its own tab in the console. A
station reads an AdvaPACS gateway's DICOM Modality Worklist and, when you click **Acquire** on its
tab, sends images for that worklist entry back to the gateway, stamped with the entry's patient,
accession number and study, as a real modality would.

Published as [`partnersinhealth/modality-simulator`](https://hub.docker.com/r/partnersinhealth/modality-simulator).

## The stations file

The simulator reads its settings from `/config/stations.yaml`, and won't start without it. The
gateway's settings are shared by every station:

```yaml
gateway:
  ae: PIH_KOL-CI_GW        # the gateway's AE title: its Local AE in AdvaPACS
  host: host.docker.internal   # default
  port: 11112                  # default; the gateway's DICOM port (its Local AE's port)
institution: OpenMRS Modality Simulator   # default; written into every image
poll_seconds: 30                          # default; how often auto-acquire reads the worklist
stations:
  - name: X-ray
    calling_ae: KOL-CI_CR
    modalities: [CR]
  - name: Ultrasound
    calling_ae: KOL-CI_US
    modalities: [US]
```

| Station field | Default | |
|---|---|---|
| `name` | must be set | the tab's label |
| `calling_ae` | must be set | the station's AE title: 1-16 letters, digits, `.`, `_` or `-`, unique |
| `modalities` | must be set | which worklist entries it shows: any of `CR`, `US`, `CT` |
| `station_ae_filter` | empty | only entries scheduled for this station AE |
| `auto_acquire` | `false` | acquire every new entry without clicking |

A setting it doesn't know is an error, so a typo stops the simulator at startup with a message
naming it. If two stations would see the same entries (they share a modality, and have the same
station AE filter or none), it logs a warning: with auto-acquire on, both would send them.

With distro-tools, the file is `modality-simulator-config/stations.yaml` in the instance
directory; on servers managed by puppet, puppet writes it. Otherwise, create it before adding the
service:

```bash
openmrs-docker <name> add-service modality-simulator
openmrs-docker <name> start
```

The console is at `http://<host>:8095`. See distro-tools' `docs/services.md` for the fragment.

The container serves its console on port 8080, keeps each station's acquisition log in
`/data/<calling_ae>/`, and reads its image library from `/images`.

## Setting up AdvaPACS

- Each station must be a **Remote AE** in AdvaPACS (Configuration > Remote AEs), with an AE title
  exactly matching its `calling_ae`, case included, and allowed to query the worklist. If it
  isn't, that station's tab says the gateway rejected the association.
- `gateway.ae` and `gateway.port` are the gateway's Local AE title and port (Configuration > Local
  AEs).

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

For a station with `auto_acquire: true`, every `poll_seconds` it acquires each of its worklist
entries that has an accession number and hasn't been stored yet. An entry that fails is tried at
most 3 times. The station's acquisition log in `/data/<calling_ae>/` records what's been done, so a
restart doesn't re-send.

## Errors

- An entry without an accession number can't be acquired: AdvaPACS would put its images in the
  Validation Queue.
- If the gateway can't be reached, rejects the association, or fails the worklist query, the
  station's tab shows why and tries again on the next page load or poll.
- An acquisition succeeds only if every image is stored; otherwise the console shows each
  failing status.
- `/health` reports only that the simulator is running, not whether the gateway is reachable.

## Logs

The console's **Recent log** box, the same on every tab, shows the last 500 log records, the same
ones written to the container's output (`docker logs`). Each line about a query or an acquisition
names its station's calling AE. Each worklist query is logged with the exact C-FIND it sent and
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

1. Set up each station as a Remote AE in AdvaPACS (see above), and start the simulator with a
   stations file naming the gateway's Local AE title and port.
2. Place a radiology order in OpenMRS for a test patient, with a CR procedure, and another with a
   US procedure.
3. Open the console: each order appears on its modality's tab, with its accession number, and not
   on the other tab.
4. Click **Acquire**: the tab's recent acquisitions table shows `stored N image(s)`.
5. In AdvaPACS, the study appears under that patient and accession number, and is **not** in the
   Validation Queue.
6. Repeat with auto-acquire on: a new order is stored within one poll, and only once.
