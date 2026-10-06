import numpy as np
import pytest
from pydicom.uid import ExplicitVRLittleEndian
from pynetdicom.sop_class import CTImageStorage, ComputedRadiographyImageStorage, UltrasoundImageStorage

from factories import entry
from modality_simulator.images import CT_SLICES, synthetic


@pytest.mark.parametrize("modality,sop_class,count", [
    ("CR", ComputedRadiographyImageStorage, 1),
    ("US", UltrasoundImageStorage, 1),
    ("CT", CTImageStorage, CT_SLICES),
])
def test_synthetic_images_have_the_modality_sop_class_and_pixels(modality, sop_class, count):
    images = synthetic(entry(modality))
    assert len(images) == count
    for ds in images:
        assert ds.SOPClassUID == sop_class
        assert ds.file_meta.MediaStorageSOPClassUID == sop_class
        assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
        assert ds.Modality == modality
        assert ds.pixel_array.shape == (ds.Rows, ds.Columns)
        assert ds.pixel_array.max() > 0


def test_the_entry_is_burned_into_the_pixels():
    first = synthetic(entry("CR", accession="ACC-1"))[0].pixel_array
    second = synthetic(entry("CR", accession="ACC-2"))[0].pixel_array
    assert not np.array_equal(first, second)


def test_ct_slices_share_a_frame_of_reference_and_step_through_the_patient():
    slices = synthetic(entry("CT"))
    assert len({ds.FrameOfReferenceUID for ds in slices}) == 1
    assert [float(ds.ImagePositionPatient[2]) for ds in slices] == [0.0, 5.0, 10.0]


def test_non_latin_names_render():
    ds = synthetic(entry("US", patient_name="Ñoño^Zoë 李 <b>"))[0]
    assert ds.pixel_array.max() > 0


def test_unsupported_modality_is_an_error():
    with pytest.raises(ValueError, match="MR"):
        synthetic(entry("MR"))
