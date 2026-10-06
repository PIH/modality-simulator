"""Acquiring a worklist entry: its images, stamped with its details, stored to the gateway."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from pydicom.dataset import Dataset
from pydicom.uid import generate_uid

from modality_simulator import dicom_net, images
from modality_simulator.config import Config
from modality_simulator.errors import GatewayError
from modality_simulator.mwl import WorklistEntry

# Identifiers a library file may still carry from the patient it came from. Left in, they'd
# contradict the worklist entry; RequestAttributesSequence holds the old accession number, which
# AdvaPACS could match on.
_LEFTOVER_IDENTIFIERS = (
    "OtherPatientIDs", "OtherPatientIDsSequence", "OtherPatientNames", "IssuerOfPatientID",
    "PatientAge", "PatientAddress", "PatientTelephoneNumbers", "PatientComments",
    "RequestAttributesSequence", "ReferencedStudySequence",
)


@dataclass(frozen=True)
class InstanceStatus:
    sop_instance_uid: str
    status: int | None  # None: no response
    detail: str = ""

    @property
    def stored(self) -> bool:
        # 0xB000-0xBFFF are warnings: stored, possibly with changes the gateway made.
        return self.status is not None and (self.status == 0x0000 or 0xB000 <= self.status <= 0xBFFF)


@dataclass(frozen=True)
class AcquisitionResult:
    accession_number: str
    patient_id: str
    modality: str
    study_instance_uid: str
    source: str
    at: str
    instances: tuple[InstanceStatus, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.instances) and all(i.stored for i in self.instances)


def stamp(datasets: Sequence[Dataset], entry: WorklistEntry, cfg: Config, now: datetime) -> list[Dataset]:
    """Makes datasets one new series of entry's study, as if this modality had just taken them."""
    study_uid = entry.study_instance_uid or generate_uid()
    series_uid = generate_uid()
    date, time = now.strftime("%Y%m%d"), now.strftime("%H%M%S")
    for number, ds in enumerate(datasets, start=1):
        for keyword in _LEFTOVER_IDENTIFIERS:
            if keyword in ds:
                delattr(ds, keyword)
        # Text never read still holds raw bytes in the file's own charset: decode it before relabelling.
        ds.decode()
        ds.SpecificCharacterSet = "ISO_IR 192"
        ds.PatientName = entry.patient_name
        ds.PatientID = entry.patient_id
        ds.PatientBirthDate = entry.birth_date
        ds.PatientSex = entry.sex
        ds.AccessionNumber = entry.accession_number
        ds.ReferringPhysicianName = ""
        ds.StudyInstanceUID = study_uid
        ds.StudyID = "1"
        ds.StudyDate = date
        ds.StudyTime = time
        ds.StudyDescription = entry.procedure
        ds.SeriesInstanceUID = series_uid
        ds.SeriesNumber = 1
        ds.SeriesDate = date
        ds.SeriesTime = time
        ds.SeriesDescription = entry.procedure
        ds.SOPInstanceUID = generate_uid()
        ds.InstanceNumber = number
        ds.ContentDate = date
        ds.ContentTime = time
        ds.AcquisitionDate = date
        ds.AcquisitionTime = time
        ds.AcquisitionDateTime = date + time
        ds.InstanceCreationDate = date
        ds.InstanceCreationTime = time
        ds.Modality = entry.modality
        ds.InstitutionName = cfg.institution
        ds.StationName = cfg.calling_ae
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.file_meta.SourceApplicationEntityTitle = cfg.calling_ae
    return list(datasets)


def send_all(datasets: Sequence[Dataset], cfg: Config) -> tuple[InstanceStatus, ...]:
    """C-STOREs each dataset on one association; raises GatewayError only if there's no association."""
    assoc = dicom_net.associate(cfg, sorted({str(ds.SOPClassUID) for ds in datasets}))
    statuses = []
    try:
        for ds in datasets:
            uid = str(ds.SOPInstanceUID)
            try:
                response = assoc.send_c_store(ds)
            except (ValueError, RuntimeError) as e:  # SOP class not accepted, or association gone
                statuses.append(InstanceStatus(uid, None, str(e)))
                continue
            if "Status" in response:
                statuses.append(InstanceStatus(uid, int(response.Status)))
            else:
                statuses.append(InstanceStatus(uid, None, "no response (the association was aborted or timed out)"))
    finally:
        assoc.release()
    return tuple(statuses)


def acquire(
    entry: WorklistEntry,
    cfg: Config,
    *,
    now: Callable[[], datetime] = datetime.now,
    pick_images: Callable[[WorklistEntry, Config], tuple[list[Dataset], str]] = images.images_for,
    send: Callable[[Sequence[Dataset], Config], tuple[InstanceStatus, ...]] = send_all,
) -> AcquisitionResult:
    at = now()
    about = dict(
        accession_number=entry.accession_number, patient_id=entry.patient_id,
        modality=entry.modality, at=at.isoformat(timespec="seconds"),
    )
    if not entry.acquirable:
        return AcquisitionResult(
            **about, study_instance_uid="", source="",
            error="The worklist entry has no accession number: AdvaPACS would put its images in the Validation Queue",
        )
    datasets, source = pick_images(entry, cfg)
    stamped = stamp(datasets, entry, cfg, at)
    study_uid = str(stamped[0].StudyInstanceUID)
    try:
        statuses = send(stamped, cfg)
    except GatewayError as e:
        return AcquisitionResult(**about, study_instance_uid=study_uid, source=source, error=str(e))
    failed = [s for s in statuses if not s.stored]
    error = ""
    if failed:
        reasons = ", ".join(
            s.detail or (f"0x{s.status:04X}" if s.status is not None else "no response") for s in failed
        )
        error = f"{len(failed)} of {len(statuses)} images weren't stored: {reasons}"
    return AcquisitionResult(
        **about, study_instance_uid=study_uid, source=source, instances=statuses, error=error,
    )
