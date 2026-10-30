from factories import entry, result, station
from modality_simulator.errors import GatewayError
from modality_simulator.results import AcquisitionLog
from modality_simulator.web import StationConsole, create_app

CR = "/station/SIM_CR"
US = "/station/SIM_US"


class Consoles:
    """The console over several stations, each with its own worklist, gateway error and log."""

    def __init__(self, tmp_path, stations, log_lines=()):
        self.logs = {}
        self.acquired = {}
        self.queries = 0
        consoles = []
        for s, entries, error in stations:
            log = AcquisitionLog(tmp_path / s.calling_ae / "acquisitions.jsonl")
            self.logs[s.calling_ae] = log
            consoles.append(StationConsole(s, self._query(list(entries), error), self._acquirer(s, log), log))
        self.client = create_app(consoles, lambda: list(log_lines)).test_client()

    def _query(self, entries, error):
        def query():
            self.queries += 1
            if error:
                raise GatewayError(error)
            return list(entries)
        return query

    def _acquirer(self, s, log):
        def do_acquire(e):
            self.acquired.setdefault(s.calling_ae, []).append(e.accession_number)
            r = result(e)
            log.append(r)
            return r
        return do_acquire


class Console(Consoles):
    """One station, SIM_CR unless cfg says otherwise."""

    def __init__(self, tmp_path, entries=(), gateway_error=None, cfg=None, log_lines=()):
        cfg = cfg or station()
        super().__init__(tmp_path, [(cfg, entries, gateway_error)], log_lines)
        self.log = self.logs[cfg.calling_ae]


def two_stations(tmp_path, errors=None, log_lines=()):
    errors = errors or {}
    return Consoles(tmp_path, [
        (station(name="X-ray", calling_ae="SIM_CR", modalities=("CR",)), [entry("CR", accession="ACC-CR")], errors.get("SIM_CR")),
        (station(name="Ultrasound", calling_ae="SIM_US", modalities=("US",)), [entry("US", accession="ACC-US")], errors.get("SIM_US")),
    ], log_lines)


def test_lists_entries_with_acquire_buttons(tmp_path):
    page = Console(tmp_path, [entry("CR", accession="ACC-1")]).client.get(CR)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "ACC-1" in html
    assert 'name="accession" value="ACC-1"' in html
    assert "TEST_GW" in html


def test_the_worklist_shows_the_scheduled_date_and_time_latest_first(tmp_path):
    entries = [
        entry(accession="ACC-MORNING", scheduled_date="20261006", scheduled_time="0900"),
        entry(accession="ACC-NO-DATE", scheduled_date=""),
        entry(accession="ACC-NEXT-DAY", scheduled_date="20261007", scheduled_time="080000"),
        entry(accession="ACC-AFTERNOON", scheduled_date="20261006", scheduled_time="143000"),
    ]
    html = Console(tmp_path, entries).client.get(CR).get_data(as_text=True)
    order = ["ACC-NEXT-DAY", "ACC-AFTERNOON", "ACC-MORNING", "ACC-NO-DATE"]
    assert [html.index(f'value="{a}"') for a in order] == sorted(html.index(f'value="{a}"') for a in order)
    assert "<td>2026-10-06 14:30</td>" in html


def test_patient_names_are_escaped(tmp_path):
    html = Console(tmp_path, [entry(patient_name="<script>x</script>")]).client.get(CR).get_data(as_text=True)
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_an_entry_without_accession_cannot_be_acquired(tmp_path):
    html = Console(tmp_path, [entry(accession="")]).client.get(CR).get_data(as_text=True)
    assert "<button disabled>Acquire</button>" in html
    assert "Validation Queue" in html


def test_a_gateway_error_shows_a_banner(tmp_path):
    page = Console(tmp_path, gateway_error="The gateway couldn't be reached").client.get(CR)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'role="alert"' in html
    assert "couldn&#39;t be reached" in html
    assert "The worklist is empty" not in html


def test_an_empty_worklist_says_so(tmp_path):
    assert "The worklist is empty" in Console(tmp_path).client.get(CR).get_data(as_text=True)


