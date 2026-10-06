from factories import config, entry, result
from modality_simulator.errors import GatewayError
from modality_simulator.results import AcquisitionLog
from modality_simulator.web import create_app


class Console:
    def __init__(self, tmp_path, entries=(), gateway_error=None, cfg=None):
        self.log = AcquisitionLog(tmp_path / "acquisitions.jsonl")
        self.entries = list(entries)
        self.gateway_error = gateway_error
        self.queries = 0
        self.acquired = []
        app = create_app(cfg or config(), self.query, self.do_acquire, self.log)
        self.client = app.test_client()

    def query(self):
        self.queries += 1
        if self.gateway_error:
            raise GatewayError(self.gateway_error)
        return list(self.entries)

    def do_acquire(self, e):
        self.acquired.append(e)
        r = result(e)
        self.log.append(r)
        return r


def test_lists_entries_with_acquire_buttons(tmp_path):
    page = Console(tmp_path, [entry("CR", accession="ACC-1")]).client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "ACC-1" in html
    assert 'name="accession" value="ACC-1"' in html
    assert "TEST_GW" in html


def test_patient_names_are_escaped(tmp_path):
    html = Console(tmp_path, [entry(patient_name="<script>x</script>")]).client.get("/").get_data(as_text=True)
    assert "<script>x" not in html
    assert "&lt;script&gt;x" in html


def test_an_entry_without_accession_cannot_be_acquired(tmp_path):
    html = Console(tmp_path, [entry(accession="")]).client.get("/").get_data(as_text=True)
    assert "<button disabled>Acquire</button>" in html
    assert "Validation Queue" in html


def test_a_gateway_error_shows_a_banner(tmp_path):
    page = Console(tmp_path, gateway_error="The gateway couldn't be reached").client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'role="alert"' in html
    assert "couldn&#39;t be reached" in html
    assert "The worklist is empty" not in html


def test_an_empty_worklist_says_so(tmp_path):
    assert "The worklist is empty" in Console(tmp_path).client.get("/").get_data(as_text=True)


def test_acquire_acquires_the_entry_and_redirects(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")])
    response = console.client.post("/acquire", data={"accession": "ACC-2"})
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/")
    assert [e.accession_number for e in console.acquired] == ["ACC-2"]


def test_acquiring_an_accession_no_longer_in_the_worklist_is_a_404(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1")])
    response = console.client.post("/acquire", data={"accession": "GONE"})
    assert response.status_code == 404
    assert "GONE" in response.get_data(as_text=True)
    assert console.acquired == []


def test_acquiring_with_the_gateway_down_is_a_502(tmp_path):
    console = Console(tmp_path, gateway_error="down")
    response = console.client.post("/acquire", data={"accession": "ACC-1"})
    assert response.status_code == 502
    assert console.acquired == []


def test_recent_results_and_status_are_shown(tmp_path):
    console = Console(tmp_path, [entry(accession="ACC-1"), entry(accession="ACC-2")], cfg=config(auto_acquire=True))
    console.log.append(result(entry(accession="ACC-1")))
    for _ in range(3):
        console.log.append(result(entry(accession="ACC-2"), ok=False, error="0xA700 from the gateway"))
    html = console.client.get("/").get_data(as_text=True)
    assert "stored 1 image(s)" in html
    assert "0xA700 from the gateway" in html
    assert "auto-acquire gave up" in html


def test_the_button_is_disabled_once_clicked(tmp_path):
    html = Console(tmp_path, [entry()]).client.get("/").get_data(as_text=True)
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
    client = create_app(config(), c.query, boom, c.log).test_client()
    response = client.post("/acquire", data={"accession": "A1"})
    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert "disk on fire" in body and "Modality simulator" in body

