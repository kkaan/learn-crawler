"""LEARN trial data inventory crawler.

Walks an XVI processed root (e.g. ``E:\\XVI_COLLECTION\\processed``), filters
to machines with calibration FlexMap files, and emits one CSV row per
``IMAGES/img_<UID>`` folder capturing treatment plan, FOV, planned fractions,
and acquisition timestamp.

Usage:
    python scripts/inventory_crawler.py [--root PATH] [--output PATH] [--log-level LEVEL]
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

# Ensure repo root is on sys.path so the learn_upload package is importable
# even when this file is invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from learn_upload.utils import (  # noqa: E402  (import after sys.path tweak)
    extract_planned_fractions,
    parse_frames_xml,
    parse_scan_datetime,
    parse_xvi_ini,
)

logger = logging.getLogger(__name__)

# Calibration files live at:
#   <machine>\Current Calibration Files\Current\FlexMap\*.flexmap
FLEXMAP_SUBPATH = Path("Current Calibration Files") / "Current" / "FlexMap"


def find_machines_with_flexmaps(processed_root: Path) -> list[Path]:
    """Return machine directories under ``processed_root`` containing flexmap files.

    A machine qualifies if at least one ``*.flexmap`` file exists under
    ``<machine>/Current Calibration Files/Current/FlexMap/``.

    Parameters
    ----------
    processed_root : Path
        Root directory containing ``<Date_Center_Machine>`` subdirectories.

    Returns
    -------
    list[Path]
        Sorted list of machine directories with at least one flexmap.
    """
    if not processed_root.is_dir():
        logger.error("Processed root does not exist or is not a directory: %s", processed_root)
        return []

    machines: list[Path] = []
    for entry in sorted(processed_root.iterdir()):
        if not entry.is_dir():
            continue
        flexmap_dir = entry / FLEXMAP_SUBPATH
        if not flexmap_dir.is_dir():
            logger.info("No flexmap dir under %s — skipping machine", entry.name)
            continue
        flexmaps = list(flexmap_dir.glob("*.flexmap"))
        if not flexmaps:
            logger.info("Flexmap dir present but empty under %s — skipping", entry.name)
            continue
        logger.info("Machine %s has %d flexmap file(s)", entry.name, len(flexmaps))
        machines.append(entry)
    return machines


def find_patient_folders(machine_dir: Path) -> list[Path]:
    """Return ``patient_*`` subdirectories of a machine directory, sorted.

    Mirrors the discovery convention used by ``scripts/elektafdt_crawler.py:69-94``.
    """
    if not machine_dir.is_dir():
        logger.warning("Machine directory not found: %s", machine_dir)
        return []
    return sorted(
        p for p in machine_dir.iterdir()
        if p.is_dir() and p.name.startswith("patient_")
    )


@dataclass
class ImgRecord:
    """One inventory row corresponding to a single ``IMAGES/img_<UID>/`` folder."""
    machine: str
    patient_folder: str
    img_uid: str
    treatment_id: Optional[str]
    fov: Optional[str]
    scan_datetime: Optional[datetime]
    planned_fractions: Optional[int]  # populated later in trawl_machine
    img_dir: Path


def iter_img_records(patient_dir: Path) -> Iterator[ImgRecord]:
    """Yield one ``ImgRecord`` per ``IMAGES/img_<UID>/`` directory.

    Per-img extraction:
      * ``treatment_id`` from ``_Frames.xml`` ``<Treatment><ID>``
        (via ``learn_upload.utils.parse_frames_xml``).
      * ``fov`` from ``Reconstruction/<UID>.INI.XVI`` ``FOV=`` line
        (via ``learn_upload.utils.parse_xvi_ini``).
      * ``scan_datetime`` parsed from ``ScanUID`` if present.

    ``planned_fractions`` is left as ``None`` here; ``trawl_machine`` fills it
    once per patient (it is plan-level data, not per-image).
    """
    images_dir = patient_dir / "IMAGES"
    if not images_dir.is_dir():
        logger.info("No IMAGES dir in %s", patient_dir)
        return

    for img_dir in sorted(images_dir.iterdir()):
        if not img_dir.is_dir() or not img_dir.name.startswith("img_"):
            continue
        img_uid = img_dir.name[len("img_"):]

        treatment_id: Optional[str] = None
        frames_xml = img_dir / "_Frames.xml"
        if frames_xml.exists():
            meta = parse_frames_xml(frames_xml)
            treatment_id = meta.get("treatment_id")
        else:
            logger.warning("No _Frames.xml in %s", img_dir)

        fov: Optional[str] = None
        scan_dt: Optional[datetime] = None
        recon_dir = img_dir / "Reconstruction"
        if recon_dir.is_dir():
            ini_xvi_files = sorted(recon_dir.glob("*.INI.XVI"))
            if ini_xvi_files:
                ini_text = ini_xvi_files[0].read_text(
                    encoding="utf-8", errors="ignore",
                )
                ini_data = parse_xvi_ini(ini_text)
                fov = ini_data.get("FOV")
                scan_uid = ini_data.get("ScanUID")
                if scan_uid:
                    scan_dt = parse_scan_datetime(scan_uid)
            else:
                logger.info("No *.INI.XVI in %s", recon_dir)

        yield ImgRecord(
            machine=patient_dir.parent.name,
            patient_folder=patient_dir.name,
            img_uid=img_uid,
            treatment_id=treatment_id,
            fov=fov,
            scan_datetime=scan_dt,
            planned_fractions=None,
            img_dir=img_dir,
        )


def _is_rtplan(dcm_path: Path) -> bool:
    """Cheaply check whether a .dcm file has Modality == 'RTPLAN'.

    Uses ``pydicom.dcmread(..., specific_tags=["Modality"])`` so we only read
    the Modality tag — important when sweeping hundreds of DICOM files.
    """
    try:
        import pydicom
        ds = pydicom.dcmread(
            str(dcm_path), stop_before_pixels=True, specific_tags=["Modality"],
        )
        return getattr(ds, "Modality", "").upper() == "RTPLAN"
    except Exception:
        return False


def find_planned_fractions_for_patient(patient_dir: Path) -> Optional[int]:
    """Search a patient directory for an RTPLAN DICOM and return its fraction count.

    Order of search:
      1. ``patient_dir/DICOM_PLAN/**/*.dcm`` — preferred location.
      2. Recursive ``patient_dir/**/*.dcm`` fallback (if no RTPLAN was found
         in DICOM_PLAN, e.g. only RTSTRUCT was present there).

    Files are filtered by DICOM ``Modality == "RTPLAN"`` (per the convention
    in ``learn_upload/folder_sort.py`` ``_MODALITY_MAP``). Returns the first
    fraction count found, or ``None`` if no usable RTPLAN exists.
    """
    if not patient_dir.is_dir():
        return None

    # Preferred: DICOM_PLAN/
    plan_dir = patient_dir / "DICOM_PLAN"
    rtplans_in_plan_dir: list[Path] = []
    if plan_dir.is_dir():
        rtplans_in_plan_dir = [p for p in sorted(plan_dir.rglob("*.dcm")) if _is_rtplan(p)]

    candidates = rtplans_in_plan_dir
    if not candidates:
        # Fallback: anywhere under patient_dir
        candidates = [p for p in sorted(patient_dir.rglob("*.dcm")) if _is_rtplan(p)]

    for dcm in candidates:
        n = extract_planned_fractions(dcm)
        if n is not None:
            return n

    logger.info("No RTPLAN with NumberOfFractionsPlanned for %s", patient_dir.name)
    return None