def test_acquire_acquires_the_entry_and_redirects(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")])
    response = console.client.post(CR + "/acquire", data={"accession": "ACC-2"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith(CR)
    assert console.acquired == {"SIM_CR": ["ACC-2"]}


def test_acquiring_an_accession_no_longer_in_the_worklist_is_a_404(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1")])
    response = console.client.post(CR + "/acquire", data={"accession": "GONE"})
    assert response.status_code == 404
    assert "GONE" in response.get_data(as_text=True)
    assert console.acquired == {}


def test_acquiring_with_the_gateway_down_is_a_502(tmp_path):
    console = Console(tmp_path, gateway_error="down")
    response = console.client.post(CR + "/acquire", data={"accession": "ACC-1"})
    assert response.status_code == 502
    assert console.acquired == {}


def test_recent_results_and_status_are_shown(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")], cfg=station(auto_acquire=True))
    console.log.append(result(entry(accession="ACC-1")))
    for _ in range(3):
        console.log.append(result(entry(accession="ACC-2"), ok=False, error="0xA700 from the gateway"))
    html = console.client.get(CR).get_data(as_text=True)
    assert "stored 1 image(s)" in html
    assert "0xA700 from the gateway" in html
    assert "auto-acquire gave up" in html


def test_the_button_is_disabled_once_clicked(tmp_path):
    html = Console(tmp_path, [entry()]).client.get(CR).get_data(as_text=True)
    assert "this.querySelector('button').disabled = true" in html


def test_health_does_not_touch_the_gateway(tmp_path):
    console = Console(tmp_path, gateway_error="down")
    response = console.client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert console.queries == 0


def test_an_unexpected_error_while_acquiring_renders_the_page_with_the_message(tmp_path):
    c = Console(tmp_path, [entry("CR", accession="A1")])

    def boom(e):
        raise RuntimeError("disk on fire")
    client = create_app([StationConsole(station(), lambda: [entry("CR", accession="A1")], boom, c.log)]).test_client()
    response = client.post(CR + "/acquire", data={"accession": "A1"})
    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert "disk on fire" in body and "Modality simulator" in body



def test_the_recent_log_is_shown_escaped_at_the_bottom(tmp_path):
    lines = ["12:00:00 INFO modality_simulator.mwl: C-FIND response", "(0010,0010) Patient's Name PN: <b>X</b>"]
    html = Console(tmp_path, log_lines=lines).client.get(CR).get_data(as_text=True)
    assert "C-FIND response" in html
    assert "&lt;b&gt;X&lt;/b&gt;" in html
    assert html.index("Recent acquisitions") < html.index("<textarea")


def test_root_redirects_to_the_first_station(tmp_path):
    response = two_stations(tmp_path).client.get("/")
    assert response.status_code == 303
    assert response.headers["Location"].endswith(CR)


def test_each_station_has_a_tab_and_shows_only_its_own_worklist(tmp_path):
    html = two_stations(tmp_path).client.get(US).get_data(as_text=True)
    assert f'href="{CR}"' in html and f'href="{US}"' in html
    assert html.count('aria-current="page"') == 1
    assert f'<a href="{US}" aria-current="page">Ultrasound</a>' in html
    assert "ACC-US" in html and "ACC-CR" not in html
    assert "SIM_US &rarr;" in html


def test_acquire_uses_that_stations_acquirer_and_returns_to_its_tab(tmp_path):
    c = two_stations(tmp_path)
    response = c.client.post(US + "/acquire", data={"accession": "ACC-US"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith(US)
    assert c.acquired == {"SIM_US": ["ACC-US"]}


def test_an_accession_from_another_station_is_not_acquired(tmp_path):
    c = two_stations(tmp_path)
    response = c.client.post(CR + "/acquire", data={"accession": "ACC-US"})
    assert response.status_code == 404
    assert c.acquired == {}


def test_a_gateway_error_shows_only_on_that_stations_tab(tmp_path):
    c = two_stations(tmp_path, errors={"SIM_CR": "gateway down"})
    assert "gateway down" in c.client.get(CR).get_data(as_text=True)
    us = c.client.get(US).get_data(as_text=True)
    assert "gateway down" not in us
    assert "ACC-US" in us


def test_recent_acquisitions_are_per_station(tmp_path):
    c = two_stations(tmp_path)
    c.logs["SIM_CR"].append(result(entry("CR", accession="ACC-CR-DONE")))
    assert "ACC-CR-DONE" in c.client.get(CR).get_data(as_text=True)
    assert "ACC-CR-DONE" not in c.client.get(US).get_data(as_text=True)


def test_an_unknown_station_is_a_404_with_the_tabs(tmp_path):
    c = two_stations(tmp_path)
    for response in (c.client.get("/station/NOPE"),
                     c.client.post("/station/NOPE/acquire", data={"accession": "ACC-CR"})):
        assert response.status_code == 404
        html = response.get_data(as_text=True)
        assert f'href="{CR}"' in html and "NOPE" in html
    assert c.acquired == {}


def test_the_recent_log_is_on_every_tab(tmp_path):
    c = two_stations(tmp_path, log_lines=["line one"])
    assert "line one" in c.client.get(CR).get_data(as_text=True)
    assert "line one" in c.client.get(US).get_data(as_text=True)
