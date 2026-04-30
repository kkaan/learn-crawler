"""Tests for scripts/inventory_crawler.py."""
import sys
from pathlib import Path

# Make scripts/ importable for tests
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _make_machine(root: Path, name: str, with_flexmap: bool) -> Path:
    machine = root / name
    machine.mkdir(parents=True)
    if with_flexmap:
        flex_dir = machine / "Current Calibration Files" / "Current" / "FlexMap"
        flex_dir.mkdir(parents=True)
        (flex_dir / "panel_a.flexmap").write_bytes(b"")
    return machine


# ---------------------------------------------------------------------------
# find_machines_with_flexmaps
# ---------------------------------------------------------------------------

class TestFindMachinesWithFlexmaps:
    def test_returns_only_flexmap_dirs(self, tmp_path):
        from inventory_crawler import find_machines_with_flexmaps

        yes_a = _make_machine(tmp_path, "20230101_CenterA_M1", with_flexmap=True)
        _make_machine(tmp_path, "20230101_CenterB_M2", with_flexmap=False)
        yes_b = _make_machine(tmp_path, "20230101_CenterC_M3", with_flexmap=True)

        result = find_machines_with_flexmaps(tmp_path)

        assert sorted(result) == sorted([yes_a, yes_b])

    def test_ignores_files_at_root(self, tmp_path):
        """Non-directory entries at the processed root must be ignored."""
        from inventory_crawler import find_machines_with_flexmaps

        (tmp_path / "stray_file.txt").write_text("hello")
        yes = _make_machine(tmp_path, "20230101_CenterA_M1", with_flexmap=True)

        assert find_machines_with_flexmaps(tmp_path) == [yes]

    def test_empty_root_returns_empty(self, tmp_path):
        from inventory_crawler import find_machines_with_flexmaps
        assert find_machines_with_flexmaps(tmp_path) == []

    def test_missing_root_returns_empty(self, tmp_path):
        from inventory_crawler import find_machines_with_flexmaps
        assert find_machines_with_flexmaps(tmp_path / "does_not_exist") == []
