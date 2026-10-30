# Multiple stations — Design

## Why

The demo is the OpenMRS → AdvaPACS order flow across more than one kind of imaging room. Someone
places a CR (x-ray) or US (ultrasound) order in OpenMRS. The order shows up in the worklist of the
matching simulated modality, and the presenter clicks **Acquire** on that modality to simulate
taking the images, as the console does today.

The simulator currently runs one modality, configured by `MODALITY_SIMULATOR_*` env vars. This
change lets one simulator container run several **stations**, each a simulated modality with its
own AE title, and gives each station its own tab in the console. The next step after this is
stations at different **locations**, which this design leaves room for but doesn't build.

## Where the stations live

Each station runs in this simulator. The alternatives were rejected:

- **More distro-tools services.** distro-tools is a key part of the production environment, and
  this simulator is throwaway test code.
- **More containers declared by puppet.** Each container would need its own port, console and
  config, and that multiplies once there are locations.

## Goals

- One container runs any number of stations. Each station has its own calling AE, its own worklist
  query, its own acquisitions and its own console tab.
- The configuration is a small stations file. On kol-ci, puppet writes it into the instance
  directory, in `modality-simulator-config/` next to `modality-simulator-images/`, so adding a
  station is a change to `site.pp` only.
- kol-ci runs two stations: `KOL-CI_CR` (CR) and `KOL-CI_US` (US).

## Non-goals

- **Locations and routing orders to a room.** Orders will get a location, but the user hasn't
  worked that out yet. This is a to-do. For now a station sees the entries of its modalities,
  optionally narrowed by Scheduled Station AE, exactly as today.
- **More than one gateway.** Production will have several, but the demo uses one gateway shared by
  every station. Don't build a per-station gateway; just don't make one hard to add.
- **Backwards compatibility.** The `MODALITY_SIMULATOR_*` simulator settings are removed, not kept
  as a fallback. The old `/data/acquisitions.jsonl` is ignored.

## Anti-goals

- A CR order showing up on the US tab, or the reverse.
- Images sent with the wrong station's AE title, so AdvaPACS shows the wrong "machine".
- One station's gateway error or slow acquisition blanking or blocking the other tabs.

## The stations file

YAML. Shared settings at the top, then the stations. For example (kol-ci's):

```yaml
gateway:
  ae: PIH_KOL-CI_GW
  host: host.docker.internal   # default
  port: 11112                  # default
institution: OpenMRS Modality Simulator   # default
poll_seconds: 30                          # default; auto-acquire's interval
stations:
  - name: X-ray
    calling_ae: KOL-CI_CR
    modalities: [CR]
  - name: Ultrasound
    calling_ae: KOL-CI_US
    modalities: [US]
```

A station's fields are:

| Field | Default | |
|---|---|---|
| `name` | required | the tab's label |
| `calling_ae` | required | its AE title; must be a Remote AE in AdvaPACS |
| `modalities` | required | which of CR, US, CT it shows; at least one |
| `station_ae_filter` | empty | only entries scheduled for this station AE |
| `auto_acquire` | `false` | acquire every new entry of this station without clicking |

`gateway.ae` and at least one station are required. Calling AEs must be unique, since a station is
identified by its calling AE. Every AE title is validated as today (1-16 characters, no backslash
or control characters). Like today, a bad file stops the simulator at startup with a message
naming the field, and an unknown key is an error, so typos don't pass silently.

The container reads it from a fixed path, `/config/stations.yaml`. A missing file is a clear
startup error that names that path.

It must not be a `*.yaml` file directly in the instance directory: distro-tools treats every
`*.yaml` there as a compose fragment (`compose.sh`, `env.sh`, `instance.sh`, and puppet's
`compose up` check), so a stations file there would break every `openmrs-docker` command for the
instance. It lives in its own directory, `modality-simulator-config/stations.yaml`, and the
directory is mounted. That also means a file replaced by puppet is seen by the container, which a
single-file bind mount would not, and if the directory is missing Docker creates it empty, so the
simulator gives its clear "missing file" error. *(My call.)*

**Stations sharing a modality** are allowed, since locations will need them, but the simulator
logs a startup warning when two stations would see the same entries: they share a modality and
neither has a station AE filter, or they have the same one. Each station's auto-acquire checks
only its own log, so two such stations with auto-acquire on would both send the same accession.
*(My call; revisit with locations.)*

`Config` becomes the shared settings plus a list of `Station`s. Code that takes a `Config` today
(`mwl`, `acquire`, `dicom_net`) takes what it needs for one station: the shared gateway, the
institution and the image directory, plus that station's calling AE, modalities and filter.

## How it behaves

- **Worklist:** each station runs its own C-FIND, as its own calling AE, and filters the results by
  its own modalities and station AE filter. The gateway may return the same entries to every
  calling AE; the per-station filter is what keeps CR and US apart.
- **Acquire:** images are stamped and sent with the station's calling AE, which goes into
  `StationName` and `SourceApplicationEntityTitle` as today.
- **Acquisitions:** each station has its own log, `/data/<calling_ae>/acquisitions.jsonl`, with the
  same format as today. Status, history and auto-acquire's "already stored" check read that
  station's log.
- **Locking:** one lock per station, so stations acquire independently. Within a station, the
  console and auto-acquire still take turns, as today.
- **Auto-acquire:** one thread for each station that has it on, with the shared `poll_seconds`.
- **Logging:** one shared recent log. Each line names its station's calling AE.

## The console

- One tab per station, labelled with its name, at `/station/<calling_ae>`. `/` redirects to the
  first station.
- A station's tab shows what the page shows today, for that station only: its header (calling AE →
  gateway, modalities, auto-acquire), its worklist with Acquire buttons, and its recent
  acquisitions. Acquire posts to `/station/<calling_ae>/acquire` and comes back to that tab.
