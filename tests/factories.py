"""Test data builders shared by the unit and integration tests."""

from pathlib import Path

from pydicom.dataset import Dataset
from pydicom.uid import RLELossless, generate_uid

from modality_simulator.config import Config
from modality_simulator.images import synthetic
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


def write_library_file(path: Path, modality: str = "CR", compressed: bool = False) -> Dataset:
    """A complete DICOM file for the image library, from a made-up patient."""
    source = entry(modality, accession="LIB-ACC", patient_id="LIB-1", patient_name="LIBRARY^SOURCE")
    ds = synthetic(source)[0]
    ds.PatientName = source.patient_name
    ds.PatientID = source.patient_id
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    if compressed:
        ds.compress(RLELossless)
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)
    return ds
