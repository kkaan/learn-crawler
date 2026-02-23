"""Tests for learn_upload.anonymise_dicom — DicomAnonymiser."""

from pathlib import Path

import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
import pytest

from learn_upload.anonymise_dicom import DicomAnonymiser


# ---------------------------------------------------------------------------
# Helper: create a minimal synthetic DICOM file
# ---------------------------------------------------------------------------

def _make_test_dicom(
    directory: Path,
    filename: str = "test.DCM",
    patient_name: str = "Doe^John",
    patient_id: str = "12345678",
    patient_birth_date: str = "19800101",
    institution_name: str = "Test Hospital",
    study_id: str = "STUDY1",
    patient_sex: str = "M",
    patient_age: str = "044Y",
    study_description: str = "CT Head",
) -> Path:
    """Create a minimal valid DICOM file for testing."""
    directory.mkdir(parents=True, exist_ok=True)
    filepath = directory / filename

    file_meta = pydicom.Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = FileDataset(str(filepath), {}, file_meta=file_meta, preamble=b"\x00" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # Tags that should be REPLACED with anon_id
    ds.PatientName = patient_name
    ds.PatientID = patient_id
    ds.StudyID = study_id

    # Tags that should be CLEARED
    ds.PatientBirthDate = patient_birth_date
    ds.AccessionNumber = "ACC001"
    ds.InstitutionName = institution_name
    ds.InstitutionAddress = "123 Test St"
    ds.ReferringPhysicianName = "Smith^Alice"
    ds.PhysiciansOfRecord = "Jones^Bob"
    ds.OperatorsName = "Operator^One"

    # Tags that should be PRESERVED
    ds.PatientSex = patient_sex
    ds.PatientAge = patient_age
    ds.StudyDescription = study_description

    # UIDs that must be preserved
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID

    ds.save_as(filepath)
    return filepath


# ---------------------------------------------------------------------------
# Tests: anonymise_file
# ---------------------------------------------------------------------------

class TestAnonymiseFile:
    def test_replaces_tags(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        dcm_path = _make_test_dicom(patient_dir / "CT_SET", "slice.DCM")
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        out = anon.anonymise_file(dcm_path)

        ds = pydicom.dcmread(out)
        assert str(ds.PatientName) == "PAT01"
        assert ds.PatientID == "PAT01"
        assert ds.StudyID == "PAT01"

    def test_clears_tags(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        dcm_path = _make_test_dicom(patient_dir / "CT_SET", "slice.DCM")
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        out = anon.anonymise_file(dcm_path)

        ds = pydicom.dcmread(out)
        assert ds.PatientBirthDate == ""
        assert ds.AccessionNumber == ""
        assert ds.InstitutionName == ""
        assert ds.InstitutionAddress == ""
        assert str(ds.ReferringPhysicianName) == ""
        assert str(ds.PhysiciansOfRecord) == ""
        assert str(ds.OperatorsName) == ""

    def test_preserves_uids(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        dcm_path = _make_test_dicom(patient_dir / "CT_SET", "slice.DCM")
        output_dir = tmp_path / "output"

        original = pydicom.dcmread(dcm_path)
        orig_study_uid = original.StudyInstanceUID
        orig_series_uid = original.SeriesInstanceUID
        orig_sop_uid = original.SOPInstanceUID

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        out = anon.anonymise_file(dcm_path)

        ds = pydicom.dcmread(out)
        assert ds.StudyInstanceUID == orig_study_uid
        assert ds.SeriesInstanceUID == orig_series_uid
        assert ds.SOPInstanceUID == orig_sop_uid

    def test_preserves_research_tags(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        dcm_path = _make_test_dicom(
            patient_dir / "CT_SET", "slice.DCM",
            patient_sex="F", patient_age="055Y", study_description="Pelvis RT",
        )
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT02", output_dir)
        out = anon.anonymise_file(dcm_path)

        ds = pydicom.dcmread(out)
        assert ds.PatientSex == "F"
        assert ds.PatientAge == "055Y"
        assert ds.StudyDescription == "Pelvis RT"

    def test_missing_optional_tag(self, tmp_path):
        """File without InstitutionName in DICOM_TAGS_CLEAR doesn't crash."""
        patient_dir = tmp_path / "patient_00000001"
        dcm_path = _make_test_dicom(patient_dir / "CT_SET", "slice.DCM")

        # Remove InstitutionName from the file before anonymising
        ds = pydicom.dcmread(dcm_path)
        del ds.InstitutionName
        ds.save_as(dcm_path)

        output_dir = tmp_path / "output"
        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        out = anon.anonymise_file(dcm_path)

        result = pydicom.dcmread(out)
        assert str(result.PatientName) == "PAT01"
        # InstitutionName should still be absent (not created)
        assert (0x0008, 0x0080) not in result


# ---------------------------------------------------------------------------
# Tests: anonymise_ct_set / anonymise_plan
# ---------------------------------------------------------------------------

class TestAnonymiseCtSet:
    def test_anonymises_all_files(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        _make_test_dicom(patient_dir / "CT_SET", "slice1.DCM")
        _make_test_dicom(patient_dir / "CT_SET", "slice2.dcm")  # lowercase ext
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        results = anon.anonymise_ct_set()

        assert len(results) == 2
        for p in results:
            ds = pydicom.dcmread(p)
            assert ds.PatientID == "PAT01"


class TestAnonymisePlan:
    def test_anonymises_all_files(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        _make_test_dicom(patient_dir / "DICOM_PLAN", "plan.DCM")
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        results = anon.anonymise_plan()

        assert len(results) == 1
        ds = pydicom.dcmread(results[0])
        assert ds.PatientID == "PAT01"


# ---------------------------------------------------------------------------
# Tests: anonymise_all
# ---------------------------------------------------------------------------

class TestAnonymiseAll:
    def test_summary_counts(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        _make_test_dicom(patient_dir / "CT_SET", "s1.DCM")
        _make_test_dicom(patient_dir / "CT_SET", "s2.DCM")
        _make_test_dicom(patient_dir / "DICOM_PLAN", "plan.DCM")
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT03", output_dir)
        summary = anon.anonymise_all()

        assert summary == {"ct_count": 2, "plan_count": 1, "anon_id": "PAT03"}


# ---------------------------------------------------------------------------
# Tests: edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_ct_set_dir(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        patient_dir.mkdir()
        output_dir = tmp_path / "output"

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        assert anon.anonymise_ct_set() == []

    def test_missing_patient_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            DicomAnonymiser(tmp_path / "nonexistent", "PAT01", tmp_path / "out")

    def test_output_dir_created(self, tmp_path):
        patient_dir = tmp_path / "patient_00000001"
        _make_test_dicom(patient_dir / "CT_SET", "s1.DCM")
        output_dir = tmp_path / "deeply" / "nested" / "output"

        assert not output_dir.exists()

        anon = DicomAnonymiser(patient_dir, "PAT01", output_dir)
        anon.anonymise_ct_set()

        assert output_dir.exists()
        assert (output_dir / "CT_SET" / "s1.DCM").exists()
