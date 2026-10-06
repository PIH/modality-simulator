from dataclasses import replace

import pytest

from conftest import find_study, instance_count
from factories import write_library_file
from modality_simulator.acquire import acquire
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import query_worklist

pytestmark = pytest.mark.integration


def entry_for(cfg, accession):
    return next(e for e in query_worklist(cfg) if e.accession_number == accession)


def test_worklist_lists_the_configured_modalities(gateway):
    by_name = {e.patient_name: e for e in query_worklist(gateway)}
    assert set(by_name) == {"TEST^CR", "TEST^US", "TEST^CT", "TEST^LIB", "TEST^NOACC"}
    cr = by_name["TEST^CR"]
    assert cr.accession_number == "ACC-IT-CR1"
    assert cr.patient_id == "IT-P1"
    assert cr.modality == "CR"
    assert cr.study_instance_uid
    assert not by_name["TEST^NOACC"].acquirable


def test_station_filter_is_applied_at_the_gateway(gateway):
    entries = query_worklist(replace(gateway, station_ae_filter="SIM_US"))
    assert [e.accession_number for e in entries] == ["ACC-IT-US1"]


def test_a_wrong_called_ae_is_rejected_with_advice(gateway):
    with pytest.raises(GatewayError, match="rejected the association from SIM_TEST"):
        query_worklist(replace(gateway, gateway_ae="NOT_THE_GW"))


def test_an_unreachable_gateway_is_reported(gateway):
    with pytest.raises(GatewayError, match="couldn't be reached"):
        query_worklist(replace(gateway, gateway_port=1))


@pytest.mark.parametrize("accession,count", [("ACC-IT-CR1", 1), ("ACC-IT-US1", 1), ("ACC-IT-CT1", 3)])
def test_acquire_stores_a_study_with_the_worklist_details(gateway, tmp_path, accession, count):
    cfg = replace(gateway, image_dir=tmp_path / "no-library")
    entry = entry_for(cfg, accession)
    result = acquire(entry, cfg)
    assert result.ok, result.error
    study = find_study(accession)
    assert study is not None
    assert study["MainDicomTags"]["StudyInstanceUID"] == entry.study_instance_uid
    assert study["PatientMainDicomTags"]["PatientID"] == entry.patient_id
    assert instance_count(study) == count


def test_acquire_sends_a_compressed_library_file(gateway, tmp_path):
    write_library_file(tmp_path / "library" / "chest.dcm", "CR", compressed=True)
    cfg = replace(gateway, image_dir=tmp_path / "library")
    result = acquire(entry_for(cfg, "ACC-IT-LIB1"), cfg)
    assert result.ok, result.error
    assert result.source == "chest.dcm"
    study = find_study("ACC-IT-LIB1")
    assert study["PatientMainDicomTags"]["PatientID"] == "IT-P4"
