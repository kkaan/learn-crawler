"""DICOM anonymisation for LEARN data transfer pipeline.

Replaces the manual MIM anonymisation step in the SOP.  Only TPS data
(CT_SET/, DICOM_PLAN/) needs anonymisation — XVI projection/CBCT files
are already anonymised by the system.
"""

import logging
import re
from pathlib import Path

import pydicom
from pydicom.valuerep import PersonName

from learn_upload.config import DICOM_TAGS_REPLACE, DICOM_TAGS_CLEAR

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

        for tag in DICOM_TAGS_REPLACE:
            if tag == (0x0010, 0x0010):  # PatientName
                dcm[tag].value = PersonName(f"{self.anon_id}^{self.site_name}")
            else:
                dcm[tag].value = self.anon_id

        for tag in DICOM_TAGS_CLEAR:
            if tag in dcm:
                dcm[tag].value = ""

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
