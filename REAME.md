# Project Purpose

This repository contains utilities for interrogating and formatting patient CT, CBCT, SRO, structure set, and projection files from Elekta radiotherapy platforms. The tools and scripts here support the data requirements of the LEARN and PRIME clinical trials.

## Repository Focus

- Efficient extraction and annotation of plan information (see: [`elektafdt_crawler.py`](./elektafdt_crawler.py))
- Verification, parsing, and manipulation of Elekta XVI CBCT *Reconstruction* directories, including all clinical metadata, projections, and reconstructed volumes (see: `Docs/Elekta_XVI_Reconstruction_Directory_Analysis.md`)
- Utility scripts for checking coverage of the target in projection data, and for evaluating CBCT acquisition suitability for markerless tracking
- Formatting outputs for downstream analysis in the LEARN and PRIME projects

## How to Use

- Use the provided scripts to extract basic plan and patient structure information into CSV files for overview and batch checks.
- Run directory and projection analysis scripts to verify angular coverage and data completeness per patient.

See the documentation in `Docs/` and example scripts in the main repository for more detailed workflows.

---

## Steps towards registering Target ROI to projection images (.his files)

##### LEARN Trial Special Requirement

A key requirement for the LEARN Trial is to ensure the radiotherapy *target* is visible in at least 180 degrees of x-ray projections during CBCT acquisition. The current markerless tracking model developed for the trial cannot be trained if this is not the case.

A script is needed to allow us to easily screen patients for recruitment based on this criteria. This is the plan:

- Verify export process from XVI as described
- Validate the exported RPS file objects and the SRO objects contained with ground truth phantom measurements. 
- Expand the contouralignment tool by ImageX to use the RSO object to align the contour to the projections. 
- Use this to screen whether patient is suitable for recruitment.




