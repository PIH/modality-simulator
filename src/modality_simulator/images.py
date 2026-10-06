"""Images to send: a real file from the library when there is one, otherwise a generated one."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pydicom
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from pynetdicom.sop_class import CTImageStorage, ComputedRadiographyImageStorage, UltrasoundImageStorage

from modality_simulator.config import Config
from modality_simulator.mwl import WorklistEntry

log = logging.getLogger(__name__)

Chooser = Callable[[Sequence[Path]], Path]

SOP_CLASSES = {
    "CR": ComputedRadiographyImageStorage,
    "US": UltrasoundImageStorage,
    "CT": CTImageStorage,
}
CT_SLICES = 3
CT_SLICE_MM = 5.0
SYNTHETIC = "synthetic"


def synthetic(entry: WorklistEntry) -> list[Dataset]:
    """Generated images for entry's modality, with its details drawn on them. Not yet stamped."""
    lines = [
        entry.patient_name or "(no name)",
        f"ID {entry.patient_id}",
        f"ACC {entry.accession_number}",
        entry.procedure,
        "SIMULATED IMAGE - NOT FOR DIAGNOSIS",
    ]
    if entry.modality == "CR":
        return [_cr(lines)]
    if entry.modality == "US":
        return [_us(lines)]
    if entry.modality == "CT":
        frame = generate_uid()
        return [_ct(lines + [f"SLICE {n} OF {CT_SLICES}"], n, frame) for n in range(1, CT_SLICES + 1)]
    raise ValueError(f"No generated image for modality {entry.modality!r}")


def images_for(entry: WorklistEntry, cfg: Config, choose: Chooser = random.choice) -> tuple[list[Dataset], str]:
    """The datasets to send for entry, unstamped, and where they came from: a file name, or "synthetic"."""
    found = from_library(cfg.image_dir, entry.modality, choose)
    if found is not None:
        ds, path = found
        return [ds], path.name
    return synthetic(entry), SYNTHETIC


def from_library(image_dir: Path, modality: str, choose: Chooser = random.choice) -> tuple[Dataset, Path] | None:
    """A library file of this modality, decoded and uncompressed, or None if there's no usable one."""
    candidates = _files_of_modality(image_dir, modality)
    while candidates:
        path = choose(candidates)
        candidates.remove(path)
        ds = _load_uncompressed(path)
        if ds is not None:
            return ds, path
    return None


def _files_of_modality(image_dir: Path, modality: str) -> list[Path]:
    if not image_dir.is_dir():
        return []
    matches = []
    for path in sorted(p for p in image_dir.rglob("*") if p.is_file()):
        try:
            header = pydicom.dcmread(path, stop_before_pixels=True)
        except Exception as e:  # anything that isn't readable DICOM: READMEs, partial copies
            log.debug("Not a DICOM file, skipping %s: %s", path, e)
            continue
        if "SOPClassUID" in header and str(header.get("Modality", "")).upper() == modality:
            matches.append(path)
    return matches


def _load_uncompressed(path: Path) -> Dataset | None:
    try:
        ds = pydicom.dcmread(path)
        if "PixelData" not in ds:
            log.warning("Skipping library file %s: it has no pixel data", path)
            return None
        if ds.file_meta.TransferSyntaxUID.is_compressed:
            ds.decompress()
        ds.pixel_array  # decodes the pixels, so a truncated file is caught here and not at the gateway
        return ds
    except Exception as e:
        log.warning("Skipping library file %s: %s", path, e)
        return None


def _render(lines: list[str], width: int, height: int) -> np.ndarray:
    """8-bit greyscale: a body-like ellipse with the lines of text over it."""
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.2, height * 0.15, width * 0.8, height * 0.85), fill=70)
    size = max(14, height // 24)
    font = ImageFont.load_default(size=size)
    for row, line in enumerate(lines):
        draw.text((20, 20 + row * (size + 8)), line, fill=255, font=font)
    return np.asarray(image, dtype=np.uint8)


def _dataset(modality: str, pixels: np.ndarray, bits_stored: int) -> Dataset:
    sop_class = SOP_CLASSES[modality]
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = sop_class
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SOPClassUID = sop_class
    ds.Modality = modality
    ds.ImageType = ["ORIGINAL", "PRIMARY"]
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows, ds.Columns = pixels.shape
    ds.BitsAllocated = pixels.dtype.itemsize * 8
    ds.BitsStored = bits_stored
    ds.HighBit = bits_stored - 1
    ds.PixelRepresentation = 0
    ds.PixelData = pixels.tobytes()
    return ds


def _twelve_bit(pixels: np.ndarray) -> np.ndarray:
    return pixels.astype(np.uint16) * 16


def _cr(lines: list[str]) -> Dataset:
    ds = _dataset("CR", _twelve_bit(_render(lines, 1024, 1024)), 12)
    ds.BodyPartExamined = ""
    ds.ViewPosition = ""
    return ds


def _us(lines: list[str]) -> Dataset:
    return _dataset("US", _render(lines, 640, 480), 8)


def _ct(lines: list[str], number: int, frame_of_reference: str) -> Dataset:
    ds = _dataset("CT", _twelve_bit(_render(lines, 512, 512)), 12)
    ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
    ds.FrameOfReferenceUID = frame_of_reference
    ds.KVP = "120"
    ds.AcquisitionNumber = 1
    ds.SliceThickness = str(CT_SLICE_MM)
    ds.PixelSpacing = [0.7, 0.7]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [-179.2, -179.2, CT_SLICE_MM * (number - 1)]
    ds.RescaleIntercept = "-1024"
    ds.RescaleSlope = "1"
    return ds
