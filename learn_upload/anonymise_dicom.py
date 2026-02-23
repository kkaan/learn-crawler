"""DICOM anonymisation for LEARN data transfer pipeline.

Replaces the manual MIM anonymisation step in the SOP.  Only TPS data
(CT_SET/, DICOM_PLAN/) needs anonymisation — XVI projection/CBCT files
are already anonymised by the system.
"""

import logging
from pathlib import Path

import pydicom
from pydicom.valuerep import PersonName

from learn_upload.config import DICOM_TAGS_REPLACE, DICOM_TAGS_CLEAR

logger = logging.getLogger(__name__)


class DicomAnonymiser:
    """Anonymise CT and plan DICOM files for a single patient."""

    def __init__(self, patient_dir: Path, anon_id: str, output_dir: Path) -> None:
        self.patient_dir = Path(patient_dir)
        self.anon_id = anon_id
        self.output_dir = Path(output_dir)

        if not self.patient_dir.is_dir():
            raise FileNotFoundError(
                f"Patient directory does not exist: {self.patient_dir}"
            )

    def anonymise_file(self, dcm_path: Path) -> Path:
        """Anonymise a single DICOM file and save to the staging directory.

        Tags in DICOM_TAGS_REPLACE are set to *anon_id*; tags in
        DICOM_TAGS_CLEAR are set to empty string (skipped if absent).
        All other tags — including UIDs — are left untouched.

        Returns the path of the written output file.
        """
        dcm = pydicom.dcmread(dcm_path)

        for tag in DICOM_TAGS_REPLACE:
            if tag == (0x0010, 0x0010):  # PatientName
                dcm[tag].value = PersonName(self.anon_id)
            else:
                dcm[tag].value = self.anon_id

        for tag in DICOM_TAGS_CLEAR:
            if tag in dcm:
                dcm[tag].value = ""

        # Mirror the subdirectory structure (CT_SET/ or DICOM_PLAN/)
        relative = dcm_path.relative_to(self.patient_dir)
        output_path = self.output_dir / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)

        dcm.save_as(output_path)
        logger.info("Anonymised %s -> %s", dcm_path.name, output_path)
        return output_path

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