- The recent log box is shared and appears on every tab.
- A gateway error shows only on the tab of the station that hit it.
- `/health` is unchanged.

## distro-tools

`docker/services/modality-simulator.yaml` and `.env.defaults` drop the simulator settings
(`GATEWAY_*`, `CALLING_AE`, `MODALITIES`, `STATION_AE_FILTER`, `INSTITUTION`, `AUTO_ACQUIRE`,
`POLL_SECONDS`). They keep the compose-level ones (`IMAGE_NAME`, `IMAGE_TAG`, `HOST_PORT`,
`IMAGE_DIR`) and add `MODALITY_SIMULATOR_CONFIG_DIR` (default `./modality-simulator-config`),
mounted read-only at `/config`. Nothing in distro-tools writes the stations file: without puppet,
you create `modality-simulator-config/stations.yaml` yourself before starting the simulator, and
`docs/services.md` says so, replacing the settings table with the stations file and an example.
No distro-tools script changes.

## Puppet

In `openmrs_docker::service::modality_simulator`:

- The simulator-setting parameters are replaced by `$gateway_ae` (required), `$gateway_host`,
  `$gateway_port`, `$institution`, `$poll_seconds` and `$stations` (required, an array of hashes
  with the fields above).
- Puppet writes `/home/<instance>/modality-simulator-config/stations.yaml` from them (and the
  directory), owned by the instance's user like the fragment, before compose up.
- A change to the file restarts only the simulator: a `refreshonly` exec, subscribed to the file,
  runs `docker restart <instance>-modality-simulator`. `openmrs-docker update` wouldn't, because a
  mounted file's contents aren't compose config, and `openmrs-docker restart` would restart
  OpenMRS too. *(My call.)*
- The env fragment keeps the compose-level variables and adds `MODALITY_SIMULATOR_CONFIG_DIR`.

kol-ci in `site.pp`:

```puppet
openmrs_docker::service::modality_simulator { 'kol-ci':
  gateway_ae => 'PIH_KOL-CI_GW',
  # somprinfappclstr-01's Tailscale IP
  host_port  => '100.90.25.20:8095',
  stations   => [
    { name => 'X-ray',      calling_ae => 'KOL-CI_CR', modalities => ['CR'] },
    { name => 'Ultrasound', calling_ae => 'KOL-CI_US', modalities => ['US'] },
  ],
}
```

`KOL-CI_CR` replaces `KOL-CI_SIM`.

## AdvaPACS (the user does this by hand)

Add `KOL-CI_CR` and `KOL-CI_US` as Remote AEs, allowed to query the worklist, with the titles
matching exactly, case included. `KOL-CI_SIM` can then be removed.

## Rollout

Merging to main publishes the image as `latest`, and kol-ci runs `latest`. Once the new image is
out, kol-ci's simulator won't start until puppet has written the stations file and distro-tools
mounts it. So the three changes go out together: merge the simulator, distro-tools and puppet PRs,
then let puppet run on kol-ci.

## Testing

As today: unit tests for the stations file (valid file, each missing or invalid field, duplicate
calling AE, unknown key, missing file), per-station worklist filtering, per-station logs and
locking, and the console's tabs and routes. The integration test against the gateway runs with two
stations. Update the README to describe the stations file instead of the env vars.

## Left to the builder

- How the code is split between `Config`, `Station` and the per-station wiring in `main.build()`.
- The tabs' markup and styling, kept as plain as the current page.
- How puppet renders the YAML (`to_yaml` or a template).
- The YAML library (PyYAML is the obvious choice).
