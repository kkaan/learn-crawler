"""DICOM anonymisation for LEARN data transfer pipeline.

Replaces the manual MIM anonymisation step in the SOP.  Handles all file
types that may contain patient-identifiable information: DICOM files,
_Frames.xml, INI configuration files, centroid files, and trajectory logs.
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pydicom
from pydicom.valuerep import PersonName

from learn_upload.config import DICOM_TAGS_CLEAR, DICOM_TAGS_REPLACE

logger = logging.getLogger(__name__)


class DicomAnonymiser:
    """Anonymise CT and plan DICOM files for a single patient."""

    def __init__(
        self, patient_dir: Path, anon_id: str, output_dir: Path, site_name: str = ""
    ) -> None:
        self.patient_dir = Path(patient_dir)
        self.anon_id = anon_id
        self.output_dir = Path(output_dir)
        self.site_name = site_name

        if not self.patient_dir.is_dir():
            raise FileNotFoundError(
                f"Patient directory does not exist: {self.patient_dir}"
            )

    def _anonymise_filename(self, filename: str) -> str:
        """Replace parenthesised patient name in filename with anon_id.

        E.g. ``DCMRT_Plan(SMITH JOHN).dcm`` → ``DCMRT_Plan(PAT01).dcm``
        """
        return re.sub(r"\([^)]+\)", f"({self.anon_id})", filename)

    def anonymise_file(self, dcm_path: Path, source_base: Path = None) -> Path:
        """Anonymise a single DICOM file and save to the staging directory.

        Tags in DICOM_TAGS_REPLACE are set to *anon_id*; tags in
        DICOM_TAGS_CLEAR are set to empty string (skipped if absent).
        All other tags — including UIDs — are left untouched.

        Parameters
        ----------
        dcm_path : Path
            Path to the DICOM file to anonymise.
        source_base : Path, optional
            Base directory for computing relative output path.  Defaults to
            ``self.patient_dir`` (original behaviour).

        Returns the path of the written output file.
        """
        dcm = pydicom.dcmread(dcm_path)

        # Capture original PatientID before replacing — used for scrubbing
        original_patient_id = str(getattr(dcm, "PatientID", ""))

        for tag in DICOM_TAGS_REPLACE:
            if tag == (0x0010, 0x0010):  # PatientName
                dcm[tag].value = PersonName(f"{self.anon_id}^{self.site_name}")
            else:
                dcm[tag].value = self.anon_id

        for tag in DICOM_TAGS_CLEAR:
            if tag in dcm:
                dcm[tag].value = ""

        # Scrub original patient ID from StudyDescription (XVI RPS files
        # embed MRN in text like "Tx Plan for 12345678 on ...")
        if original_patient_id and hasattr(dcm, "StudyDescription"):
            dcm.StudyDescription = dcm.StudyDescription.replace(
                original_patient_id, self.anon_id
            )

        # Mirror the subdirectory structure relative to source_base
        base = Path(source_base) if source_base is not None else self.patient_dir
        relative = dcm_path.relative_to(base)
        # Anonymise the filename (replace parenthesised patient name)
        anon_name = self._anonymise_filename(relative.name)
        output_path = self.output_dir / relative.parent / anon_name
        output_path.parent.mkdir(parents=True, exist_ok=True)

        dcm.save_as(output_path)
        logger.info("Anonymised %s -> %s", dcm_path.name, output_path)
        return output_path

    def anonymise_all_dcm(self, source_dir: Path) -> list[Path]:
        """Recursively find and anonymise every ``.dcm`` file under *source_dir*.

        Unlike :meth:`anonymise_ct_set` / :meth:`anonymise_plan`, this method
        does not assume any particular subdirectory layout — it simply walks the
        tree and anonymises every DICOM file it finds.

        Returns a list of output file paths.
        """
        source_dir = Path(source_dir)
        if not source_dir.is_dir():
            logger.warning("Source directory does not exist: %s", source_dir)
            return []

        files = sorted(source_dir.rglob("*.dcm"), key=str) + sorted(
            source_dir.rglob("*.DCM"), key=str
        )
        # Deduplicate (on case-insensitive filesystems *.dcm and *.DCM overlap)
        seen: set[Path] = set()
        unique: list[Path] = []
        for f in files:
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                unique.append(f)

        results = [self.anonymise_file(f, source_base=source_dir) for f in unique]
        logger.info(
            "anonymise_all_dcm: %d files anonymised under %s", len(results), source_dir
        )
        return results

    def _glob_dcm(self, subdir: str) -> list[Path]:
        """Return all .dcm/.DCM files under patient_dir/subdir."""
        folder = self.patient_dir / subdir
        if not folder.is_dir():
            logger.warning("Directory not found: %s", folder)
            return []
        files = sorted(
            p for p in folder.iterdir()
            if p.suffix.lower() == ".dcm"
        )
        if not files:
            logger.warning("No DCM files in %s", folder)
        return files

    def anonymise_ct_set(self) -> list[Path]:
        """Anonymise all DICOM files in CT_SET/."""
        return [self.anonymise_file(f) for f in self._glob_dcm("CT_SET")]

    def anonymise_plan(self) -> list[Path]:
        """Anonymise all DICOM files in DICOM_PLAN/."""
        return [self.anonymise_file(f) for f in self._glob_dcm("DICOM_PLAN")]

    def anonymise_frames_xml(self, xml_path: Path, output_path: Path) -> Path:
        """Anonymise a ``_Frames.xml`` file, removing patient PII.

        Replaces ``<Patient><FirstName>``, ``<LastName>``, and ``<ID>`` with
        *anon_id*.  Also regex-scrubs the original patient ID from
        ``<Treatment><Description>`` text (if present).

        Parameters
        ----------
        xml_path : Path
            Path to the source ``_Frames.xml``.
        output_path : Path
            Destination path for the anonymised XML.

        Returns
        -------
        Path
            The written output file path.
        """
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Detect original patient ID before replacing it
        patient_el = root.find("Patient")
        original_id = None
        if patient_el is not None:
            id_el = patient_el.find("ID")
            if id_el is not None and id_el.text:
                original_id = id_el.text.strip()

            # Replace PII tags
            for tag_name in ("FirstName", "LastName", "ID"):
                el = patient_el.find(tag_name)
                if el is not None:
                    if tag_name == "FirstName":
                        el.text = ""
                    else:
                        el.text = self.anon_id

        # Scrub original patient ID from Treatment/Description
        if original_id:
            treatment_el = root.find("Treatment")
            if treatment_el is not None:
                desc_el = treatment_el.find("Description")
                if desc_el is not None and desc_el.text:
                    desc_el.text = desc_el.text.replace(original_id, self.anon_id)

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tree.write(output_path, encoding="unicode", xml_declaration=True)
        logger.info("Anonymised _Frames.xml %s -> %s", xml_path, output_path)
        return output_path

    def anonymise_all(self) -> dict:
        """Anonymise CT_SET and DICOM_PLAN, returning a summary dict."""
        ct_files = self.anonymise_ct_set()
        plan_files = self.anonymise_plan()
        summary = {
            "ct_count": len(ct_files),
            "plan_count": len(plan_files),
            "anon_id": self.anon_id,
        }
        logger.info(
            "Anonymisation complete: %d CT, %d plan files (ID: %s)",
            summary["ct_count"],
            summary["plan_count"],
            summary["anon_id"],
        )
        return summary


# ---------------------------------------------------------------------------
# Standalone anonymisation helpers (operate on already-copied output files)
# ---------------------------------------------------------------------------


def anonymise_ini_file(ini_path: Path, anon_id: str) -> None:
    """Anonymise an XVI ``.INI`` or ``.INI.XVI`` file in-place.

    Replaces ``PatientID=xxx`` with ``PatientID={anon_id}``,
    ``FirstName=xxx`` with ``FirstName=``, and
    ``LastName=xxx`` with ``LastName={anon_id}``.
    """
    ini_path = Path(ini_path)
    text = ini_path.read_text(encoding="utf-8", errors="replace")

    text = re.sub(r"(?m)^PatientID=.*$", f"PatientID={anon_id}", text)
    text = re.sub(r"(?m)^FirstName=.*$", "FirstName=", text)
    text = re.sub(r"(?m)^LastName=.*$", f"LastName={anon_id}", text)

    ini_path.write_text(text, encoding="utf-8")
    logger.info("Anonymised INI %s", ini_path)


def anonymise_centroid_file(file_path: Path, anon_id: str) -> Path:
    """Anonymise a centroid file in-place.

    Lines 1 and 2 contain MRN and patient name; both are replaced with
    *anon_id*.  The MRN portion of the filename is also replaced.

    Returns the (possibly renamed) output path.
    """
    file_path = Path(file_path)
    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines(
        keepends=True
    )

    original_id = lines[0].strip() if lines else ""

    if len(lines) >= 1:
        lines[0] = anon_id + "\n"
    if len(lines) >= 2:
        lines[1] = anon_id + "\n"

    file_path.write_text("".join(lines), encoding="utf-8")

    # Rename file if MRN appears in the filename
    if original_id and original_id in file_path.name:
        new_name = file_path.name.replace(original_id, anon_id)
        new_path = file_path.parent / new_name
        file_path.rename(new_path)
        logger.info("Anonymised centroid -> %s", new_path.name)
        return new_path

    logger.info("Anonymised centroid %s", file_path)
    return file_path


def anonymise_trajectory_log(file_path: Path, original_id: str, anon_id: str) -> None:
    """Anonymise a trajectory log file in-place.

    Replaces ``patient_{original_id}`` with ``patient_{anon_id}`` in
    the file text (used for MarkerLocations*.txt files).
    """
    file_path = Path(file_path)
    text = file_path.read_text(encoding="utf-8", errors="ignore")

    if original_id:
        text = text.replace(f"patient_{original_id}", f"patient_{anon_id}")

    file_path.write_text(text, encoding="utf-8")
    logger.info("Anonymised trajectory log %s", file_path)


def anonymise_output_folder(
    output_dir: Path,
    anon_id: str,
    site_name: str,
    patient_dir: Path,
    tps_path: Path = None,
    progress_callback=None,
) -> dict:
    """Scan an entire output folder and anonymise all files in-place.

    This is the main entry point for the anonymisation step, called after
    folder sort has copied raw files into the LEARN directory structure.

    Parameters
    ----------
    output_dir : Path
        Base output directory (contains *site_name*/ subtree).
    anon_id : str
        Anonymised patient identifier (e.g. ``PAT01``).
    site_name : str
        Site/treatment name (e.g. ``Prostate``).
    patient_dir : Path
        Original XVI patient directory (for extracting original MRN).
    tps_path : Path, optional
        TPS export directory.  If provided, DICOM files are imported and
        anonymised into Patient Plans/.
    progress_callback : callable, optional
        ``callback(current, total, filename)`` for progress reporting.

    Returns
    -------
    dict
        Summary: ``{"dcm": N, "xml": M, "ini": P, "centroid": Q,
        "trajectory": R, "tps_imported": S, "errors": E}``.
    """
    output_dir = Path(output_dir)
    patient_dir = Path(patient_dir)
    site_root = output_dir / Path(site_name).name

    # Detect original patient ID from source directory name
    patient_dir_name = patient_dir.name
    original_id = ""
    if patient_dir_name.lower().startswith("patient_"):
        original_id = patient_dir_name[len("patient_"):]

    counts = {
        "dcm": 0, "xml": 0, "ini": 0, "centroid": 0,
        "trajectory": 0, "tps_imported": 0, "errors": 0,
    }

    # --- Phase 1: Collect all files to process ---
    files_to_process: list[Path] = []
    if site_root.is_dir():
        for f in site_root.rglob("*"):
            if f.is_file():
                files_to_process.append(f)

    total = len(files_to_process)
    processed = 0
    last_emit = 0.0

    def _progress(filename: str) -> None:
        nonlocal processed, last_emit
        processed += 1
        if progress_callback:
            now = time.monotonic()
            if now - last_emit >= 0.2 or processed % 10 == 0 or processed == total:
                progress_callback(processed, total, filename)
                last_emit = now

    # --- Phase 2: Walk and anonymise ---
    # Build a DicomAnonymiser that writes in-place (output_dir == source)
    anon = DicomAnonymiser(
        patient_dir=patient_dir,
        anon_id=anon_id,
        output_dir=site_root,
        site_name=site_name,
    )

    for file_path in sorted(files_to_process):
        name_lower = file_path.name.lower()

        try:
            if name_lower == "_frames.xml":
                anon.anonymise_frames_xml(file_path, file_path)
                counts["xml"] += 1

            elif name_lower.endswith((".ini", ".ini.xvi")):
                anonymise_ini_file(file_path, anon_id)
                counts["ini"] += 1

            elif name_lower.endswith(".dcm"):
                # In-place: read, anonymise, overwrite
                dcm = pydicom.dcmread(file_path)
                original_patient_id = str(getattr(dcm, "PatientID", ""))

                for tag in DICOM_TAGS_REPLACE:
                    if tag == (0x0010, 0x0010):  # PatientName
                        dcm[tag].value = PersonName(f"{anon_id}^{site_name}")
                    else:
                        dcm[tag].value = anon_id

                for tag in DICOM_TAGS_CLEAR:
                    if tag in dcm:
                        dcm[tag].value = ""

                if original_patient_id and hasattr(dcm, "StudyDescription"):
                    dcm.StudyDescription = dcm.StudyDescription.replace(
                        original_patient_id, anon_id
                    )

                dcm.save_as(file_path)
                counts["dcm"] += 1

            elif name_lower.startswith("markerlocations") and name_lower.endswith(".txt"):
                anonymise_trajectory_log(file_path, original_id, anon_id)
                counts["trajectory"] += 1

            elif "patient files" in str(file_path.parent).lower() and name_lower.endswith(".txt"):
                # Centroid files live in Patient Files/{anon_id}/
                anonymise_centroid_file(file_path, anon_id)
                counts["centroid"] += 1

            # .his and .SCAN — skip (binary data)

        except Exception:
            logger.exception("Failed to anonymise %s", file_path)
            counts["errors"] += 1

        _progress(file_path.name)

    # --- Phase 3: TPS import (if provided) ---
    if tps_path:
        tps_path = Path(tps_path)
        if tps_path.is_dir():
            plans_root = site_root / "Patient Plans" / anon_id
            tps_categories = {
                "DICOM CT Images": "CT",
                "DICOM RT Plan": "Plan",
                "DICOM RT Structures": "Structure Set",
                "DICOM RT Dose": "Dose",
            }

            # Count TPS files for progress update
            tps_files: list[tuple[Path, str]] = []
            for src_name, dest_name in tps_categories.items():
                src_dir = tps_path / src_name
                if src_dir.is_dir():
                    for dcm_file in sorted(src_dir.rglob("*.dcm")):
                        tps_files.append((dcm_file, dest_name))

            total += len(tps_files)

            for dcm_file, dest_name in tps_files:
                dest_dir = plans_root / dest_name
                dest_dir.mkdir(parents=True, exist_ok=True)
                try:
                    tps_anon = DicomAnonymiser(
                        patient_dir=patient_dir,
                        anon_id=anon_id,
                        output_dir=dest_dir,
                        site_name=site_name,
                    )
                    tps_anon.anonymise_file(dcm_file, source_base=dcm_file.parent)
                    counts["tps_imported"] += 1
                except Exception:
                    logger.exception("Failed to anonymise TPS file %s", dcm_file)
                    counts["errors"] += 1
                _progress(dcm_file.name)

    logger.info("anonymise_output_folder complete: %s", counts)
    return counts
