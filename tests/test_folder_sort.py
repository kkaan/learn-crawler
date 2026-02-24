"""Tests for learn_upload.folder_sort — session discovery, fraction assignment, file copying."""

import textwrap
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pydicom
from pydicom.dataset import FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import pytest

from learn_upload.folder_sort import (
    CBCTSession,
    LearnFolderMapper,
    classify_acquisition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frames_xml(
    treatment_id: str = "Prostate",
    preset_name: str = "4ee Pelvis Soft S20 179-181",
    dicom_uid: str = "1.3.46.423632.33783920233217242713.500",
    kv: str = "120.0",
    ma: str = "25.5",
    patient_id: str = "",
    patient_first_name: str = "",
    patient_last_name: str = "",
) -> str:
    """Generate a _Frames.xml content string."""
    patient_block = ""
    if patient_id or patient_first_name or patient_last_name:
        patient_block = (
            "    <Patient>\n"
            f"        <FirstName>{patient_first_name}</FirstName>\n"
            f"        <LastName>{patient_last_name}</LastName>\n"
            f"        <ID>{patient_id}</ID>\n"
            "    </Patient>\n"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<FrameData>\n"
        f"{patient_block}"
        "    <Treatment>\n"
        f"        <ID>{treatment_id}</ID>\n"
        "    </Treatment>\n"
        "    <Image>\n"
        f"        <AcquisitionPresetName>{preset_name}</AcquisitionPresetName>\n"
        f"        <DicomUID>{dicom_uid}</DicomUID>\n"
        f"        <kV>{kv}</kV>\n"
        f"        <mA>{ma}</mA>\n"
        "    </Image>\n"
        "    <Frames>\n"
        '        <Frame Number="1" />\n'
        "    </Frames>\n"
        "</FrameData>\n"
    )


def _make_xvi_session(
    tmp_path: Path,
    img_name: str,
    preset_name: str = "4ee Pelvis Soft S20 179-181",
    treatment_id: str = "Prostate",
    dicom_uid: str = "1.3.46.423632.33783920233217242713.500",
    scan_uid: str = "1.3.46.423632.33783920233217242713.224.2023-03-21165402768",
    num_his: int = 2,
    with_reconstruction: bool = True,
    with_rps: bool = False,
) -> Path:
    """Create a synthetic img_* directory with _Frames.xml and optional files."""
    patient_dir = tmp_path / "patient_12345"
    images_dir = patient_dir / "IMAGES"
    img_dir = images_dir / img_name
    img_dir.mkdir(parents=True, exist_ok=True)

    # _Frames.xml
    xml_content = _make_frames_xml(
        treatment_id=treatment_id,
        preset_name=preset_name,
        dicom_uid=dicom_uid,
    )
    (img_dir / "_Frames.xml").write_text(xml_content, encoding="utf-8")

    # .his files
    for i in range(num_his):
        (img_dir / f"frame_{i:04d}.his").write_bytes(b"\x00" * 100)

    # Reconstruction directory
    if with_reconstruction:
        recon_dir = img_dir / "Reconstruction"
        recon_dir.mkdir()
        ini_content = textwrap.dedent(f"""\
            [IDENTIFICATION]
            PatientID=12345
            TreatmentID={treatment_id}
            ScanUID={scan_uid}

            [RECONSTRUCTION]
            TubeKV=120.0
            TubeMA=25.5
        """)
        (recon_dir / "recon.INI").write_text(ini_content, encoding="utf-8")
        (recon_dir / "volume.SCAN").write_bytes(b"\x00" * 200)
        (recon_dir / "volume.SCAN.MACHINEORIENTATION").write_bytes(b"\x00" * 50)

    return patient_dir


def _make_modality_dcm(directory: Path, filename: str, modality: str) -> Path:
    """Create a minimal DICOM file with a specific Modality tag."""
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / filename

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(filepath), {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.Modality = modality
    ds.PatientName = "Test^Patient"
    ds.PatientID = "12345"
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID

    ds.save_as(filepath)
    return filepath


# ---------------------------------------------------------------------------
# classify_dicom_files
# ---------------------------------------------------------------------------

class TestClassifyDicomFiles:
    def test_classifies_by_modality(self, tmp_path):
        """CT, RTPLAN, RTSTRUCT, RTDOSE correctly classified."""
        source = tmp_path / "tps_export"
        _make_modality_dcm(source / "CT_SET", "slice1.dcm", "CT")
        _make_modality_dcm(source / "CT_SET", "slice2.dcm", "CT")
        _make_modality_dcm(source / "DICOM_PLAN", "plan.dcm", "RTPLAN")
        _make_modality_dcm(source / "Structures", "struct.dcm", "RTSTRUCT")
        _make_modality_dcm(source / "Dose", "dose.dcm", "RTDOSE")

        result = LearnFolderMapper.classify_dicom_files(source)

        assert len(result["ct"]) == 2
        assert len(result["plan"]) == 1
        assert len(result["structures"]) == 1
        assert len(result["dose"]) == 1

    def test_unknown_modality_excluded(self, tmp_path):
        """Unrecognised modality logged, not in results."""
        source = tmp_path / "tps_export"
        _make_modality_dcm(source, "mystery.dcm", "MR")
        _make_modality_dcm(source, "ct.dcm", "CT")

        result = LearnFolderMapper.classify_dicom_files(source)

        assert len(result["ct"]) == 1
        # MR should not appear in any category
        all_files = result["ct"] + result["plan"] + result["structures"] + result["dose"]
        assert len(all_files) == 1

    def test_empty_dir(self, tmp_path):
        source = tmp_path / "empty"
        source.mkdir()
        result = LearnFolderMapper.classify_dicom_files(source)
        assert all(len(v) == 0 for v in result.values())

    def test_nonexistent_dir(self, tmp_path):
        result = LearnFolderMapper.classify_dicom_files(tmp_path / "nope")
        assert all(len(v) == 0 for v in result.values())

    def test_recursive_discovery(self, tmp_path):
        """Files in deeply nested directories are found."""
        source = tmp_path / "export"
        _make_modality_dcm(source / "a" / "b" / "c", "deep.dcm", "RTDOSE")

        result = LearnFolderMapper.classify_dicom_files(source)
        assert len(result["dose"]) == 1


# ---------------------------------------------------------------------------
# classify_acquisition
# ---------------------------------------------------------------------------

class TestClassifyAcquisition:
    def test_cbct(self):
        assert classify_acquisition("4ee Pelvis Soft S20 179-181") == "cbct"

    def test_kim_learning(self):
        assert classify_acquisition("12aa KIM S20 R 34-181") == "kim_learning"

    def test_motionview(self):
        assert classify_acquisition("13a KIM S20 MotionView") == "motionview"

    def test_case_insensitive(self):
        assert classify_acquisition("KIM motionview preset") == "motionview"
        assert classify_acquisition("kim learning preset") == "kim_learning"


# ---------------------------------------------------------------------------
# discover_sessions
# ---------------------------------------------------------------------------

class TestDiscoverSessions:
    @patch("learn_upload.folder_sort.extract_ini_from_rps", return_value=None)
    def test_discover_sessions_basic(self, mock_rps, tmp_path):
        patient_dir = _make_xvi_session(
            tmp_path, "img_001",
            scan_uid="1.3.46.423632.12345.2023-03-21100000000",
            dicom_uid="1.3.46.001",
        )
        # Add second session
        img2 = patient_dir / "IMAGES" / "img_002"
        img2.mkdir()
        xml2 = _make_frames_xml(
            dicom_uid="1.3.46.002",
            preset_name="4ee Pelvis Soft S20 179-181",
        )
        (img2 / "_Frames.xml").write_text(xml2, encoding="utf-8")
        recon2 = img2 / "Reconstruction"
        recon2.mkdir()
        ini2 = "[IDENTIFICATION]\nScanUID=1.3.46.423632.12345.2023-03-22110000000\n"
        (recon2 / "recon.INI").write_text(ini2, encoding="utf-8")

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        sessions = mapper.discover_sessions()

        assert len(sessions) == 2
        assert sessions[0].scan_datetime < sessions[1].scan_datetime

    @patch("learn_upload.folder_sort.extract_ini_from_rps", return_value=None)
    def test_discover_sessions_skips_missing_xml(self, mock_rps, tmp_path):
        patient_dir = _make_xvi_session(tmp_path, "img_001")
        # Create a dir with no _Frames.xml
        no_xml_dir = patient_dir / "IMAGES" / "img_999"
        no_xml_dir.mkdir()

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        sessions = mapper.discover_sessions()
        assert len(sessions) == 1

    @patch("learn_upload.folder_sort.extract_ini_from_rps", return_value=None)
    def test_discover_motionview_session(self, mock_rps, tmp_path):
        patient_dir = _make_xvi_session(
            tmp_path, "img_mv",
            preset_name="13a KIM S20 MotionView",
            with_reconstruction=False,
        )

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        sessions = mapper.discover_sessions()

        assert len(sessions) == 1
        assert sessions[0].session_type == "motionview"
        assert sessions[0].scan_datetime is None

    def test_missing_images_dir(self, tmp_path):
        patient_dir = tmp_path / "patient_empty"
        patient_dir.mkdir()

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        sessions = mapper.discover_sessions()
        assert sessions == []


# ---------------------------------------------------------------------------
# _match_motionview_dates
# ---------------------------------------------------------------------------

class TestMatchMotionviewDates:
    def test_match_motionview_dates(self, tmp_path):
        dated_dt = datetime(2023, 3, 21, 10, 0, 0)
        dated = CBCTSession(
            img_dir=tmp_path / "img_001",
            dicom_uid="1.3.46.423632.ABCDEF.001",
            acquisition_preset="4ee Pelvis",
            session_type="cbct",
            treatment_id="Prostate",
            scan_datetime=dated_dt,
        )
        undated = CBCTSession(
            img_dir=tmp_path / "img_mv",
            dicom_uid="1.3.46.423632.ABCDEF.999",
            acquisition_preset="13a KIM MotionView",
            session_type="motionview",
            treatment_id="Prostate",
            scan_datetime=None,
        )

        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", tmp_path / "out")
        mapper._match_motionview_dates([dated], [undated])

        assert undated.scan_datetime == dated_dt


# ---------------------------------------------------------------------------
# assign_fractions
# ---------------------------------------------------------------------------

class TestAssignFractions:
    def _make_session(self, tmp_path, dt, session_type="cbct", name="img_001"):
        return CBCTSession(
            img_dir=tmp_path / name,
            dicom_uid=f"uid_{name}",
            acquisition_preset="preset",
            session_type=session_type,
            treatment_id="Prostate",
            scan_datetime=dt,
        )

    def test_assign_fractions_chronological(self, tmp_path):
        s1 = self._make_session(tmp_path, datetime(2023, 3, 21, 10, 0), name="img_001")
        s2 = self._make_session(tmp_path, datetime(2023, 3, 22, 10, 0), name="img_002")
        s3 = self._make_session(tmp_path, datetime(2023, 3, 23, 10, 0), name="img_003")

        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", tmp_path / "out")
        fractions = mapper.assign_fractions([s1, s2, s3])

        assert list(fractions.keys()) == ["FX0", "FX1", "FX2"]
        assert fractions["FX0"] == [s1]
        assert fractions["FX1"] == [s2]
        assert fractions["FX2"] == [s3]

    def test_assign_fractions_same_day(self, tmp_path):
        s1 = self._make_session(tmp_path, datetime(2023, 3, 21, 10, 0), name="img_001")
        s2 = self._make_session(tmp_path, datetime(2023, 3, 21, 14, 0), name="img_002")

        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", tmp_path / "out")
        fractions = mapper.assign_fractions([s1, s2])

        assert list(fractions.keys()) == ["FX0"]
        assert len(fractions["FX0"]) == 2

    def test_assign_fractions_with_motionview(self, tmp_path):
        s1 = self._make_session(tmp_path, datetime(2023, 3, 21, 10, 0), name="img_001")
        mv = self._make_session(
            tmp_path, datetime(2023, 3, 21, 10, 5),
            session_type="motionview", name="img_mv",
        )

        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", tmp_path / "out")
        fractions = mapper.assign_fractions([s1, mv])

        assert "FX0" in fractions
        assert len(fractions["FX0"]) == 2
        types = {s.session_type for s in fractions["FX0"]}
        assert types == {"cbct", "motionview"}


# ---------------------------------------------------------------------------
# create_learn_structure
# ---------------------------------------------------------------------------

class TestCreateLearnStructure:
    def test_create_learn_structure(self, tmp_path):
        s1 = CBCTSession(
            img_dir=tmp_path / "img_001",
            dicom_uid="uid1",
            acquisition_preset="4ee Pelvis",
            session_type="cbct",
            treatment_id="Prostate",
            scan_datetime=datetime(2023, 3, 21, 10, 0),
        )
        s2 = CBCTSession(
            img_dir=tmp_path / "img_002",
            dicom_uid="uid2",
            acquisition_preset="4ee Pelvis",
            session_type="cbct",
            treatment_id="Prostate",
            scan_datetime=datetime(2023, 3, 21, 14, 0),
        )

        fraction_map = {"FX0": [s1, s2]}
        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", tmp_path / "out")
        site_root = mapper.create_learn_structure(fraction_map)

        # Verify key directories exist
        assert (site_root / "Patient Files" / "PAT01").is_dir()
        assert (site_root / "Patient Plans" / "PAT01" / "CT").is_dir()
        assert (site_root / "Patient Plans" / "PAT01" / "Plan").is_dir()
        assert (site_root / "Patient Plans" / "PAT01" / "Dose").is_dir()
        assert (site_root / "Patient Plans" / "PAT01" / "Structure Set").is_dir()
        assert (site_root / "Ground Truth" / "PAT01").is_dir()

        # Verify fraction structure
        fx0 = site_root / "Patient Images" / "PAT01" / "FX0"
        assert (fx0 / "CBCT" / "CBCT1" / "CBCT Projections" / "IPS").is_dir()
        assert (fx0 / "CBCT" / "CBCT1" / "CBCT Projections" / "CDOG").is_dir()
        assert (fx0 / "CBCT" / "CBCT1" / "Reconstructed CBCT").is_dir()
        assert (fx0 / "CBCT" / "CBCT1" / "Registration file").is_dir()
        assert (fx0 / "CBCT" / "CBCT2" / "CBCT Projections" / "IPS").is_dir()
        assert (fx0 / "KIM-KV").is_dir()


# ---------------------------------------------------------------------------
# copy_cbct_files
# ---------------------------------------------------------------------------

class TestCopyCbctFiles:
    def test_copy_cbct_files(self, tmp_path):
        # Set up source session
        patient_dir = _make_xvi_session(tmp_path, "img_001", num_his=3)
        img_dir = patient_dir / "IMAGES" / "img_001"

        session = CBCTSession(
            img_dir=img_dir,
            dicom_uid="uid1",
            acquisition_preset="4ee Pelvis",
            session_type="cbct",
            treatment_id="Prostate",
            has_rps=False,
            rps_path=None,
        )

        # Set up destination
        cbct_path = tmp_path / "dest" / "CBCT1"
        cbct_path.mkdir(parents=True)

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        counts = mapper.copy_cbct_files(session, cbct_path)

        assert counts["his"] == 3
        assert counts["scan"] == 2  # .SCAN + .SCAN.MACHINEORIENTATION
        assert counts["rps"] == 0

        # Verify files exist
        ips_files = list((cbct_path / "CBCT Projections" / "IPS").glob("*.his"))
        assert len(ips_files) == 3


# ---------------------------------------------------------------------------
# copy_motionview_files
# ---------------------------------------------------------------------------

class TestCopyMotionviewFiles:
    def test_copy_motionview_files(self, tmp_path):
        # Set up MV source
        patient_dir = _make_xvi_session(
            tmp_path, "img_mv",
            preset_name="13a KIM S20 MotionView",
            with_reconstruction=False,
            num_his=5,
        )
        img_dir = patient_dir / "IMAGES" / "img_mv"

        session = CBCTSession(
            img_dir=img_dir,
            dicom_uid="uid_mv",
            acquisition_preset="13a KIM S20 MotionView",
            session_type="motionview",
            treatment_id="Prostate",
        )

        fx_path = tmp_path / "dest" / "FX0"
        fx_path.mkdir(parents=True)

        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", tmp_path / "out")
        counts = mapper.copy_motionview_files(session, fx_path)

        assert counts["his"] == 5
        assert counts["frames_xml"] == 1  # _Frames.xml from _make_xvi_session
        dest_files = list((fx_path / "KIM-KV" / "img_mv").glob("*.his"))
        assert len(dest_files) == 5


# ---------------------------------------------------------------------------
# copy_anonymised_plans
# ---------------------------------------------------------------------------

class TestCopyAnonymisedPlans:
    def test_copy_anonymised_plans(self, tmp_path):
        # Set up anonymised source dirs
        ct_dir = tmp_path / "anon" / "CT_SET"
        ct_dir.mkdir(parents=True)
        for i in range(3):
            (ct_dir / f"ct_{i}.dcm").write_bytes(b"\x00" * 50)

        plan_dir = tmp_path / "anon" / "DICOM_PLAN"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.dcm").write_bytes(b"\x00" * 50)

        out = tmp_path / "out"
        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", out)
        counts = mapper.copy_anonymised_plans(ct_dir, plan_dir)

        assert counts["ct_count"] == 3
        assert counts["plan_count"] == 1
        assert counts["structures_count"] == 0
        assert counts["dose_count"] == 0

        # Verify destination
        assert (out / "Prostate" / "Patient Plans" / "PAT01" / "CT" / "ct_0.dcm").exists()
        assert (out / "Prostate" / "Patient Plans" / "PAT01" / "Plan" / "plan.dcm").exists()

    def test_copy_all_four_categories(self, tmp_path):
        ct_dir = tmp_path / "anon" / "ct"
        ct_dir.mkdir(parents=True)
        (ct_dir / "ct.dcm").write_bytes(b"\x00" * 50)

        plan_dir = tmp_path / "anon" / "plan"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.dcm").write_bytes(b"\x00" * 50)

        struct_dir = tmp_path / "anon" / "struct"
        struct_dir.mkdir(parents=True)
        (struct_dir / "struct.dcm").write_bytes(b"\x00" * 50)

        dose_dir = tmp_path / "anon" / "dose"
        dose_dir.mkdir(parents=True)
        (dose_dir / "dose.dcm").write_bytes(b"\x00" * 50)
        (dose_dir / "dose2.dcm").write_bytes(b"\x00" * 50)

        out = tmp_path / "out"
        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", out)
        counts = mapper.copy_anonymised_plans(
            ct_dir, plan_dir, struct_dir, dose_dir
        )

        assert counts == {
            "ct_count": 1,
            "plan_count": 1,
            "structures_count": 1,
            "dose_count": 2,
        }

        plans_root = out / "Prostate" / "Patient Plans" / "PAT01"
        assert (plans_root / "CT" / "ct.dcm").exists()
        assert (plans_root / "Plan" / "plan.dcm").exists()
        assert (plans_root / "Structure Set" / "struct.dcm").exists()
        assert (plans_root / "Dose" / "dose.dcm").exists()
        assert (plans_root / "Dose" / "dose2.dcm").exists()

    def test_none_dirs_skipped(self, tmp_path):
        """Passing None for all dirs returns all-zero counts."""
        out = tmp_path / "out"
        mapper = LearnFolderMapper(tmp_path, "PAT01", "Prostate", out)
        counts = mapper.copy_anonymised_plans()

        assert counts == {
            "ct_count": 0,
            "plan_count": 0,
            "structures_count": 0,
            "dose_count": 0,
        }


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

class TestExecute:
    @patch("learn_upload.folder_sort.extract_ini_from_rps", return_value=None)
    def test_execute_dry_run(self, mock_rps, tmp_path):
        patient_dir = _make_xvi_session(
            tmp_path, "img_001",
            scan_uid="1.3.46.423632.12345.2023-03-21100000000",
        )

        out = tmp_path / "out"
        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", out)
        summary = mapper.execute(dry_run=True)

        assert summary["dry_run"] is True
        assert summary["sessions"] == 1
        assert summary["fractions"] == 1
        # Dirs should exist
        assert (out / "Prostate" / "Patient Images" / "PAT01" / "FX0" / "CBCT" / "CBCT1").is_dir()
        # No files should be copied
        ips = out / "Prostate" / "Patient Images" / "PAT01" / "FX0" / "CBCT" / "CBCT1" / "CBCT Projections" / "IPS"
        assert list(ips.glob("*.his")) == []

    @patch("learn_upload.folder_sort.extract_ini_from_rps", return_value=None)
    def test_execute_full(self, mock_rps, tmp_path):
        patient_dir = _make_xvi_session(
            tmp_path, "img_001",
            scan_uid="1.3.46.423632.12345.2023-03-21100000000",
            num_his=4,
        )

        out = tmp_path / "out"
        mapper = LearnFolderMapper(patient_dir, "PAT01", "Prostate", out)
        summary = mapper.execute(dry_run=False)

        assert summary["dry_run"] is False
        assert summary["sessions"] == 1
        assert summary["fractions"] == 1
        assert summary["files_copied"]["his"] == 4
        assert summary["files_copied"]["scan"] == 2  # .SCAN + .SCAN.MACHINEORIENTATION

        # Verify files actually copied
        ips = out / "Prostate" / "Patient Images" / "PAT01" / "FX0" / "CBCT" / "CBCT1" / "CBCT Projections" / "IPS"
        assert len(list(ips.glob("*.his"))) == 4


# ---------------------------------------------------------------------------
# _Frames.xml copying
# ---------------------------------------------------------------------------

class TestFramesXmlCopied:
    def test_frames_xml_copied_with_cbct(self, tmp_path):
        """_Frames.xml anonymised and placed in IPS/ alongside .his files."""
        patient_dir = tmp_path / "patient_12345678"
        images_dir = patient_dir / "IMAGES" / "img_001"
        images_dir.mkdir(parents=True)

        xml_content = _make_frames_xml(
            patient_id="12345678",
            patient_first_name="JOHN",
            patient_last_name="SMITH",
        )
        (images_dir / "_Frames.xml").write_text(xml_content, encoding="utf-8")
        (images_dir / "frame_0000.his").write_bytes(b"\x00" * 100)

        # Reconstruction for datetime extraction
        recon = images_dir / "Reconstruction"
        recon.mkdir()
        ini = "[IDENTIFICATION]\nScanUID=1.3.46.423632.12345.2023-03-21100000000\n"
        (recon / "recon.INI").write_text(ini, encoding="utf-8")

        session = CBCTSession(
            img_dir=images_dir,
            dicom_uid="uid1",
            acquisition_preset="4ee Pelvis",
            session_type="cbct",
            treatment_id="Prostate",
        )

        cbct_path = tmp_path / "dest" / "CBCT1"
        cbct_path.mkdir(parents=True)

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", tmp_path / "out")
        counts = mapper.copy_cbct_files(session, cbct_path)

        assert counts["frames_xml"] == 1
        output_xml = cbct_path / "CBCT Projections" / "IPS" / "_Frames.xml"
        assert output_xml.exists()

        # Verify PII removed
        tree = ET.parse(output_xml)
        root = tree.getroot()
        patient = root.find("Patient")
        assert patient.find("ID").text == "PRIME001"
        assert patient.find("LastName").text == "PRIME001"

    def test_frames_xml_copied_with_motionview(self, tmp_path):
        """_Frames.xml anonymised and placed in KIM-KV/{img_dir}/."""
        patient_dir = tmp_path / "patient_12345678"
        images_dir = patient_dir / "IMAGES" / "img_mv01"
        images_dir.mkdir(parents=True)

        xml_content = _make_frames_xml(
            preset_name="13a KIM S20 MotionView",
            patient_id="12345678",
            patient_first_name="JOHN",
            patient_last_name="SMITH",
        )
        (images_dir / "_Frames.xml").write_text(xml_content, encoding="utf-8")
        for i in range(3):
            (images_dir / f"frame_{i:04d}.his").write_bytes(b"\x00" * 100)

        session = CBCTSession(
            img_dir=images_dir,
            dicom_uid="uid_mv",
            acquisition_preset="13a KIM S20 MotionView",
            session_type="motionview",
            treatment_id="Prostate",
        )

        fx_path = tmp_path / "dest" / "FX0"
        fx_path.mkdir(parents=True)

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", tmp_path / "out")
        counts = mapper.copy_motionview_files(session, fx_path)

        assert counts["his"] == 3
        assert counts["frames_xml"] == 1

        output_xml = fx_path / "KIM-KV" / "img_mv01" / "_Frames.xml"
        assert output_xml.exists()

        tree = ET.parse(output_xml)
        root = tree.getroot()
        patient = root.find("Patient")
        assert patient.find("ID").text == "PRIME001"
        assert "12345678" not in output_xml.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# copy_centroid_file
# ---------------------------------------------------------------------------

class TestCopyCentroidFile:
    def test_copy_centroid_file(self, tmp_path):
        """MRN and patient name replaced; file placed in Patient Files/."""
        patient_dir = tmp_path / "patient_12345678"
        patient_dir.mkdir()

        centroid = tmp_path / "Centroid_12345678.txt"
        centroid.write_text("12345678\nSMITH JOHN\n1.23 4.56 7.89\n", encoding="utf-8")

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", tmp_path / "out")
        result = mapper.copy_centroid_file(centroid)

        assert result.exists()
        assert result.name == "Centroid_PRIME001.txt"
        assert result.parent == tmp_path / "out" / "Prostate" / "Patient Files" / "PRIME001"

        text = result.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[0] == "PRIME001"
        assert lines[1] == "PRIME001"
        assert "12345678" not in text
        assert "SMITH" not in text
        # Data lines preserved
        assert lines[2] == "1.23 4.56 7.89"


# ---------------------------------------------------------------------------
# copy_trajectory_logs
# ---------------------------------------------------------------------------

class TestCopyTrajectoryLogs:
    def _setup_trajectory(self, tmp_path):
        """Create a patient dir and trajectory base dir with FX01/FX02."""
        patient_dir = tmp_path / "patient_12345678"
        patient_dir.mkdir()

        traj_base = tmp_path / "trajectory"
        for fx in ("FX01", "FX02"):
            fx_dir = traj_base / fx
            fx_dir.mkdir(parents=True)
            # MarkerLocations with PII path
            ml_text = (
                "Marker 1\n"
                f"C:\\data\\patient_12345678\\trajectory\\{fx}\\markers.dat\n"
                "1.0 2.0 3.0\n"
            )
            (fx_dir / "MarkerLocations.txt").write_text(ml_text, encoding="utf-8")
            (fx_dir / "MarkerLocationsGA.txt").write_text(ml_text, encoding="utf-8")
            # Non-PII files
            (fx_dir / "couchShifts.txt").write_text("0.1 0.2 0.3\n", encoding="utf-8")
            (fx_dir / "covOutput.txt").write_text("cov data\n", encoding="utf-8")
            (fx_dir / "Rotation.txt").write_text("rot data\n", encoding="utf-8")

        return patient_dir, traj_base

    def test_copy_trajectory_logs(self, tmp_path):
        """Files placed in correct Trajectory Logs structure."""
        patient_dir, traj_base = self._setup_trajectory(tmp_path)
        out = tmp_path / "out"

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", out)
        counts = mapper.copy_trajectory_logs(traj_base)

        assert counts["fx_count"] == 2
        # 5 files per FX (2 MarkerLocations + 3 plain) × 2 FXs = 10
        assert counts["files_copied"] == 10

        for fx in ("FX01", "FX02"):
            traj_dest = out / "Prostate" / "Trajectory Logs" / "PRIME001" / fx / "Trajectory Logs"
            assert traj_dest.is_dir()
            assert (traj_dest / "MarkerLocations.txt").exists()
            assert (traj_dest / "MarkerLocationsGA.txt").exists()
            assert (traj_dest / "couchShifts.txt").exists()
            assert (traj_dest / "covOutput.txt").exists()
            assert (traj_dest / "Rotation.txt").exists()

            # Treatment Records sibling created
            treat_dest = out / "Prostate" / "Trajectory Logs" / "PRIME001" / fx / "Treatment Records"
            assert treat_dest.is_dir()

    def test_trajectory_marker_locations_scrubbed(self, tmp_path):
        """patient_12345678 replaced with patient_PRIME001 in MarkerLocations."""
        patient_dir, traj_base = self._setup_trajectory(tmp_path)
        out = tmp_path / "out"

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", out)
        mapper.copy_trajectory_logs(traj_base)

        ml_path = (
            out / "Prostate" / "Trajectory Logs" / "PRIME001"
            / "FX01" / "Trajectory Logs" / "MarkerLocations.txt"
        )
        text = ml_path.read_text(encoding="utf-8")
        assert "patient_12345678" not in text
        assert "patient_PRIME001" in text

    def test_trajectory_no_pii_files_unchanged(self, tmp_path):
        """couchShifts, covOutput, Rotation copied byte-for-byte."""
        patient_dir, traj_base = self._setup_trajectory(tmp_path)
        out = tmp_path / "out"

        mapper = LearnFolderMapper(patient_dir, "PRIME001", "Prostate", out)
        mapper.copy_trajectory_logs(traj_base)

        for fname in ("couchShifts.txt", "covOutput.txt", "Rotation.txt"):
            src = traj_base / "FX01" / fname
            dest = (
                out / "Prostate" / "Trajectory Logs" / "PRIME001"
                / "FX01" / "Trajectory Logs" / fname
            )
            assert src.read_text() == dest.read_text()
