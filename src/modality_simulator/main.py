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
