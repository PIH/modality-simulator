"""Entry point: read the stations file, start each station's auto-acquire if it's on, serve the console."""

from __future__ import annotations

import logging
import sys
import threading
from datetime import datetime
from pathlib import Path

import waitress
from flask import Flask

from modality_simulator import acquire, config, mwl, web
from modality_simulator.auto import MAX_ATTEMPTS, AutoAcquirer
from modality_simulator.errors import ConfigError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.recent_logs import FORMAT, RecentLogs
from modality_simulator.results import AcquisitionLog

log = logging.getLogger("modality_simulator")

PORT = 8080


def build(cfg: config.Config, recent: RecentLogs | None = None) -> tuple[Flask, list[AutoAcquirer]]:
    consoles, autos = [], []
    for station in cfg.stations:
        console, auto = _station(station, cfg.data_dir)
        consoles.append(console)
        if auto is not None:
            autos.append(auto)
    log_lines = recent.lines if recent is not None else list
    return web.create_app(consoles, log_lines), autos


def _station(station: config.Station, data_dir: Path) -> tuple[web.StationConsole, AutoAcquirer | None]:
    """A station's worklist query and acquirer, over its own acquisition log."""
    ae = station.calling_ae
    results = AcquisitionLog(data_dir / ae / "acquisitions.jsonl")
    lock = threading.Lock()  # one acquisition at a time per station, from the console or auto-acquire

    def query() -> list[WorklistEntry]:
        return mwl.query_worklist(station)

    def do_acquire(entry: WorklistEntry, only_if_new: bool = False) -> acquire.AcquisitionResult | None:
        """Acquires entry and logs the outcome. only_if_new (auto-acquire) skips an accession that is
        already stored or out of attempts, checked under the lock; the console's Acquire can re-send."""
        with lock:
            if only_if_new:
                history = results.history().get(entry.accession_number)
                if history and (history.stored or history.failures >= MAX_ATTEMPTS):
                    return None
            try:
                result = acquire.acquire(entry, station)
            except Exception as e:
                log.exception("%s: Acquiring accession %s failed unexpectedly", ae, entry.accession_number)
                result = acquire.AcquisitionResult(
                    accession_number=entry.accession_number, patient_id=entry.patient_id,
                    modality=entry.modality, study_instance_uid="", source="",
                    at=datetime.now().isoformat(timespec="seconds"), error=f"Unexpected error: {e}",
                )
            try:
                results.append(result)
            except Exception:
                log.exception("%s: Couldn't write the result for accession %s to the acquisition log",
                              ae, entry.accession_number)
        if result.ok:
            log.info("%s: Stored %d image(s) for accession %s (images from %s)",
                     ae, len(result.instances), result.accession_number, result.source)
        else:
            log.warning("%s: Acquiring accession %s failed: %s", ae, result.accession_number, result.error)
        return result

    auto = AutoAcquirer(query, do_acquire, results, name=ae) if station.auto_acquire else None
    return web.StationConsole(station, query, do_acquire, results), auto


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMAT)
    recent = RecentLogs()
    logging.getLogger().addHandler(recent)
    logging.getLogger("pynetdicom").setLevel(logging.WARNING)
    try:
        cfg = config.load(config.CONFIG_PATH)
    except ConfigError as e:
        print(f"modality-simulator: {e}", file=sys.stderr)
        sys.exit(2)
    for warning in config.overlaps(cfg.stations):
        log.warning("%s", warning)
    app, autos = build(cfg, recent)
    for auto in autos:
        threading.Thread(
            target=auto.run_forever, args=(cfg.poll_seconds, threading.Event()),
            name=f"auto-acquire-{auto.name}", daemon=True,
        ).start()
    first = cfg.stations[0]
    log.info("Console on port %d; gateway %s at %s:%d", PORT, first.gateway_ae, first.gateway_host, first.gateway_port)
    for station in cfg.stations:
        log.info("Station %s (%s): %s; auto-acquire %s", station.calling_ae, station.name,
                 ", ".join(station.modalities), "on" if station.auto_acquire else "off")
    waitress.serve(app, host="0.0.0.0", port=PORT)
