import pytest
from pydicom.dataset import Dataset

from factories import config
from modality_simulator import mwl
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry, build_query, collect, parse_entry


def status(code: int) -> Dataset:
    ds = Dataset()
    ds.Status = code
    return ds


def identifier(accession="ACC-1", modality="CR", station="SIM_CR", name="DOE^JANE") -> Dataset:
    ds = Dataset()
    ds.PatientName = name
    ds.PatientID = "P-1"
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "F"
    ds.AccessionNumber = accession
    ds.RequestedProcedureDescription = "Chest PA"
    ds.StudyInstanceUID = "1.2.3"
    step = Dataset()
    step.Modality = modality
    step.ScheduledStationAETitle = station
    step.ScheduledProcedureStepStartDate = "20261006"
    ds.ScheduledProcedureStepSequence = [step]
    return ds


def test_parse_entry_reads_patient_order_and_step():
    assert parse_entry(identifier()) == WorklistEntry(
        patient_id="P-1", patient_name="DOE^JANE", birth_date="19800101", sex="F",
        accession_number="ACC-1", procedure="Chest PA", modality="CR",
        study_instance_uid="1.2.3", station_ae="SIM_CR", scheduled_date="20261006",
    )


def test_parse_entry_without_a_step_sequence_has_no_modality():
    ds = identifier()
    del ds.ScheduledProcedureStepSequence
    assert parse_entry(ds).modality == ""


def test_collect_gathers_pending_matches_until_success():
    responses = [(status(0xFF00), identifier("A1")), (status(0xFF01), identifier("A2")), (status(0x0000), None)]
    assert [e.accession_number for e in collect(responses, config())] == ["A1", "A2"]


def test_collect_keeps_only_configured_modalities():
    responses = [
        (status(0xFF00), identifier("A1", "CR")), (status(0xFF00), identifier("A2", "MR")),
        (status(0xFF00), identifier("A3", "US")), (status(0x0000), None),
    ]
    entries = collect(responses, config(modalities=("CR",)))
    assert [e.accession_number for e in entries] == ["A1"]


def test_collect_applies_the_station_filter_even_if_the_gateway_ignores_it():
    responses = [
        (status(0xFF00), identifier("A1", station="SIM_CR")),
        (status(0xFF00), identifier("A2", station="OTHER")), (status(0x0000), None),
    ]
    entries = collect(responses, config(station_ae_filter="SIM_CR"))
    assert [e.accession_number for e in entries] == ["A1"]


def test_a_failure_status_is_an_error_not_an_empty_worklist():
    with pytest.raises(GatewayError, match="0xA700"):
        collect([(status(0xA700), None)], config())


def test_no_status_means_the_gateway_did_not_answer():
    with pytest.raises(GatewayError, match="didn't answer"):
        collect([(Dataset(), None)], config())


def test_responses_ending_without_a_final_status_are_an_error():
    with pytest.raises(GatewayError, match="without a final status"):
        collect([(status(0xFF00), identifier())], config())


def test_entry_without_accession_is_listed_but_not_acquirable():
    entries = collect([(status(0xFF00), identifier(accession="")), (status(0x0000), None)], config())
    assert len(entries) == 1
    assert not entries[0].acquirable


def test_build_query_asks_for_every_field_we_read_and_filters_by_station():
    query = build_query(config(station_ae_filter="SIM_US"))
    for keyword in ("PatientName", "PatientID", "PatientBirthDate", "PatientSex",
                    "AccessionNumber", "RequestedProcedureDescription", "StudyInstanceUID"):
        assert keyword in query
    step = query.ScheduledProcedureStepSequence[0]
    assert step.ScheduledStationAETitle == "SIM_US"
    assert step.Modality == ""


class RefusingAssociation:
    """Established, but the peer accepted no worklist presentation context."""

    def __init__(self, error):
        self.error = error
        self.released = False

    def send_c_find(self, query, sop_class):
        raise self.error
        yield  # a generator, like pynetdicom's: the error surfaces on iteration

    def release(self):
        self.released = True


@pytest.mark.parametrize("error", [ValueError("No presentation context accepted"), RuntimeError("gone")])
def test_query_worklist_explains_a_gateway_that_accepts_the_association_but_not_the_query(monkeypatch, error):
    fake = RefusingAssociation(error)
    monkeypatch.setattr(mwl.dicom_net, "associate", lambda cfg, syntaxes: fake)
    with pytest.raises(GatewayError) as e:
        mwl.query_worklist(config(calling_ae="SIM_X"))
    assert "accepted the association but not the worklist query" in str(e.value)
    assert "SIM_X" in str(e.value) and "TEST_GW" in str(e.value)
    assert fake.released
