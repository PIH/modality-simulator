"""The console: a tab per station, each with its worklist, an Acquire button per entry and its recent
results; and the recent log, shared by every tab."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from flask import Flask, abort, make_response, redirect, render_template_string, request, url_for

from modality_simulator.acquire import AcquisitionResult
from modality_simulator.auto import MAX_ATTEMPTS
from modality_simulator.config import Station
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry
from modality_simulator.results import AcquisitionLog

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{% if console %}{{ console.station.name }} — {% endif %}Modality simulator</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 1.5rem; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 2rem; }
  th, td { border-bottom: 1px solid #ddd; padding: .4rem .6rem; text-align: left; vertical-align: top; }
  .banner { background: #fde8e8; border: 1px solid #e0a0a0; padding: .75rem; margin-bottom: 1rem; }
  .why { color: #666; font-size: .9em; }
  nav { display: flex; gap: .25rem; border-bottom: 1px solid #ccc; margin-bottom: 1rem; }
  nav a { padding: .4rem .9rem; border: 1px solid #ccc; border-bottom: none; text-decoration: none; color: inherit; }
  nav a[aria-current] { background: #eef; font-weight: bold; }
  .ok { color: #1a7f37; }
  .failed { color: #b42318; }
  textarea { width: 100%; height: 24rem; font-family: ui-monospace, monospace; font-size: .8em; }
</style>
</head>
<body>
<h1>Modality simulator</h1>
<nav aria-label="Stations">
  {% for c in consoles %}
  <a href="{{ url_for('station_page', calling_ae=c.station.calling_ae) }}"{% if c is sameas console %} aria-current="page"{% endif %}>{{ c.station.name }}</a>
  {% endfor %}
</nav>
{% if error %}<div class="banner" role="alert">{{ error }}</div>{% endif %}
{% if notice %}<div class="banner" role="status">{{ notice }}</div>{% endif %}
{% if console %}
{% set cfg = console.station %}
<p>{{ cfg.calling_ae }} &rarr; {{ cfg.gateway_ae }} at {{ cfg.gateway_host }}:{{ cfg.gateway_port }}
  &middot; {{ cfg.modalities | join(", ") }}
  &middot; auto-acquire {{ "on" if cfg.auto_acquire else "off" }}</p>

<h2>Worklist</h2>
{% if entries %}
<table>
  <tr><th>Patient</th><th>ID</th><th>Accession</th><th>Procedure</th><th>Modality</th>
      <th>Station</th><th>Scheduled</th><th>Status</th><th></th></tr>
  {% for e in entries %}
  {% set h = history.get(e.accession_number) if e.accession_number else none %}
  <tr>
    <td>{{ e.patient_name }}</td><td>{{ e.patient_id }}</td><td>{{ e.accession_number }}</td>
    <td>{{ e.procedure }}</td><td>{{ e.modality }}</td><td>{{ e.station_ae }}</td><td>{{ e.scheduled }}</td>
    <td>
      {% if h and h.stored %}<span class="ok">stored</span>
      {% elif h and h.failures %}<span class="failed">failed {{ h.failures }}&times;{% if cfg.auto_acquire and h.failures >= max_attempts %} (auto-acquire gave up){% endif %}</span>
      {% endif %}
    </td>
    <td>
      {% if e.acquirable %}
      <form method="post" action="{{ url_for('acquire_entry', calling_ae=cfg.calling_ae) }}" onsubmit="this.querySelector('button').disabled = true">
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
{% endif %}

<h2>Recent log</h2>
<p class="why">Includes each worklist query and every raw response from the gateway, newest at the bottom.</p>
<textarea id="log" readonly wrap="off" aria-label="Recent log">{{ log_lines | join("\n") }}</textarea>
<script>const l = document.getElementById("log"); l.scrollTop = l.scrollHeight;</script>
</body>
</html>
"""


@dataclass(frozen=True)
class StationConsole:
    """What the console needs of a station: its settings, worklist, acquirer and acquisition log."""

    station: Station
    query: Callable[[], list[WorklistEntry]]
    do_acquire: Callable[[WorklistEntry], AcquisitionResult]
    log: AcquisitionLog


def create_app(consoles: Sequence[StationConsole], log_lines: Callable[[], list[str]] = list) -> Flask:
    app = Flask(__name__)
    by_ae = {c.station.calling_ae: c for c in consoles}

    def worklist(console: StationConsole) -> tuple[list[WorklistEntry], str]:
        """The station's worklist, latest scheduled first."""
        try:
            return sorted(console.query(), key=lambda e: e.scheduled_sort_key, reverse=True), ""
        except GatewayError as e:
            return [], str(e)

    def page(console: StationConsole | None, entries: list[WorklistEntry], error: str = "", notice: str = "",
             status: int = 200):
        html = render_template_string(
            PAGE, consoles=consoles, console=console, entries=entries, error=error, notice=notice,
            history=console.log.history() if console else {}, recent=console.log.recent(20) if console else [],
            max_attempts=MAX_ATTEMPTS, log_lines=log_lines(),
        )
        return html, status

    def station(calling_ae: str) -> StationConsole:
        console = by_ae.get(calling_ae)
        if console is None:
            abort(make_response(*page(None, [], notice=f"There's no station {calling_ae}: pick one of the tabs", status=404)))
        return console

    @app.get("/")
    def index():
        return redirect(url_for("station_page", calling_ae=consoles[0].station.calling_ae), code=303)

    @app.get("/station/<calling_ae>")
    def station_page(calling_ae: str):
        console = station(calling_ae)
        entries, error = worklist(console)
        return page(console, entries, error)

    @app.post("/station/<calling_ae>/acquire")
    def acquire_entry(calling_ae: str):
        console = station(calling_ae)
        accession = request.form.get("accession", "").strip()
        entries, error = worklist(console)
        if error:
            return page(console, [], error, status=502)
        entry = next((e for e in entries if e.acquirable and e.accession_number == accession), None)
        if entry is None:
            return page(console, entries, notice=f"Accession {accession} isn't in the worklist any more", status=404)
        try:
            console.do_acquire(entry)
        except Exception as e:
            return page(console, entries, error=f"Acquiring accession {accession} failed unexpectedly: {e}", status=500)
        return redirect(url_for("station_page", calling_ae=calling_ae), code=303)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
