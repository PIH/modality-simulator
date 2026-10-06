"""Reading the gateway's DICOM Modality Worklist."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pydicom.dataset import Dataset
from pynetdicom.sop_class import ModalityWorklistInformationFind

from modality_simulator import dicom_net
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

PENDING = (0xFF00, 0xFF01)
SUCCESS = 0x0000


@dataclass(frozen=True)
class WorklistEntry:
    patient_id: str
    patient_name: str
    birth_date: str
    sex: str
    accession_number: str
    procedure: str
    modality: str
    study_instance_uid: str = ""
    station_ae: str = ""
    scheduled_date: str = ""

    @property
    def acquirable(self) -> bool:
        """Images sent without an accession number land in AdvaPACS's Validation Queue."""
        return bool(self.accession_number)


def build_query(cfg: Config) -> Dataset:
    ds = Dataset()
    ds.PatientName = ""
    ds.PatientID = ""
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.AccessionNumber = ""
    ds.RequestedProcedureDescription = ""
    ds.StudyInstanceUID = ""
    step = Dataset()
    # Matched in collect() rather than at the gateway: a worklist query matches only one modality.
    step.Modality = ""
    step.ScheduledStationAETitle = cfg.station_ae_filter
    step.ScheduledProcedureStepStartDate = ""
    ds.ScheduledProcedureStepSequence = [step]
    return ds


def parse_entry(identifier: Dataset) -> WorklistEntry:
    steps = identifier.get("ScheduledProcedureStepSequence") or [Dataset()]
    step = steps[0]
    return WorklistEntry(
        patient_id=_text(identifier, "PatientID"),
        patient_name=_text(identifier, "PatientName"),
        birth_date=_text(identifier, "PatientBirthDate"),
        sex=_text(identifier, "PatientSex"),
        accession_number=_text(identifier, "AccessionNumber"),
        procedure=_text(identifier, "RequestedProcedureDescription"),
        modality=_text(step, "Modality").upper(),
        study_instance_uid=_text(identifier, "StudyInstanceUID"),
        station_ae=_text(step, "ScheduledStationAETitle"),
        scheduled_date=_text(step, "ScheduledProcedureStepStartDate"),
    )


def collect(responses: Iterable[tuple[Dataset, Dataset | None]], cfg: Config) -> list[WorklistEntry]:
    """The entries in a C-FIND's responses for cfg's modalities (and station, if filtered)."""
    entries = []
    for status, identifier in responses:
        if "Status" not in status:
            raise GatewayError(
                "The gateway didn't answer the worklist query (the association was aborted or timed out)"
            )
        code = int(status.Status)
        if code in PENDING:
            if identifier is None:
                continue
            entry = parse_entry(identifier)
            if entry.modality not in cfg.modalities:
                continue
            if cfg.station_ae_filter and entry.station_ae != cfg.station_ae_filter:
                continue
            entries.append(entry)
        elif code == SUCCESS:
            return entries
        else:
            raise GatewayError(f"The gateway's worklist query failed with status 0x{code:04X}")
    raise GatewayError("The worklist query ended without a final status")


def query_worklist(cfg: Config) -> list[WorklistEntry]:
    assoc = dicom_net.associate(cfg, [ModalityWorklistInformationFind])
    try:
        return collect(assoc.send_c_find(build_query(cfg), ModalityWorklistInformationFind), cfg)
    finally:
        assoc.release()


def _text(ds: Dataset, keyword: str) -> str:
    return str(ds.get(keyword, "") or "").strip()
