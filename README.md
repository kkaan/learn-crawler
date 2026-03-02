# learn-crawler

**Author:** Kaan

Utilities for interrogating and formatting patient CT, CBCT, SRO, structure set, and projection files from Elekta radiotherapy platforms. The tools and scripts here support the data requirements of the PRIME and LEARN clinical trials.

## Quick Start

```bash
# Install dependencies
pip install pydicom PyQt6

# Launch the GUI wizard
python -m learn_upload

# Run tests
python -m pytest tests/ -v

# Verify the package imports
python -c "from learn_upload.folder_sort import LearnFolderMapper; print('ok')"
```

## Repository Layout

| Directory | Description |
|-----------|-------------|
| `learn_upload/` | Core Python package -- anonymisation, folder sorting, PII verification, GUI |
| `cbct-shifts/` | CBCT shift analysis scripts (Mosaiq vs RPS comparison, patient reports) |
| `scripts/` | Standalone CLI tools (RPS matrix extraction, DICOM tag reader, XVI crawler) |
| `examples/` | Pipeline usage examples |
| `tests/` | pytest test suite for `learn_upload` |
| `Docs/` | SOP documentation, automation plan, format specs |
| `Data/` | Sample/reference data files |

## `learn_upload` Package

The `learn_upload/` package automates the LEARN data transfer pipeline -- transferring Elekta XVI CBCT patient data from GC (GenesisCare) to the USYD RDS research drive, replacing manual steps with Python scripts.

### Modules

| Module | Purpose |
|--------|---------|
| `config.py` | Shared configuration, paths, DICOM tag lists, logging setup |
| `utils.py` | INI parsing, XML parsing, ScanUID datetime extraction, couch shift parsing |
| `anonymise_dicom.py` | DICOM anonymisation (replaces manual MIM workflow) |
| `folder_sort.py` | XVI export to LEARN directory structure mapping and file copying |
| `verify_pii.py` | Post-anonymisation scan for residual patient-identifiable data |
| `gui_qt.py` | PyQt6 desktop GUI wrapping all pipeline steps |

### Using the GUI

```bash
python -m learn_upload
```

This launches a 6-step wizard:

1. **Configuration** -- set paths, anonymised ID (PATxx), and PII search strings
2. **Data Preview** -- discover XVI sessions and preview fraction assignments
3. **Folder Sort** -- copy files into the LEARN directory structure
4. **Anonymise** -- run DICOM anonymisation with per-file progress
5. **PII Verification** -- scan output for residual patient data
6. **CBCT Shift Report** -- generate a markdown report of CBCT registration shifts

See the **[GUI Walkthrough](Docs/GUI_Walkthrough.md)** for a step-by-step guide with screenshots.

### Using the Python API (no GUI)

#### DICOM Anonymisation

```python
from pathlib import Path
from learn_upload.anonymise_dicom import DicomAnonymiser

anonymiser = DicomAnonymiser(
    patient_dir=Path(r"E:\XVI_COLLECTION\processed\20230403_Flinders\patient_15002197"),
    anon_id="PAT01",
    output_dir=Path(r"E:\staging\patient_15002197"),
)

summary = anonymiser.anonymise_all()
print(summary)
# {'ct_count': 182, 'plan_count': 1, 'anon_id': 'PAT01'}
```

#### Folder Mapping and File Sorting

```python
from pathlib import Path
from learn_upload.folder_sort import LearnFolderMapper

mapper = LearnFolderMapper(
    patient_dir=Path(r"E:\XVI_COLLECTION\processed\20230403_Flinders\patient_15002197"),
    anon_id="PAT01",
    site_name="Prostate",
    output_base=Path(r"E:\LEARN_OUTPUT"),
)

# Preview (dry run)
summary = mapper.execute(dry_run=True)

# Run for real
summary = mapper.execute(
    anon_ct_dir=Path(r"E:\staging\patient_15002197\CT_SET"),
    anon_plan_dir=Path(r"E:\staging\patient_15002197\DICOM_PLAN"),
    dry_run=False,
)
```

#### PII Verification

```python
from pathlib import Path
from learn_upload.verify_pii import verify_no_pii

findings = verify_no_pii(
    directory=Path(r"E:\LEARN_OUTPUT\Prostate\Patient Plans\PAT01"),
    pii_strings=["12345678", "SMITH", "JOHN"],
)
if findings:
    print("PII detected!")
```

#### Full End-to-End Pipeline

```python
from pathlib import Path
from learn_upload.anonymise_dicom import DicomAnonymiser
from learn_upload.folder_sort import LearnFolderMapper
from learn_upload.verify_pii import verify_no_pii

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

# Step 3: Verify no residual PII
findings = verify_no_pii(output / "Prostate" / "Patient Plans" / "PAT01", ["12345678"])
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

### Session Types

The mapper classifies XVI acquisitions by their `AcquisitionPresetName` in `_Frames.xml`:

| Type | Preset example | Destination |
|------|---------------|-------------|
| CBCT | `4ee Pelvis Soft S20 179-181` | `.his` to `CBCT Projections/IPS/`, `.SCAN` to `Reconstructed CBCT/` |
| KIM Learning | `12aa KIM S20 R 34-181` | Same as CBCT (treated identically) |
| KIM MotionView | `13a KIM S20 MotionView` | `.his` to `KIM-KV/{img_dirname}/` |

### Expected Input: XVI Export Directory

The tool expects an Elekta XVI export with this structure:

```
patient_XXXXXXXX/
  IMAGES/
    img_<UID>/                     <- one directory per acquisition session
      _Frames.xml                  <- treatment ID, acquisition preset, kV/mA
      *.his                        <- raw X-ray projections
      Reconstruction/
        *.INI or *.INI.XVI         <- ScanUID (embeds datetime), patient metadata
        *.SCAN                     <- reconstructed CBCT volume
        *.SCAN.MACHINEORIENTATION  <- machine orientation metadata
        *.RPS.dcm                  <- registration DICOM (couch shifts, matrices)
    img_<UID>/  ...                <- additional sessions
  CT_SET/                          <- reference CT DICOM (optional, for anonymisation)
    *.dcm
  DICOM_PLAN/                      <- treatment plan DICOM (optional, for anonymisation)
    *.dcm
