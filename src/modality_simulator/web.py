"""The console: the worklist with an Acquire button per entry, recent results, and the recent log."""

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
  textarea { width: 100%; height: 24rem; font-family: ui-monospace, monospace; font-size: .8em; }
</style>
</head>
<body>
<h1>Modality simulator</h1>
<p>{{ cfg.calling_ae }} &rarr; {{ cfg.gateway_ae }} at {{ cfg.gateway_host }}:{{ cfg.gateway_port }}
  &middot; {{ cfg.modalities | join(", ") }}
  &middot; auto-acquire {{ "on" if cfg.auto_acquire else "off" }}</p>
{% if error %}<div class="banner" role="alert">{{ error }}</div>{% endif %}
{% if notice %}<div class="banner" role="status">{{ notice }}</div>{% endif %}
{% if can_probe_mpps %}
<form method="post" action="{{ url_for('probe_mpps_check') }}">
  <button>Check MPPS support</button>
  <span class="why">Asks the gateway whether it accepts MPPS (exam progress messages). Sends no MPPS message.</span>
</form>
{% endif %}

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

<h2>Recent log</h2>
<p class="why">Includes each worklist query and every raw response from the gateway, newest at the bottom.</p>
<textarea id="log" readonly wrap="off" aria-label="Recent log">{{ log_lines | join("\n") }}</textarea>
<script>const l = document.getElementById("log"); l.scrollTop = l.scrollHeight;</script>
</body>
</html>
"""


def create_app(
    cfg: Config,
    query: Callable[[], list[WorklistEntry]],
    do_acquire: Callable[[WorklistEntry], AcquisitionResult],
    log: AcquisitionLog,
    log_lines: Callable[[], list[str]] = list,
    probe_mpps: Callable[[], str] | None = None,
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
            log_lines=log_lines(), can_probe_mpps=probe_mpps is not None,
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
        try:
            do_acquire(entry)
        except Exception as e:
            return page(entries, error=f"Acquiring accession {accession} failed unexpectedly: {e}", status=500)
        return redirect(url_for("index"), code=303)

    @app.post("/probe-mpps")
    def probe_mpps_check():
        if probe_mpps is None:
            return page([], error="The MPPS check isn't available", status=404)
        notice = probe_mpps()
        entries, error = worklist()
        return page(entries, error, notice)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app
