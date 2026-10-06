import pytest

from factories import config
from modality_simulator.auto import AutoAcquirer
from modality_simulator.main import build, main


def test_missing_gateway_ae_exits_2_saying_why(monkeypatch, capsys):
    monkeypatch.delenv("MODALITY_SIMULATOR_GATEWAY_AE", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    assert "MODALITY_SIMULATOR_GATEWAY_AE must be set" in capsys.readouterr().err


def test_build_serves_the_console_without_auto_acquire_by_default(tmp_path):
    app, auto = build(config(data_dir=tmp_path))
    assert auto is None
    assert app.test_client().get("/health").get_json() == {"status": "ok"}


def test_build_makes_an_auto_acquirer_when_it_is_on(tmp_path):
    _, auto = build(config(data_dir=tmp_path, auto_acquire=True))
    assert isinstance(auto, AutoAcquirer)
