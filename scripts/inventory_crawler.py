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
from pathlib import Path

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
