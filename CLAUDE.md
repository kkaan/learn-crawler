# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Purpose

This repository contains tools for analyzing Elekta XVI CBCT (Cone Beam Computed Tomography) medical imaging data from VersaHD linear accelerators. The primary focus is extracting treatment plan information from XVI system exports for clinical workflow analysis and research applications.

## Core Architecture

### Primary Script: `extract_dicom_plans.py`
A specialized data extraction tool that:
- Traverses patient directories following XVI export structure
- Parses `_Frames.xml` files to extract treatment plan names
- Generates CSV reports for clinical data analysis
- Handles missing data gracefully with comprehensive error logging

### Data Structure Understanding
The XVI system exports follow a specific hierarchical structure:
```
patient_XXXXXXXX/
├── IMAGES/
│   └── img_[UID]/
│       ├── _Frames.xml          # Contains treatment plan metadata
│       ├── *.his files          # CBCT projection images
│       └── Reconstruction/      # Reconstructed volume data
├── DICOM_PLAN/                  # Treatment planning DICOM files
└── CT_SET/                      # Reference CT images
```

Key XML parsing target: `<Treatment><ID>` tag in `_Frames.xml` files contains the actual treatment plan names (not generic DICOM filenames).

## Dependencies and Environment

### Required Python Packages
- `pydicom` (version 3.0.1+) - For DICOM file handling if needed
- Built-in libraries: `xml.etree.ElementTree`, `pathlib`, `csv`, `logging`

### Installation Commands
```bash
pip install pydicom
```

### Data Access Configuration
The repository includes Claude Code permissions for specific XVI data paths:
- Network paths: `\\GC04PRBAK02\elekta_fdt\XVI_COLLECTION\processed\20230403_Flinders`
- Mapped drives: `E:\XVI_COLLECTION\processed\20230403_Flinders`

## Execution Commands

### Run Treatment Plan Extraction
```bash
python extract_dicom_plans.py
```

This will:
1. Scan all `patient_*` directories in the configured base path
2. Extract treatment plan names from `_Frames.xml` files
3. Generate `patient_dicom_plans.csv` with Patient_Directory,Plan_Name columns
4. Provide detailed logging and processing summary

### Modify Data Source Path
Edit the `base_directory` variable in `extract_dicom_plans.py` main() function:
```python
base_directory = r"E:\XVI_COLLECTION\processed\20230403_Flinders"
```

## Medical Imaging Context

### Clinical Workflow Integration
- **IGRT (Image-Guided Radiotherapy)**: Patient positioning verification before treatment
- **Treatment Planning**: Links to radiotherapy treatment plans via UIDs
- **Quality Assurance**: Systematic data extraction for clinical audits
- **Research Applications**: Bulk analysis of imaging protocols and treatment patterns

### Data Types Handled
- **CBCT Acquisitions**: Cone beam CT scans for patient positioning
- **Treatment Plans**: Radiotherapy planning data with descriptive names
- **Projection Data**: Raw X-ray projections (`.his` files)
- **Reconstruction Parameters**: XVI system configuration and settings

### Key Technical Concepts
- **UIDs**: Unique identifiers linking imaging sessions to treatment plans
- **IEC 61217**: Coordinate system standards for radiotherapy equipment
- **XVI System**: Elekta's kV imaging platform for CBCT acquisition
- **Treatment Fractions**: Individual treatment delivery sessions

## Output Data Structure

### Generated CSV Format
```
Patient_Directory,Plan_Name
patient_15002197,WholeBrain-C2Retrt
patient_19001415,BilatPelvis
patient_19003538,ProstateBed
```

### Common Treatment Plan Names
- Anatomical sites: Prostate, Breast, Lung, Brain, Pelvis
- Treatment modifiers: Nodes, Bed, Retrt (retreatment)
- Specific techniques: VMAT, IMRT, SRS planning approaches

## Error Handling Patterns

The codebase implements robust error handling for:
- Missing patient directories or IMAGES folders
- Absent `_Frames.xml` files or malformed XML
- Network path access issues
- Unicode encoding problems in patient data

When extending functionality, maintain this error-tolerant approach since medical data often has inconsistencies.

## Documentation Reference

See `Elekta_XVI_Reconstruction_Directory_Analysis.md` for comprehensive technical documentation of XVI data structure, file formats, and clinical workflow context. This document provides essential background for understanding the medical imaging pipeline and data relationships.