```

The `IMAGES/` subdirectory name is configurable (`images_subdir` parameter); the default is `"IMAGES"`.

### Optional Inputs

These are passed via the GUI or Python API when available:

| Input | Description |
|-------|-------------|
| **TPS Export directory** | Contains `DICOM CT Images/`, `DICOM RT Plan/`, `DICOM RT Structures/`, `DICOM RT Dose/` subdirectories from Monaco/TPS export |
| **Trajectory log directory** | Contains `FX01/`, `FX02/`, ... subdirectories with `MarkerLocations*.txt` files |
| **Centroid file** | Single `.txt` file (e.g. `Centroid_12345678_BeamID_1.1_1.2.txt`); MRN in filename is replaced with anon ID during copy |

### Output: LEARN Directory Structure

```
<site_name>/                                  e.g. "Prostate"
  Patient Files/<anon_id>/
    cbct_shift_report.md                      <- generated CBCT shift report
    Centroid_<anon_id>_BeamID_*.txt           <- anonymised centroid file (if provided)
  Patient Images/<anon_id>/
    FX1/                                      <- fractions numbered chronologically
      CBCT/
        CBCT1/                                <- multiple CBCTs per fraction if same-day
          CBCT Projections/
            CDOG/
            IPS/
              *.his                           <- projection files
              _Frames.xml
          Reconstructed CBCT/
            *.SCAN                            <- volume files
            *.INI / *.INI.XVI                 <- metadata
          Registration file/
            *.RPS.dcm                         <- registration DICOM
        CBCT2/ ...
      KIM-KV/                                 <- MotionView sessions (if present)
        img_<UID>/
          *.his
          _Frames.xml
    FX2/ ...
  Patient Plans/<anon_id>/
    CT/                                       <- anonymised CT DICOM
    Plan/                                     <- anonymised plan DICOM
    Dose/                                     <- anonymised dose DICOM (if provided)
    Structure Set/                            <- anonymised structure DICOM (if provided)
  Ground Truth/<anon_id>/
  Trajectory Logs/<anon_id>/                  <- only if trajectory logs provided
    FX01/
      Trajectory Logs/
        MarkerLocations*.txt
      Treatment Records/
    FX02/ ...
```

## CBCT Shift Analysis

The `cbct-shifts/` directory contains scripts for comparing XVI RPS registration data with Mosaiq CBCT shift records:

- `compare_rps_mosaiq.py` -- matches RPS DICOM registrations to Mosaiq log entries by date/time and prints a side-by-side 6-DOF comparison
- `report_patient_details.py` -- generates patient-level CBCT shift reports

## Standalone Scripts

| Script | Description |
|--------|-------------|
| [`scripts/extract_elekta_rps_matrices.py`](scripts/extract_elekta_rps_matrices.py) | Extract XVI RPS registration matrices and alignment data from `.RPS.dcm` files |
| [`scripts/read_dicom_tags.py`](scripts/read_dicom_tags.py) | Read and display DICOM tags from any `.dcm` file |
| [`scripts/elektafdt_crawler.py`](scripts/elektafdt_crawler.py) | Crawl XVI export directories and list treatment plans from `_Frames.xml` |

## Examples

See [`examples/run_patient_example.py`](examples/run_patient_example.py) for a complete end-to-end pipeline example using `DicomAnonymiser`, `LearnFolderMapper`, and `verify_no_pii`.

## Documentation

- **[GUI Walkthrough](Docs/GUI_Walkthrough.md)** -- step-by-step guide to the LEARN Pipeline GUI with screenshots
- [GC Elekta Patient Upload Process](Docs/GC_Elekta_Patient_Upload_Process.md) -- SOP for patient data transfer to LEARN
- [LEARN Upload Automation Plan](Docs/LEARN_Upload_Automation_Plan.md) -- full automation plan for the pipeline
- [Elekta XVI Reconstruction Directory Analysis](Docs/Elekta_XVI_Reconstruction_Directory_Analysis.md) -- directory and file breakdown
- [Elekta XVI RPS Format Documentation](Docs/elekta_rps_format_documentation.md) -- RPS DICOM file format and coordinate mapping
- [Experimental Validation Notes](Docs/elekta_xvi_sro_experimental_validation.md) -- validation by phantom measurement

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Specific modules
python -m pytest tests/test_utils.py -v
python -m pytest tests/test_anonymise_dicom.py -v
python -m pytest tests/test_folder_sort.py -v
python -m pytest tests/test_verify_pii.py -v
```

## Background: Target ROI Registration

The LEARN trial requires that the *Target* contour is visible in at least 180 degrees of x-ray projections during CBCT acquisition. The current markerless tracking model cannot be trained otherwise.

Plan:
- Verify export process from XVI as described
- Validate exported RPS/SRO objects with ground truth phantom measurements
- Expand the contour alignment tool by ImageX to use the SRO object to align contours to projections
- Use this to screen whether a patient is suitable for recruitment
