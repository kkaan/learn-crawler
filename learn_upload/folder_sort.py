"""Folder mapping and file sorting for the LEARN data transfer pipeline.

Automates the manual SOP steps of:
1. Discovering XVI acquisition sessions from patient IMAGES/ directories
2. Classifying sessions as CBCT, KIM Learning, or KIM MotionView
3. Assigning sessions to treatment fractions (FX0, FX1, ...)
4. Creating the LEARN hierarchical directory structure
5. Copying files to their correct destinations
"""

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from learn_upload.utils import (
    extract_ini_from_rps,
    parse_couch_shifts,
    parse_frames_xml,
    parse_scan_datetime,
    parse_xvi_ini,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session dataclass
# ---------------------------------------------------------------------------

@dataclass
class CBCTSession:
    """Represents a single XVI acquisition session (img_* directory)."""

    img_dir: Path
    dicom_uid: str
    acquisition_preset: str
    session_type: str  # "cbct" | "kim_learning" | "motionview"
    treatment_id: str
    scan_datetime: Optional[datetime] = None
    tube_kv: Optional[float] = None
    tube_ma: Optional[float] = None
    has_rps: bool = False
    rps_path: Optional[Path] = None
    couch_shifts: Optional[dict] = None
    ini_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_acquisition(preset_name: str) -> str:
    """Classify an acquisition preset into session type.

    Parameters
    ----------
    preset_name : str
        AcquisitionPresetName from _Frames.xml.

    Returns
    -------
    str
        ``"motionview"``, ``"kim_learning"``, or ``"cbct"``.
    """
    lower = preset_name.lower()
    if "motionview" in lower:
        return "motionview"
    if "kim" in lower:
        return "kim_learning"
    return "cbct"


# ---------------------------------------------------------------------------
# Folder Mapper
# ---------------------------------------------------------------------------

class LearnFolderMapper:
    """Discovers XVI sessions and maps them to the LEARN directory structure."""

    def __init__(
        self,
        patient_dir: Path,
        anon_id: str,
        site_name: str,
        output_base: Path,
    ) -> None:
        self.patient_dir = Path(patient_dir)
        self.anon_id = anon_id
        self.site_name = site_name
        self.output_base = Path(output_base)

    # ----- Discovery -----

    def discover_sessions(self) -> list[CBCTSession]:
        """Scan IMAGES/img_* directories and build session objects.

        Returns
        -------
        list[CBCTSession]
            Sessions sorted by scan_datetime (None-datetime sessions at end).
        """
        images_dir = self.patient_dir / "IMAGES"
        if not images_dir.is_dir():
            logger.warning("No IMAGES directory in %s", self.patient_dir)
            return []

        sessions: list[CBCTSession] = []
        for img_dir in sorted(images_dir.iterdir()):
            if not img_dir.is_dir() or not img_dir.name.startswith("img_"):
                continue

            frames_xml = img_dir / "_Frames.xml"
            if not frames_xml.exists():
                logger.warning("No _Frames.xml in %s — skipping", img_dir)
                continue

            meta = parse_frames_xml(frames_xml)
            if meta.get("acquisition_preset") is None:
                logger.warning("No AcquisitionPresetName in %s — skipping", frames_xml)
                continue

            session_type = classify_acquisition(meta["acquisition_preset"])
            dicom_uid = meta.get("dicom_uid") or img_dir.name

            session = CBCTSession(
                img_dir=img_dir,
                dicom_uid=dicom_uid,
                acquisition_preset=meta["acquisition_preset"],
                session_type=session_type,
                treatment_id=meta.get("treatment_id") or "",
                tube_kv=meta.get("kv"),
                tube_ma=meta.get("ma"),
            )

            # For CBCT/KIM Learning: extract datetime and registration data
            if session_type in ("cbct", "kim_learning"):
                self._enrich_cbct_session(session)

            sessions.append(session)

        # Sort: sessions with datetime first (chronological), None-datetime at end
        sessions.sort(key=lambda s: (s.scan_datetime is None, s.scan_datetime or datetime.min))
        return sessions

    def _enrich_cbct_session(self, session: CBCTSession) -> None:
        """Populate datetime, RPS, and couch shifts for a CBCT/KIM Learning session."""
        recon_dir = session.img_dir / "Reconstruction"

        # Find INI file for ScanUID → datetime
        if recon_dir.is_dir():
            ini_files = sorted(recon_dir.glob("*.INI"))
            if ini_files:
                session.ini_path = ini_files[0]
                ini_text = ini_files[0].read_text(encoding="utf-8", errors="ignore")
                ini_data = parse_xvi_ini(ini_text)
                scan_uid = ini_data.get("ScanUID")
                if scan_uid:
                    session.scan_datetime = parse_scan_datetime(scan_uid)

        # Find RPS DICOM → couch shifts
        rps_files = sorted(session.img_dir.glob("Reconstruction/*.dcm"))
        if not rps_files:
            rps_files = sorted(session.img_dir.glob("Reconstruction/*.RPS.dcm"))
        if rps_files:
            session.rps_path = rps_files[0]
            session.has_rps = True
            ini_text = extract_ini_from_rps(rps_files[0])
            if ini_text:
                session.couch_shifts = parse_couch_shifts(ini_text)

    # ----- MotionView date matching -----

    def _match_motionview_dates(
        self,
        dated: list[CBCTSession],
        undated: list[CBCTSession],
    ) -> None:
        """Assign scan_datetime to MotionView sessions by matching DicomUID prefixes.

        Mutates undated sessions in place.
        """
        if not dated or not undated:
            return

        for mv_session in undated:
            best_match: Optional[CBCTSession] = None
            best_prefix_len = 0

            for d_session in dated:
                # Find longest common prefix between DicomUIDs
                prefix_len = 0
                for a, b in zip(mv_session.dicom_uid, d_session.dicom_uid):
                    if a == b:
                        prefix_len += 1
                    else:
                        break

                if prefix_len > best_prefix_len:
                    best_prefix_len = prefix_len
                    best_match = d_session

            if best_match and best_match.scan_datetime:
                mv_session.scan_datetime = best_match.scan_datetime
                logger.info(
                    "Matched MotionView %s to dated session %s (prefix=%d)",
                    mv_session.img_dir.name,
                    best_match.img_dir.name,
                    best_prefix_len,
                )
            else:
                logger.warning(
                    "Could not match MotionView session %s to any dated session",
                    mv_session.img_dir.name,
                )

    # ----- Fraction assignment -----

    def assign_fractions(self, sessions: list[CBCTSession]) -> dict[str, list[CBCTSession]]:
        """Group sessions into fractions by date.

        Parameters
        ----------
        sessions : list[CBCTSession]
            All sessions with scan_datetime assigned.

        Returns
        -------
        dict[str, list[CBCTSession]]
            ``{"FX0": [...], "FX1": [...], ...}`` sorted chronologically.
        """
        # Sort all sessions by datetime
        sorted_sessions = sorted(
            sessions,
            key=lambda s: s.scan_datetime or datetime.min,
        )

        # Group by date
        date_groups: dict[str, list[CBCTSession]] = {}
        for s in sorted_sessions:
            if s.scan_datetime is None:
                date_key = "unknown"
            else:
                date_key = s.scan_datetime.strftime("%Y-%m-%d")

            if date_key not in date_groups:
                date_groups[date_key] = []
            date_groups[date_key].append(s)

        # Assign fraction labels in chronological order
        fraction_map: dict[str, list[CBCTSession]] = {}
        for fx_idx, date_key in enumerate(sorted(date_groups.keys())):
            fx_label = f"FX{fx_idx}"
            fraction_map[fx_label] = date_groups[date_key]

        return fraction_map

    # ----- Directory creation -----

    def create_learn_structure(self, fraction_map: dict[str, list[CBCTSession]]) -> Path:
        """Create the full LEARN directory tree.

        Returns the site root path.
        """
        site_root = self.output_base / self.site_name

        # Patient Files
        patient_files = site_root / "Patient Files" / self.anon_id
        patient_files.mkdir(parents=True, exist_ok=True)

        # Patient Plans
        plans_root = site_root / "Patient Plans" / self.anon_id
        for subdir in ("CT", "Plan", "Dose", "Structure Set"):
            (plans_root / subdir).mkdir(parents=True, exist_ok=True)

        # Ground Truth
        gt_root = site_root / "Ground Truth" / self.anon_id
        gt_root.mkdir(parents=True, exist_ok=True)

        # Patient Images — per fraction
        images_root = site_root / "Patient Images" / self.anon_id
        for fx_label, sessions in fraction_map.items():
            fx_path = images_root / fx_label

            # Count CBCT/KIM-Learning sessions for numbering
            cbct_sessions = [
                s for s in sessions if s.session_type in ("cbct", "kim_learning")
            ]
            cbct_sessions.sort(key=lambda s: s.scan_datetime or datetime.min)

            for cbct_idx, _session in enumerate(cbct_sessions, start=1):
                cbct_path = fx_path / "CBCT" / f"CBCT{cbct_idx}"
                (cbct_path / "CBCT Projections" / "CDOG").mkdir(parents=True, exist_ok=True)
                (cbct_path / "CBCT Projections" / "IPS").mkdir(parents=True, exist_ok=True)
                (cbct_path / "Reconstructed CBCT").mkdir(parents=True, exist_ok=True)
                (cbct_path / "Registration file").mkdir(parents=True, exist_ok=True)

            # KIM-KV directory (always created per fraction)
            (fx_path / "KIM-KV").mkdir(parents=True, exist_ok=True)

        return site_root

    # ----- File copying -----

    def copy_cbct_files(self, session: CBCTSession, cbct_path: Path) -> dict:
        """Copy CBCT/KIM-Learning files to the LEARN structure.

        Returns ``{"his": N, "scan": M, "rps": K}``.
        """
        counts = {"his": 0, "scan": 0, "rps": 0}

        # .his → CBCT Projections/IPS/
        ips_dir = cbct_path / "CBCT Projections" / "IPS"
        ips_dir.mkdir(parents=True, exist_ok=True)
        for his_file in sorted(session.img_dir.glob("*.his")):
            shutil.copy2(his_file, ips_dir / his_file.name)
            counts["his"] += 1

        # Reconstruction/ SCAN files → Reconstructed CBCT/
        recon_dest = cbct_path / "Reconstructed CBCT"
        recon_dest.mkdir(parents=True, exist_ok=True)
        recon_src = session.img_dir / "Reconstruction"
        if recon_src.is_dir():
            for scan_file in sorted(recon_src.iterdir()):
                if ".SCAN" in scan_file.name.upper():
                    shutil.copy2(scan_file, recon_dest / scan_file.name)
                    counts["scan"] += 1

        # RPS → Registration file/
        if session.rps_path and session.rps_path.exists():
            reg_dest = cbct_path / "Registration file"
            reg_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(session.rps_path, reg_dest / session.rps_path.name)
            counts["rps"] += 1

        return counts

    def copy_motionview_files(self, session: CBCTSession, fx_path: Path) -> int:
        """Copy MotionView .his files to KIM-KV/{img_dirname}/.

        Returns file count.
        """
        dest = fx_path / "KIM-KV" / session.img_dir.name
        dest.mkdir(parents=True, exist_ok=True)
        count = 0
        for his_file in sorted(session.img_dir.glob("*.his")):
            shutil.copy2(his_file, dest / his_file.name)
            count += 1
        return count

    def copy_anonymised_plans(
        self, anon_ct_dir: Path, anon_plan_dir: Path
    ) -> dict:
        """Copy anonymised CT and plan DICOM files to the LEARN structure.

        Returns ``{"ct_count": N, "plan_count": M}``.
        """
        site_root = self.output_base / self.site_name
        plans_root = site_root / "Patient Plans" / self.anon_id
        counts = {"ct_count": 0, "plan_count": 0}

        ct_dest = plans_root / "CT"
        ct_dest.mkdir(parents=True, exist_ok=True)
        if anon_ct_dir.is_dir():
            for f in sorted(anon_ct_dir.iterdir()):
                if f.is_file():
                    shutil.copy2(f, ct_dest / f.name)
                    counts["ct_count"] += 1

        plan_dest = plans_root / "Plan"
        plan_dest.mkdir(parents=True, exist_ok=True)
        if anon_plan_dir.is_dir():
            for f in sorted(anon_plan_dir.iterdir()):
                if f.is_file():
                    shutil.copy2(f, plan_dest / f.name)
                    counts["plan_count"] += 1

        return counts

    # ----- Execute -----

    def execute(
        self,
        anon_ct_dir: Path = None,
        anon_plan_dir: Path = None,
        dry_run: bool = False,
    ) -> dict:
        """Run the full folder mapping pipeline.

        Parameters
        ----------
        anon_ct_dir : Path, optional
            Directory containing anonymised CT DICOM files.
        anon_plan_dir : Path, optional
            Directory containing anonymised plan DICOM files.
        dry_run : bool
            If True, create directories but skip file copies.

        Returns
        -------
        dict
            Summary with keys: sessions, fractions, files_copied, dry_run.
        """
        # 1. Discover
        sessions = self.discover_sessions()
        logger.info("Discovered %d sessions", len(sessions))

        # 2. Match MotionView dates
        dated = [s for s in sessions if s.scan_datetime is not None]
        undated = [s for s in sessions if s.scan_datetime is None]
        if undated:
            self._match_motionview_dates(dated, undated)

        # 3. Assign fractions
        fraction_map = self.assign_fractions(sessions)
        logger.info("Assigned %d fractions", len(fraction_map))

        # 4. Create directory structure
        site_root = self.create_learn_structure(fraction_map)

        summary = {
            "sessions": len(sessions),
            "fractions": len(fraction_map),
            "files_copied": {"his": 0, "scan": 0, "rps": 0, "motionview": 0},
            "dry_run": dry_run,
        }

        if dry_run:
            logger.info("Dry run — directories created, no files copied")
            return summary

        # 5. Copy files
        images_root = site_root / "Patient Images" / self.anon_id
        for fx_label, sessions_in_fx in fraction_map.items():
            fx_path = images_root / fx_label

            cbct_sessions = [
                s for s in sessions_in_fx if s.session_type in ("cbct", "kim_learning")
            ]
            cbct_sessions.sort(key=lambda s: s.scan_datetime or datetime.min)

            for cbct_idx, session in enumerate(cbct_sessions, start=1):
                cbct_path = fx_path / "CBCT" / f"CBCT{cbct_idx}"
                counts = self.copy_cbct_files(session, cbct_path)
                summary["files_copied"]["his"] += counts["his"]
                summary["files_copied"]["scan"] += counts["scan"]
                summary["files_copied"]["rps"] += counts["rps"]

            mv_sessions = [
                s for s in sessions_in_fx if s.session_type == "motionview"
            ]
            for session in mv_sessions:
                mv_count = self.copy_motionview_files(session, fx_path)
                summary["files_copied"]["motionview"] += mv_count

        # 6. Copy anonymised plans
        if anon_ct_dir and anon_plan_dir and not dry_run:
            plan_counts = self.copy_anonymised_plans(anon_ct_dir, anon_plan_dir)
            summary["files_copied"]["ct"] = plan_counts["ct_count"]
            summary["files_copied"]["plan"] = plan_counts["plan_count"]

        logger.info("Execute complete: %s", summary)
        return summary
