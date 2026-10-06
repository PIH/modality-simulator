"""Test data builders shared by the unit and integration tests."""

from modality_simulator.config import Config
from modality_simulator.mwl import WorklistEntry


def config(**overrides) -> Config:
    return Config(**{"gateway_ae": "TEST_GW", **overrides})


def entry(modality: str = "CR", accession: str = "ACC-1", **overrides) -> WorklistEntry:
    values = dict(
        patient_id="P-1", patient_name="DOE^JANE", birth_date="19800101", sex="F",
        accession_number=accession, procedure=f"{modality} test procedure", modality=modality,
        study_instance_uid="", station_ae="SIM_CR", scheduled_date="20261006",
    )
    values.update(overrides)
    return WorklistEntry(**values)
