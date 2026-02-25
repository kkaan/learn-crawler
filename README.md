# learn-crawler

**Author:** Kaan

Utilities for interrogating and formatting patient CT, CBCT, SRO, structure set, and projection files from Elekta radiotherapy platforms. The tools and scripts here support the data requirements of the PRIME and LEARN clinical trials.

## Quick Start

```bash
# Install dependencies
pip install pydicom

# Run tests
python -m pytest tests/ -v

# Verify the package imports
python -c "from learn_upload.folder_sort import LearnFolderMapper; print('ok')"
```

## Network Share Considerations

When running the crawler on network shares (UNC paths), keep in mind:
- Prefer UNC paths (e.g. `\\server\share\XVI_COLLECTION`) over mapped drives if the process runs as a service account.
- Deep folder structures can exceed the Windows 260-character limit; the utilities now apply long-path prefixes, but Windows long-path support may still need to be enabled.
- Copy operations log and skip files if permissions or network errors occur, so review logs for missed files.

## `learn_upload` Package

The `learn_upload/` package automates the LEARN data transfer pipeline — transferring Elekta XVI CBCT patient data from GC (GenesisCare) to the USYD RDS research drive, replacing manual steps with Python scripts.

### Implemented Modules

| Module | Purpose | Status |
|--------|---------|--------|
| `config.py` | Shared configuration, paths, DICOM tag lists | Done |
| `utils.py` | INI parsing, XML parsing, ScanUID datetime, couch shift extraction | Done |
| `anonymise_dicom.py` | DICOM anonymisation (replaces manual MIM workflow) | Done |
| `folder_sort.py` | XVI export to LEARN directory structure mapping and file copying | Done |
| `treatment_notes.py` | Treatment_Notes.xlsx generation | Planned |
| `upload_workflow.py` | Interactive CLI wrapper for the full upload process | Planned |

### Usage: DICOM Anonymisation

Anonymises CT and plan DICOM files, replacing patient identifiers with a sequential ID (e.g. PAT01) while preserving all DICOM UIDs for referential integrity.

```python
from pathlib import Path
from learn_upload.anonymise_dicom import DicomAnonymiser

anonymiser = DicomAnonymiser(
    patient_dir=Path(r"E:\XVI_COLLECTION\processed\20230403_Flinders\patient_15002197"),
    anon_id="PAT01",
    output_dir=Path(r"E:\staging\patient_15002197"),
)

# Anonymise all CT and plan files
summary = anonymiser.anonymise_all()
print(summary)
# {'ct_count': 182, 'plan_count': 1, 'anon_id': 'PAT01'}
```

### Usage: Folder Mapping and File Sorting

Discovers XVI acquisition sessions, classifies them (CBCT / KIM Learning / KIM MotionView), assigns treatment fractions, creates the LEARN directory tree, and copies files to their correct locations.

```python
from pathlib import Path
from learn_upload.folder_sort import LearnFolderMapper

mapper = LearnFolderMapper(
    patient_dir=Path(r"E:\XVI_COLLECTION\processed\20230403_Flinders\patient_15002197"),
    anon_id="PAT01",
    site_name="Prostate",
    output_base=Path(r"E:\LEARN_OUTPUT"),
)

# Preview what will happen (creates directories, skips file copies)
summary = mapper.execute(dry_run=True)
print(summary)
# {'sessions': 12, 'fractions': 6, 'files_copied': {...}, 'dry_run': True}

# Run for real — copies all files
summary = mapper.execute(
    anon_ct_dir=Path(r"E:\staging\patient_15002197\CT_SET"),
    anon_plan_dir=Path(r"E:\staging\patient_15002197\DICOM_PLAN"),
    dry_run=False,
)
```

This produces the LEARN directory structure:

