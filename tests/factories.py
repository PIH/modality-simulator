"""Test data builders shared by the unit and integration tests."""

from pathlib import Path

from pydicom.dataset import Dataset
from pydicom.uid import RLELossless, generate_uid

from modality_simulator.acquire import AcquisitionResult, InstanceStatus
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


def result(e: WorklistEntry, ok: bool = True, error: str = "") -> AcquisitionResult:
    return AcquisitionResult(
        accession_number=e.accession_number, patient_id=e.patient_id, modality=e.modality,
        study_instance_uid="1.2.3", source="synthetic", at="2026-10-06T12:00:00",
        instances=(InstanceStatus("1.2.3.4", 0x0000 if ok else 0xA700),),
        error="" if ok else (error or "1 of 1 images weren't stored: 0xA700"),
    )
