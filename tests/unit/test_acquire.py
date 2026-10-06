import json
from dataclasses import asdict
from datetime import datetime

import pytest
from pydicom import dcmread
from pydicom.dataset import Dataset

from factories import config, entry, write_library_file
from modality_simulator import acquire as acquire_module
from modality_simulator.acquire import InstanceStatus, acquire, send_all, stamp
from modality_simulator.errors import GatewayError
from modality_simulator.images import synthetic

NOW = datetime(2026, 10, 6, 14, 30, 5)


def sent_ok(datasets, cfg):
    return tuple(InstanceStatus(str(ds.SOPInstanceUID), 0x0000) for ds in datasets)


def test_stamp_sets_patient_study_and_new_uids():
    e = entry("CT", study_instance_uid="1.2.826.0.1.99", patient_name="Ñoño^Zoë")
    stamped = stamp(synthetic(e), e, config(institution="Test Hospital", calling_ae="SIM_CT"), NOW)
    assert len({ds.SOPInstanceUID for ds in stamped}) == len(stamped)
    assert len({ds.SeriesInstanceUID for ds in stamped}) == 1
    assert [ds.InstanceNumber for ds in stamped] == [1, 2, 3]
    for ds in stamped:
        assert ds.PatientName == "Ñoño^Zoë"
        assert ds.PatientID == e.patient_id
        assert ds.PatientBirthDate == e.birth_date
        assert ds.PatientSex == e.sex
        assert ds.AccessionNumber == e.accession_number
        assert ds.StudyInstanceUID == "1.2.826.0.1.99"
        assert ds.StudyDescription == e.procedure
        assert ds.StudyDate == "20261006" and ds.StudyTime == "143005"
        assert ds.AcquisitionDate == "20261006" and ds.AcquisitionTime == "143005"
        assert ds.AcquisitionDateTime == "20261006143005"
        assert ds.InstanceCreationDate == "20261006" and ds.InstanceCreationTime == "143005"
        assert ds.Modality == "CT"
        assert ds.InstitutionName == "Test Hospital"
        assert ds.StationName == "SIM_CT"
        assert ds.SpecificCharacterSet == "ISO_IR 192"
        assert ds.file_meta.MediaStorageSOPInstanceUID == ds.SOPInstanceUID
        assert ds.file_meta.SourceApplicationEntityTitle == "SIM_CT"


def test_stamp_replaces_the_library_files_old_dates():
    e = entry("CR")
    ds = synthetic(e)[0]
    ds.AcquisitionDate = "19990101"
    ds.InstanceCreationDate = "19990101"
    stamped = stamp([ds], e, config(), NOW)[0]
    assert stamped.AcquisitionDate == "20261006"
    assert stamped.InstanceCreationDate == "20261006"


def test_stamp_keeps_untouched_latin1_text_correct_as_utf8(tmp_path):
    e = entry("CR")
    write_library_file(tmp_path / "lib.dcm")
    path = tmp_path / "latin1.dcm"
    source = dcmread(tmp_path / "lib.dcm")
    source.SpecificCharacterSet = "ISO_IR 100"
    # As a library file holds it: raw bytes in the file's charset, never decoded.
    source.add_new(0x00081040, "LO", "Département".encode("latin-1"))
    source.save_as(path, enforce_file_format=True)
    loaded = dcmread(path)  # InstitutionalDepartmentName is still undecoded bytes
    stamp([loaded], e, config(), NOW)
    # Decoded under the original charset, before the label changed to UTF-8.
    assert not loaded.get_item(0x00081040).is_raw
    assert loaded.InstitutionalDepartmentName == "Département"
    loaded.save_as(tmp_path / "out.dcm", enforce_file_format=True)
    again = dcmread(tmp_path / "out.dcm")
    assert again.SpecificCharacterSet == "ISO_IR 192"
    assert again.InstitutionalDepartmentName == "Département"


def test_stamp_generates_a_study_uid_when_the_worklist_has_none():
    e = entry("CR", study_instance_uid="")
    first = stamp(synthetic(e), e, config(), NOW)[0].StudyInstanceUID
    second = stamp(synthetic(e), e, config(), NOW)[0].StudyInstanceUID
    assert first and second and first != second


