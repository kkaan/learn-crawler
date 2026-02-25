"""
Shared parsing utilities for Elekta XVI data files.

Functions here are generalised from patterns in the existing standalone scripts:
- elektafdt_crawler.py          (XML parsing)
- extract_elekta_rps_matrices.py (ZIP-embedded INI parsing from RPS DICOM)

They are designed to be reused across anonymise_dicom, folder_sort,
treatment_notes, and upload_workflow modules.
"""

import io
import logging
import os
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

from learn_upload.config import RPS_ZIP_TAG

logger = logging.getLogger(__name__)

_WINDOWS_LONG_PATH_PREFIX = "\\\\?\\"


def normalize_windows_path(path: Path) -> str:
    """Return a Windows-safe path string, adding long-path prefixes if needed."""
    path_str = str(path)
    if os.name != "nt":
        return path_str
    if path_str.startswith(_WINDOWS_LONG_PATH_PREFIX):
        return path_str
    if not Path(path_str).is_absolute():
        return path_str
    if len(path_str) < 240:
        return path_str
    if path_str.startswith("\\\\"):
        return f"\\\\?\\UNC\\{path_str.lstrip('\\\\')}"
    return f"{_WINDOWS_LONG_PATH_PREFIX}{path_str}"


def safe_copy2(src: Path, dst: Path) -> bool:
    """Copy a file with logging, returning True on success."""
    try:
        shutil.copy2(normalize_windows_path(src), normalize_windows_path(dst))
    except (OSError, PermissionError) as exc:
        logger.error("Failed to copy %s -> %s: %s", src, dst, exc)
        return False
    return True

# ---------------------------------------------------------------------------
# Plain INI parsing  (Reconstruction/*.INI files)
# ---------------------------------------------------------------------------

# Fields we extract from XVI plain INI files.  The regex approach (not
# configparser) is inherited from extract_elekta_rps_matrices.py because XVI
# INI files use non-standard formatting that configparser chokes on.
_INI_FIELDS = [
    "PatientID",
    "TreatmentID",
    "TreatmentUID",
    "ReferenceUID",
    "FirstName",
    "LastName",
    "ScanUID",
    "TubeKV",
    "TubeMA",
    "CollimatorName",
]


def parse_xvi_ini(ini_text: str) -> dict:
    """Parse an Elekta XVI INI file and return extracted fields.

    Handles both ``[IDENTIFICATION]``-section fields from ``.INI`` files and
    reconstruction parameters (TubeKV, TubeMA, ScanUID, CollimatorName) from
    ``.INI.XVI`` files — the same regex works on either since the key=value
    format is identical.

    Parameters
    ----------
    ini_text : str
        Raw text content of the INI file.

    Returns
    -------
    dict
        Mapping of field name -> string value for every field found.
        Missing fields are omitted (not set to None).
    """
    result = {}
    for field in _INI_FIELDS:
        match = re.search(rf"^{field}=(.+)$", ini_text, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            result[field] = value
    return result


# ---------------------------------------------------------------------------
# ScanUID datetime parsing
# ---------------------------------------------------------------------------

# ScanUID format example:
#   1.3.46.423632.33783920233217242713.224.2023-03-21165402768
# The datetime is embedded at the end: YYYY-MM-DDHHMMSSmmm
_SCAN_DATETIME_PATTERN = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(\d{2})(\d{2})(\d{2})(\d{3})$"
)


def parse_scan_datetime(scan_uid: str) -> Optional[datetime]:
    """Extract the embedded datetime from an Elekta ScanUID string.

    Parameters
    ----------
    scan_uid : str
        Full ScanUID value, e.g.
        ``"1.3.46.423632.33783920233217242713.224.2023-03-21165402768"``

    Returns
    -------
    datetime or None
        Parsed datetime, or None if the pattern is not found.
    """
    match = _SCAN_DATETIME_PATTERN.search(scan_uid)
    if not match:
        logger.warning("Could not parse datetime from ScanUID: %s", scan_uid)
        return None

    year, month, day, hour, minute, second, ms = (int(g) for g in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, ms * 1000)
    except ValueError as exc:
        logger.warning("Invalid datetime values in ScanUID %s: %s", scan_uid, exc)
        return None


# ---------------------------------------------------------------------------
# _Frames.xml parsing
# ---------------------------------------------------------------------------

