"""Starts the Orthanc stand-in gateway once per session, with generated worklist entries."""

import subprocess
import time
from pathlib import Path

import pytest
import requests
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from modality_simulator.config import Config

HERE = Path(__file__).parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "docker-compose.yml"), "-p", "modality-simulator-it"]
WORKLISTS = HERE / "worklists"
REST = "http://localhost:18042"
MODALITY_WORKLIST_SOP_CLASS = "1.2.840.10008.5.1.4.31"

# accession, patient ID, patient name, modality, scheduled station AE
FIXTURES = [
    ("ACC-IT-CR1", "IT-P1", "TEST^CR", "CR", "SIM_CR"),
    ("ACC-IT-US1", "IT-P2", "TEST^US", "US", "SIM_US"),
    ("ACC-IT-CT1", "IT-P3", "TEST^CT", "CT", "SIM_CT"),
    ("ACC-IT-LIB1", "IT-P4", "TEST^LIB", "CR", "SIM_CR"),
    ("", "IT-P5", "TEST^NOACC", "CR", "SIM_CR"),
    ("ACC-IT-MR1", "IT-P6", "TEST^MR", "MR", "SIM_MR"),
]


def write_worklist(path: Path, accession: str, patient_id: str, name: str, modality: str, station: str) -> None:
    ds = Dataset()
    ds.SpecificCharacterSet = "ISO_IR 100"
    ds.PatientName = name
    ds.PatientID = patient_id
    ds.PatientBirthDate = "19800101"
    ds.PatientSex = "F"
    ds.AccessionNumber = accession
    ds.RequestedProcedureID = "1"
    ds.RequestedProcedureDescription = f"{modality} test procedure"
    ds.StudyInstanceUID = generate_uid()
    step = Dataset()
    step.Modality = modality
    step.ScheduledStationAETitle = station
    step.ScheduledProcedureStepStartDate = "20261006"
    step.ScheduledProcedureStepID = "1"
    ds.ScheduledProcedureStepSequence = [step]
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = MODALITY_WORKLIST_SOP_CLASS
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.save_as(path, enforce_file_format=True)


def wait_for(url: str, seconds: int = 90) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            if requests.get(url, timeout=2).ok:
                return
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"{url} didn't answer within {seconds}s")
        time.sleep(1)


@pytest.fixture(scope="session")
def gateway():
    WORKLISTS.mkdir(exist_ok=True)
    for old in WORKLISTS.glob("*.wl"):
        old.unlink()
    for number, fixture in enumerate(FIXTURES):
        write_worklist(WORKLISTS / f"{number}.wl", *fixture)
    subprocess.run([*COMPOSE, "up", "-d"], check=True)
    try:
        wait_for(f"{REST}/system")
        yield Config(gateway_ae="TEST_GW", gateway_host="localhost", gateway_port=14242, calling_ae="SIM_TEST")
    finally:
        subprocess.run([*COMPOSE, "down", "-v"], check=False)


def find_study(accession: str) -> dict | None:
    response = requests.post(
        f"{REST}/tools/find",
        json={"Level": "Study", "Query": {"AccessionNumber": accession}, "Expand": True},
        timeout=10,
    )
    response.raise_for_status()
    studies = response.json()
    return studies[0] if studies else None


def instance_count(study: dict) -> int:
    response = requests.get(f"{REST}/studies/{study['ID']}/instances", timeout=10)
    response.raise_for_status()
    return len(response.json())