def test_stamp_removes_the_source_patients_leftover_identifiers():
    e = entry("CR")
    ds = synthetic(e)[0]
    ds.OtherPatientIDs = "OLD-ID"
    ds.IssuerOfPatientID = "OLD-HOSPITAL"
    ds.PatientAge = "070Y"
    old_request = Dataset()
    old_request.AccessionNumber = "OLD-ACC"
    ds.RequestAttributesSequence = [old_request]
    stamped = stamp([ds], e, config(), NOW)[0]
    for keyword in ("OtherPatientIDs", "IssuerOfPatientID", "PatientAge", "RequestAttributesSequence"):
        assert keyword not in stamped


def test_acquire_succeeds_when_every_image_is_stored():
    result = acquire(entry("CT"), config(), now=lambda: NOW, send=sent_ok,
                     pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert result.ok
    assert len(result.instances) == 3
    assert result.source == "synthetic"
    assert result.at == "2026-10-06T14:30:05"
    assert result.error == ""


def test_warning_statuses_count_as_stored():
    def warned(datasets, cfg):
        return tuple(InstanceStatus(str(ds.SOPInstanceUID), 0xB000) for ds in datasets)
    result = acquire(entry("CR"), config(), send=warned, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert result.ok


def test_a_partial_failure_is_a_failure_with_the_statuses():
    def one_fails(datasets, cfg):
        codes = [0x0000, 0xA700, 0x0000]
        return tuple(InstanceStatus(str(ds.SOPInstanceUID), c) for ds, c in zip(datasets, codes))
    result = acquire(entry("CT"), config(), send=one_fails, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert not result.ok
    assert "1 of 3 images weren't stored" in result.error
    assert "0xA700" in result.error


def test_a_gateway_error_is_a_failed_result_not_an_exception():
    def unreachable(datasets, cfg):
        raise GatewayError("The gateway couldn't be reached")
    result = acquire(entry("CR"), config(), send=unreachable, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert not result.ok
    assert result.error == "The gateway couldn't be reached"


def test_an_entry_without_accession_is_never_sent():
    def must_not_send(datasets, cfg):
        raise AssertionError("sent")
    result = acquire(entry("CR", accession=""), config(), send=must_not_send)
    assert not result.ok
    assert "Validation Queue" in result.error


def test_results_serialize_to_json():
    result = acquire(entry("CR"), config(), send=sent_ok, pick_images=lambda e, cfg: (synthetic(e), "synthetic"))
    assert json.loads(json.dumps(asdict(result)))["instances"][0]["status"] == 0


class FakeAssociation:
    def __init__(self, accepted_sop_classes):
        self.accepted = accepted_sop_classes
        self.released = False

    def send_c_store(self, ds):
        if ds.SOPClassUID not in self.accepted:
            raise ValueError(f"No presentation context for '{ds.SOPClassUID}' has been accepted")
        status = Dataset()
        status.Status = 0x0000
        return status

    def release(self):
        self.released = True


def test_send_all_reports_an_unaccepted_sop_class_per_image(monkeypatch):
    fake = FakeAssociation(accepted_sop_classes=set())
    monkeypatch.setattr(acquire_module.dicom_net, "associate", lambda cfg, syntaxes: fake)
    e = entry("CT")
    statuses = send_all(stamp(synthetic(e), e, config(), NOW), config())
    assert [s.stored for s in statuses] == [False, False, False]
    assert "No presentation context" in statuses[0].detail
    assert fake.released


def test_send_all_proposes_each_sop_class_once(monkeypatch):
    proposed = []
    def fake_associate(cfg, syntaxes):
        proposed.extend(syntaxes)
        return FakeAssociation(accepted_sop_classes=set(syntaxes))
    monkeypatch.setattr(acquire_module.dicom_net, "associate", fake_associate)
    e = entry("CT")
    statuses = send_all(stamp(synthetic(e), e, config(), NOW), config())
    assert all(s.stored for s in statuses)
    assert len(proposed) == 1