def parse_frames_xml(xml_path: Path) -> dict:
    """Parse a ``_Frames.xml`` file and return treatment + acquisition metadata.

    Refactored from ``elektafdt_crawler.py:get_plan_name_from_xml()``.

    Parameters
    ----------
    xml_path : Path
        Path to the ``_Frames.xml`` file.

    Returns
    -------
    dict
        Keys:
        - ``treatment_id`` (str or None) — ``<Treatment><ID>``
        - ``acquisition_preset`` (str or None) — ``<Image><AcquisitionPresetName>``
        - ``dicom_uid`` (str or None) — ``<Image><DicomUID>``
        - ``kv`` (float or None) — ``<Image><kV>``
        - ``ma`` (float or None) — ``<Image><mA>``
    """
    result: dict = {
        "treatment_id": None,
        "acquisition_preset": None,
        "dicom_uid": None,
        "kv": None,
        "ma": None,
    }
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Treatment ID
        treatment_el = root.find("Treatment")
        if treatment_el is not None:
            id_el = treatment_el.find("ID")
            if id_el is not None and id_el.text:
                result["treatment_id"] = id_el.text.strip()
                logger.info("Found treatment_id '%s' in %s", result["treatment_id"], xml_path)
        else:
            logger.warning("No Treatment/ID found in %s", xml_path)

        # Image acquisition metadata
        image_el = root.find("Image")
        if image_el is not None:
            preset_el = image_el.find("AcquisitionPresetName")
            if preset_el is not None and preset_el.text:
                result["acquisition_preset"] = preset_el.text.strip()

            uid_el = image_el.find("DicomUID")
            if uid_el is not None and uid_el.text:
                result["dicom_uid"] = uid_el.text.strip()

            kv_el = image_el.find("kV")
            if kv_el is not None and kv_el.text:
                try:
                    result["kv"] = float(kv_el.text.strip())
                except ValueError:
                    logger.warning("Non-numeric kV value in %s: %s", xml_path, kv_el.text)

            ma_el = image_el.find("mA")
            if ma_el is not None and ma_el.text:
                try:
                    result["ma"] = float(ma_el.text.strip())
                except ValueError:
                    logger.warning("Non-numeric mA value in %s: %s", xml_path, ma_el.text)

    except ET.ParseError as exc:
        logger.error("XML parse error in %s: %s", xml_path, exc)
    except OSError as exc:
        logger.error("Could not read %s: %s", xml_path, exc)

    return result


# ---------------------------------------------------------------------------
# Couch shift extraction from INI text
# ---------------------------------------------------------------------------

def parse_couch_shifts(ini_text: str) -> Optional[dict]:
    """Extract CouchShiftLat/Long/Height from XVI INI text.

    Parameters
    ----------
    ini_text : str
        Raw INI text content (from ``.INI.XVI`` or plain INI file).

    Returns
    -------
    dict or None
        ``{"lateral": float, "longitudinal": float, "vertical": float}``
        if all three shift keys are found, otherwise None.
    """
    couch_lat = re.search(r"CouchShiftLat=(.+)", ini_text)
    couch_long = re.search(r"CouchShiftLong=(.+)", ini_text)
    couch_height = re.search(r"CouchShiftHeight=(.+)", ini_text)

    if couch_lat and couch_long and couch_height:
        try:
            return {
                "lateral": float(couch_lat.group(1).strip()),
                "longitudinal": float(couch_long.group(1).strip()),
                "vertical": float(couch_height.group(1).strip()),
            }
        except ValueError as exc:
            logger.warning("Non-numeric couch shift value: %s", exc)
            return None

    return None


# ---------------------------------------------------------------------------
# ZIP-embedded INI extraction from RPS DICOM
# ---------------------------------------------------------------------------

def extract_ini_from_rps(dcm_path: Path) -> Optional[str]:
    """Read an Elekta RPS DICOM file and return the embedded INI text.

    The RPS DICOM stores a ZIP archive in private tag ``(0021,103A)``.
    Inside the ZIP is a ``.INI.XVI`` file with registration data.

    Refactored from ``extract_elekta_rps_matrices.py:extract_zip()``.

    Parameters
    ----------
    dcm_path : Path
        Path to the ``.RPS.dcm`` file.

    Returns
    -------
    str or None
        Raw INI text content, or None on failure.
    """
    try:
        import pydicom
    except ImportError:
        logger.error("pydicom is required for RPS extraction but not installed")
        return None

    try:
        dcm = pydicom.dcmread(normalize_windows_path(dcm_path))
    except Exception as exc:
        logger.error("Failed to read DICOM %s: %s", dcm_path, exc)
        return None

    if RPS_ZIP_TAG not in dcm:
        logger.error("ZIP data tag %s not found in %s", RPS_ZIP_TAG, dcm_path)
        return None

    zip_data = dcm[RPS_ZIP_TAG].value
    try:
        zip_buffer = io.BytesIO(zip_data)
        with zipfile.ZipFile(zip_buffer, "r") as zf:
            ini_files = [f for f in zf.namelist() if f.endswith(".INI.XVI")]
            if not ini_files:
                logger.error("No .INI.XVI file in ZIP from %s", dcm_path)
                return None
            return zf.read(ini_files[0]).decode("utf-8", errors="ignore")
    except zipfile.BadZipFile:
        logger.error("Invalid ZIP data in %s", dcm_path)
        return None
