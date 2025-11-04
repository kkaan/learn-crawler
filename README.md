# learn-crawler things
**Author:** Kaan


Aspirational statement: This repository will contain utilities for interrogating and formatting patient CT, CBCT, SRO, structure set, and projection files from Elekta radiotherapy platforms. The tools and scripts here support the data requirements of the PRIME and LEARN clinical trials.

## Repository Contains

- [extract_elekta_rps_matrices.py](./extract_elekta_rps_matrices.py) – Extract XVI RPS matrices
- [elektafdt_crawler.py](./elektafdt_crawler.py) – List plans in XVI export folders
- [Elekta XVI Reconstruction Directory Analysis](Docs/Elekta_XVI_Reconstruction_Directory_Analysis.md) - Directory and file breakdown
- [Elekta XVI RPS Format Documentation](Docs/elekta_rps_format_documentation.md) - RPS DICOM file format
- [Experimental Validation Notes](Docs/elekta_xvi_sro_experimental_validation.md) - Validation by phantom measurement


## Steps towards registering Target ROI to projection images (.his files)

##### LEARN Trial Requirement

*Target* contour should visible in at least 180 degrees of x-ray projections during CBCT acquisition. The current markerless tracking model developed for the trial cannot be trained if this is not the case.

A script is needed to allow us to easily screen patients for recruitment based on this criteria. This is the plan:

- Verify export process from XVI as described
- Validate the exported RPS file objects and the SRO objects contained with ground truth phantom measurements. 
- Expand the contouralignment tool by ImageX to use the RSO object to align the contour to the projections. 
- Use this to screen whether patient is suitable for recruitment.




