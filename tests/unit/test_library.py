import logging

import numpy as np
from pydicom.uid import ExplicitVRLittleEndian

from factories import config, entry, write_library_file
from modality_simulator.images import SYNTHETIC, from_library, images_for


def first(candidates):
    return candidates[0]


def test_no_library_directory_means_generated_images(tmp_path):
    datasets, source = images_for(entry("CR"), config(image_dir=tmp_path / "missing"))
    assert source == SYNTHETIC
    assert len(datasets) == 1


def test_picks_a_file_of_the_entrys_modality(tmp_path):
    write_library_file(tmp_path / "a-cr.dcm", "CR")
    write_library_file(tmp_path / "nested" / "b-us.dcm", "US")
    datasets, source = images_for(entry("US"), config(image_dir=tmp_path), choose=first)
    assert source == "b-us.dcm"
    assert datasets[0].Modality == "US"


def test_no_file_of_the_modality_means_generated_images(tmp_path):
    write_library_file(tmp_path / "a-cr.dcm", "CR")
    _, source = images_for(entry("CT"), config(image_dir=tmp_path))
    assert source == SYNTHETIC


def test_a_compressed_file_comes_back_uncompressed(tmp_path):
    original = write_library_file(tmp_path / "rle.dcm", "CR", compressed=True)
    ds, _ = from_library(tmp_path, "CR", choose=first)
    assert ds.file_meta.TransferSyntaxUID == ExplicitVRLittleEndian
    assert np.array_equal(ds.pixel_array, original.pixel_array)


def test_non_dicom_and_non_image_files_are_ignored(tmp_path):
    (tmp_path / "README.txt").write_text("not DICOM")
    no_pixels = write_library_file(tmp_path / "no-pixels.dcm", "CR")
    del no_pixels.PixelData
    no_pixels.save_as(tmp_path / "no-pixels.dcm", enforce_file_format=True)
    assert from_library(tmp_path, "CR") is None


def test_undecodable_library_file_is_skipped(tmp_path, caplog):
    path = tmp_path / "truncated.dcm"
    write_library_file(path, "CR")
    path.write_bytes(path.read_bytes()[:-5000])
    with caplog.at_level(logging.WARNING):
        datasets, source = images_for(entry("CR"), config(image_dir=tmp_path))
    assert source == SYNTHETIC
    assert "truncated.dcm" in caplog.text


def test_a_bad_file_is_skipped_for_a_good_one(tmp_path):
    bad = tmp_path / "a-bad.dcm"
    write_library_file(bad, "CR")
    bad.write_bytes(bad.read_bytes()[:-5000])
    write_library_file(tmp_path / "b-good.dcm", "CR")
    _, source = images_for(entry("CR"), config(image_dir=tmp_path), choose=first)
    assert source == "b-good.dcm"
