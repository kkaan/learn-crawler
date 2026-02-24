"""Desktop GUI for the LEARN data transfer pipeline.

Provides a 5-step wizard wrapping the existing learn_upload modules:
  1. Configuration — paths, anon ID, PII strings
  2. Data Preview — session discovery and fraction assignment
  3. Anonymise — DICOM anonymisation with per-file progress
  4. Folder Sort — copy files into LEARN directory structure
  5. PII Verification — scan output for residual patient data

Usage:
    python -m learn_upload.gui
"""

import base64
import json
import logging
import sys
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import webview

from learn_upload.anonymise_dicom import DicomAnonymiser
from learn_upload.config import DEFAULT_LEARN_OUTPUT, DEFAULT_XVI_BASE, setup_logging
from learn_upload.folder_sort import LearnFolderMapper
from learn_upload.verify_pii import verify_no_pii

logger = logging.getLogger(__name__)

# Reference to the pywebview window, set in main()
_window: webview.Window | None = None


# ---------------------------------------------------------------------------
# Logging handler that forwards to JS
# ---------------------------------------------------------------------------

class GuiLogHandler(logging.Handler):
    """Captures log records and pushes them to the JS terminal widget."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "level": record.levelname.lower(),
                "name": record.name,
                "message": self.format(record),
            }
            _push_to_js("onLogMessage", entry)
        except Exception:
            pass  # Never let logging errors crash the app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _push_to_js(func_name: str, data) -> None:
    """Thread-safe push of JSON data to a JS callback.

    Base64-encodes the JSON string so we don't need to worry about quote
    escaping when building the JS expression.
    """
    if _window is None:
        return
    raw = json.dumps(data, default=str)
    b64 = base64.b64encode(raw.encode()).decode()
    js = f"{func_name}(JSON.parse(atob('{b64}')))"
    try:
        _window.evaluate_js(js)
    except Exception:
        logger.exception("evaluate_js failed for %s", func_name)


def _serialize_session(session) -> dict:
    """Convert a CBCTSession dataclass to a JSON-safe dict."""
    d = asdict(session)
    for key, val in d.items():
        if isinstance(val, Path):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
    return d


def _get_html_path() -> str:
    """Resolve gui.html for both dev mode and PyInstaller bundle."""
    if getattr(sys, "_MEIPASS", None):
        return str(Path(sys._MEIPASS) / "learn_upload" / "gui.html")
    return str(Path(__file__).parent / "gui.html")


# ---------------------------------------------------------------------------
# API class exposed to JavaScript
# ---------------------------------------------------------------------------

class LearnPipelineAPI:
    """Methods callable from JS via ``window.pywebview.api.<method>(...)``."""

    def __init__(self) -> None:
        self._config: dict | None = None
        self._mapper: LearnFolderMapper | None = None
        self._sessions: list = []
        self._fraction_map: dict = {}
        self._anon_dirs: dict = {}

    # ----- Step 1: Configuration -------------------------------------------

    def get_defaults(self) -> dict:
        """Return default path values for the config form."""
        return {
            "xvi_base": str(DEFAULT_XVI_BASE),
            "output_base": str(DEFAULT_LEARN_OUTPUT),
        }

    def browse_folder(self, title: str = "Select folder") -> str | None:
        """Open a native folder picker and return the selected path."""
        result = _window.create_file_dialog(
            webview.FOLDER_DIALOG, directory="", allow_multiple=False
        )
        if result and len(result) > 0:
            return str(result[0])
        return None

    def browse_file(self, title: str = "Select file") -> str | None:
        """Open a native file picker and return the selected path."""
        result = _window.create_file_dialog(
            webview.OPEN_DIALOG, directory="", allow_multiple=False
        )
        if result and len(result) > 0:
            return str(result[0])
        return None

    def save_config(self, json_str: str) -> dict:
        """Validate and store config from Step 1 form.

        Returns ``{"ok": True}`` or ``{"ok": False, "error": "..."}``
        """
        try:
            cfg = json.loads(json_str) if isinstance(json_str, str) else json_str
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Invalid JSON: {exc}"}

        # Required fields
        required = ["anon_id", "site_name", "source_path", "output_path"]
        for field in required:
            if not cfg.get(field, "").strip():
                return {"ok": False, "error": f"Missing required field: {field}"}

        # Validate anon_id format (PATxx)
        anon_id = cfg["anon_id"].strip()
        if not anon_id.startswith("PAT") or not anon_id[3:].isdigit():
            return {"ok": False, "error": "Anon ID must match PATxx format (e.g. PAT01)"}

        self._config = cfg
        return {"ok": True}

    # ----- Step 2: Discovery -----------------------------------------------

    def run_discovery(self) -> dict:
        """Discover XVI sessions (called from JS via await, runs on pywebview thread)."""
        cfg = self._config
        try:
            patient_dir = Path(cfg["source_path"])
            images_subdir = cfg.get("images_subdir", "IMAGES").strip() or "IMAGES"

            self._mapper = LearnFolderMapper(
                patient_dir=patient_dir,
                anon_id=cfg["anon_id"].strip(),
                site_name=cfg["site_name"].strip(),
                output_base=Path(cfg["output_path"]),
                images_subdir=images_subdir,
            )

            self._sessions = self._mapper.discover_sessions(enrich=False)
            self._fraction_map = self._mapper.assign_fractions(self._sessions)

            # Build serializable result
            fractions = {}
            for fx_label, fx_sessions in self._fraction_map.items():
                fractions[fx_label] = [_serialize_session(s) for s in fx_sessions]

            return {
                "ok": True,
                "session_count": len(self._sessions),
                "fraction_count": len(self._fraction_map),
                "his_count": None,
                "dcm_count": None,
                "fractions": fractions,
            }
        except Exception as exc:
            logger.exception("Discovery failed")
            return {"ok": False, "error": str(exc)}

    # ----- Step 3: Anonymise -----------------------------------------------

    def run_anonymise(self) -> None:
        """Anonymise TPS DICOM files in a background thread."""
        threading.Thread(target=self._do_anonymise, daemon=True).start()

    def _do_anonymise(self) -> None:
        cfg = self._config
        try:
            tps_path = cfg.get("tps_path", "")
            staging_dir = Path(cfg.get("staging_path", "")) or (Path(cfg["output_path"]) / "_staging")

            if not tps_path or not Path(tps_path).is_dir():
                _push_to_js("onAnonymiseComplete", {
                    "ct": 0, "plan": 0, "structures": 0, "dose": 0, "errors": 0,
                    "skipped": True,
                    "message": "No TPS export path provided — skipping anonymisation.",
                })
                return

            tps = Path(tps_path)
            categories = {
                "ct": tps / "DICOM CT Images",
                "plan": tps / "DICOM RT Plan",
                "structures": tps / "DICOM RT Structures",
                "dose": tps / "DICOM RT Dose",
            }

            counts = {"ct": 0, "plan": 0, "structures": 0, "dose": 0, "errors": 0}
            total_files = 0
            for cat, src in categories.items():
                if src.is_dir():
                    total_files += len(list(src.rglob("*.dcm")))

            _push_to_js("onProgress", {"step": 3, "current": 0, "total": total_files, "file": ""})

            processed = 0
            for category, source_dir in categories.items():
                if not source_dir.is_dir():
                    logger.info("Category %s — directory not found, skipping", category)
                    continue

                cat_staging = staging_dir / category
                anon = DicomAnonymiser(
                    patient_dir=Path(cfg["source_path"]),
                    anon_id=cfg["anon_id"].strip(),
                    output_dir=cat_staging,
                    site_name=cfg["site_name"].strip(),
                )

                dcm_files = list(source_dir.rglob("*.dcm"))
                for dcm_file in dcm_files:
                    try:
                        anon.anonymise_file(dcm_file, source_base=source_dir)
                        counts[category] += 1
                    except Exception:
                        counts["errors"] += 1
                        logger.exception("Failed to anonymise %s", dcm_file.name)
                    processed += 1
                    _push_to_js("onProgress", {
                        "step": 3,
                        "current": processed,
                        "total": total_files,
                        "file": dcm_file.name,
                    })

                self._anon_dirs[category] = cat_staging

            counts["skipped"] = False
            _push_to_js("onAnonymiseComplete", counts)
        except Exception as exc:
            logger.exception("Anonymisation failed")
            _push_to_js("onError", {"step": 3, "message": str(exc)})

    # ----- Step 4: Folder Sort ---------------------------------------------

    def run_folder_sort(self) -> None:
        """Run folder mapping in a background thread."""
        threading.Thread(target=self._do_folder_sort, daemon=True).start()

    def _do_folder_sort(self) -> None:
        cfg = self._config
        try:
            if self._mapper is None:
                _push_to_js("onError", {"step": 4, "message": "Run discovery first."})
                return

            _push_to_js("onProgress", {"step": 4, "indeterminate": True, "file": "Starting folder sort..."})

            centroid_path = cfg.get("centroid_path", "")
            trajectory_dir = cfg.get("trajectory_dir", "")
            dry_run = cfg.get("dry_run", False)

            summary = self._mapper.execute(
                anon_ct_dir=self._anon_dirs.get("ct"),
                anon_plan_dir=self._anon_dirs.get("plan"),
                anon_struct_dir=self._anon_dirs.get("structures"),
                anon_dose_dir=self._anon_dirs.get("dose"),
                centroid_path=Path(centroid_path) if centroid_path else None,
                trajectory_base_dir=Path(trajectory_dir) if trajectory_dir else None,
                dry_run=dry_run,
            )

            _push_to_js("onFolderSortComplete", summary)
        except Exception as exc:
            logger.exception("Folder sort failed")
            _push_to_js("onError", {"step": 4, "message": str(exc)})

    # ----- Step 5: PII Verification ----------------------------------------

    def run_pii_check(self) -> None:
        """Run PII verification in a background thread."""
        threading.Thread(target=self._do_pii_check, daemon=True).start()

    def _do_pii_check(self) -> None:
        cfg = self._config
        try:
            pii_strings = [s.strip() for s in cfg.get("pii_strings", []) if s.strip()]
            if not pii_strings:
                _push_to_js("onPiiCheckComplete", {
                    "passed": True,
                    "files_scanned": 0,
                    "findings": [],
                    "message": "No PII strings configured — skipping verification.",
                })
                return

            _push_to_js("onProgress", {"step": 5, "indeterminate": True, "file": "Scanning for PII..."})

            # Determine output patient directory
            output_base = Path(cfg["output_path"])
            site_name = cfg["site_name"].strip()
            anon_id = cfg["anon_id"].strip()
            scan_dir = output_base / site_name / "Patient Plans" / anon_id

            if not scan_dir.is_dir():
                # Fall back to scanning the whole output base
                scan_dir = output_base

            findings = verify_no_pii(scan_dir, pii_strings)

            # Serialize Path objects
            serialized = []
            for f in findings:
                serialized.append({
                    "file": str(f["file"]),
                    "location": f["location"],
                    "matched": f["matched"],
                })

            _push_to_js("onPiiCheckComplete", {
                "passed": len(findings) == 0,
                "files_scanned": len(list(scan_dir.rglob("*"))),
                "findings": serialized,
            })
        except Exception as exc:
            logger.exception("PII verification failed")
            _push_to_js("onError", {"step": 5, "message": str(exc)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global _window

    setup_logging(logging.INFO)

    # Attach GUI log handler to root logger
    handler = GuiLogHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s — %(message)s"))
    logging.getLogger().addHandler(handler)

    api = LearnPipelineAPI()
    html_path = _get_html_path()

    _window = webview.create_window(
        "LEARN Pipeline",
        url=html_path,
        js_api=api,
        width=1200,
        height=820,
        min_size=(900, 600),
    )

    webview.start(debug=("--debug" in sys.argv))


if __name__ == "__main__":
    main()
