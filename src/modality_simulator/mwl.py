"""Reading the gateway's DICOM Modality Worklist."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Iterator

from pydicom.dataset import Dataset
from pynetdicom.sop_class import ModalityWorklistInformationFind

from modality_simulator import dicom_net
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError

log = logging.getLogger(__name__)

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
    # Not read into entries: asked for so the raw responses in the log show what the gateway reports.
    step.ScheduledProcedureStepStatus = ""
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
    query = build_query(cfg)
    log.info("Worklist C-FIND from %s to %s at %s:%d:\n%s",
             cfg.calling_ae, cfg.gateway_ae, cfg.gateway_host, cfg.gateway_port, query)
    assoc = dicom_net.associate(cfg, [ModalityWorklistInformationFind])
    try:
        return collect(_logged(assoc.send_c_find(query, ModalityWorklistInformationFind)), cfg)
    except (ValueError, RuntimeError) as e:  # no worklist presentation context accepted, or the association is gone
        raise GatewayError(
            f"The gateway ({cfg.gateway_ae} at {cfg.gateway_host}:{cfg.gateway_port}) accepted the association "
            f"but not the worklist query: check that its worklist is enabled and that {cfg.calling_ae} may query it"
        ) from e
    finally:
        assoc.release()


def _logged(responses: Iterable[tuple[Dataset, Dataset | None]]) -> Iterator[tuple[Dataset, Dataset | None]]:
    """responses, each logged as it arrives: every entry the gateway sent, before any filtering."""
    for status, identifier in responses:
        code = f"0x{int(status.Status):04X}" if "Status" in status else "none"
        if identifier is None:
            log.info("Worklist C-FIND response, status %s", code)
        else:
            log.info("Worklist C-FIND response, status %s:\n%s", code, identifier)
        yield status, identifier


def _text(ds: Dataset, keyword: str) -> str:
    return str(ds.get(keyword, "") or "").strip()