```
Prostate/
  Patient Files/PAT01/
  Patient Images/PAT01/
    FX0/
      CBCT/
        CBCT1/
          CBCT Projections/{CDOG, IPS}/    ← .his projection files
          Reconstructed CBCT/               ← .SCAN volume files
          Registration file/                ← .RPS.dcm
        CBCT2/ ...
      KIM-KV/{img_dirname}/                ← MotionView .his files
    FX1/ ...
  Patient Plans/PAT01/
    CT/              ← anonymised CT DICOM
    Plan/            ← anonymised plan DICOM
    Dose/
    Structure Set/
  Ground Truth/PAT01/
```

#### Session Types

The mapper classifies XVI acquisitions by their `AcquisitionPresetName` in `_Frames.xml`:

| Type | Preset example | Destination |
|------|---------------|-------------|
| CBCT | `4ee Pelvis Soft S20 179-181` | `.his` to `CBCT Projections/IPS/`, `.SCAN` to `Reconstructed CBCT/` |
| KIM Learning | `12aa KIM S20 R 34-181` | Same as CBCT (treated identically) |
| KIM MotionView | `13a KIM S20 MotionView` | `.his` to `KIM-KV/{img_dirname}/` |

#### Typical End-to-End Workflow

```python
from pathlib import Path
from learn_upload.anonymise_dicom import DicomAnonymiser
from learn_upload.folder_sort import LearnFolderMapper

patient = Path(r"E:\XVI_COLLECTION\processed\20230403_Flinders\patient_15002197")
staging = Path(r"E:\staging\patient_15002197")
output = Path(r"E:\LEARN_OUTPUT")

# Step 1: Anonymise DICOM files
anon = DicomAnonymiser(patient, "PAT01", staging)
anon.anonymise_all()

# Step 2: Map folders and copy files
mapper = LearnFolderMapper(patient, "PAT01", "Prostate", output)
summary = mapper.execute(
    anon_ct_dir=staging / "CT_SET",
    anon_plan_dir=staging / "DICOM_PLAN",
)
print(f"Copied {summary['sessions']} sessions across {summary['fractions']} fractions")
```

### Utility Functions

The `utils.py` module provides reusable parsing functions:

```python
from learn_upload.utils import (
    parse_xvi_ini,          # Parse Elekta XVI INI files for patient/scan metadata
    parse_scan_datetime,    # Extract datetime from ScanUID strings
    parse_frames_xml,       # Parse _Frames.xml for treatment ID, acquisition preset, kV/mA
    parse_couch_shifts,     # Extract couch shift values from INI text
    extract_ini_from_rps,   # Extract ZIP-embedded INI from RPS DICOM files
)
```

## Standalone Scripts

- [extract_elekta_rps_matrices.py](./extract_elekta_rps_matrices.py) -- Extract XVI RPS registration matrices
- [elektafdt_crawler.py](./elektafdt_crawler.py) -- List treatment plans in XVI export folders
- [extract_dicom_plans.py](./extract_dicom_plans.py) -- Generate CSV of patient plan names

## Documentation

- [GC Elekta Patient Upload Process](Docs/GC_Elekta_Patient_Upload_Process.md) - SOP for patient data transfer to LEARN
- [LEARN Upload Automation Plan](Docs/LEARN_Upload_Automation_Plan.md) - Full automation plan for the pipeline
- [Elekta XVI Reconstruction Directory Analysis](Docs/Elekta_XVI_Reconstruction_Directory_Analysis.md) - Directory and file breakdown
- [Elekta XVI RPS Format Documentation](Docs/elekta_rps_format_documentation.md) - RPS DICOM file format
- [Experimental Validation Notes](Docs/elekta_xvi_sro_experimental_validation.md) - Validation by phantom measurement

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific modules
python -m pytest tests/test_utils.py -v
python -m pytest tests/test_anonymise_dicom.py -v
python -m pytest tests/test_folder_sort.py -v
```

## Background: Target ROI Registration

The LEARN trial requires that the *Target* contour is visible in at least 180 degrees of x-ray projections during CBCT acquisition. The current markerless tracking model cannot be trained otherwise.

Plan:
- Verify export process from XVI as described
- Validate exported RPS/SRO objects with ground truth phantom measurements
- Expand the contour alignment tool by ImageX to use the SRO object to align contours to projections
- Use this to screen whether a patient is suitable for recruitment
