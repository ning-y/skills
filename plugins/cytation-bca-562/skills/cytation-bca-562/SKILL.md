---
name: cytation-bca-562
description: Parse Cytation 562-nm BCA Excel files into PDF reports.
version: 0.5.0
author: ning, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [cytation, bca, absorbance, 562-nm, xlsx, 4pl, pdf]
    category: productivity
    related_skills: [xlsx, pdf]
---

# Cytation BCA 562-nm Report Skill

Use this skill when the user supplies an Excel workbook that is clearly a BioTek Cytation5/Cytation 5 export for a 96-well absorbance-endpoint read at 562 nm. Parse the raw plate and produce a portrait A4 PDF with a raw-absorbance plate table, an ascending 4PL calibration plot, and a matching 8×12 plate table containing inverse-4PL concentrations. The helper script performs deterministic validation, fitting, rendering, and PDF creation.

## When to Use

- Trigger for a machine-generated `.xlsx` workbook whose metadata identifies a Cytation5/Cytation 5 reader, a 96-well plate, an absorbance endpoint, and a 562-nm read.
- Accept a multi-wavelength workbook only when the 562-nm plate block is uniquely identifiable.
- Reject a workbook when reader, plate format, absorbance endpoint, or 562-nm identification is missing/ambiguous, when more than one valid plate sheet exists, or when a required well is missing/non-numeric.
- Do not silently convert blanks or error cells to zero.

## Prerequisites

- Python 3.10+ with `openpyxl`, `numpy`, `scipy`, `matplotlib`, and `pypdf` available in the active Hermes Python environment.
- For report delivery, load `sending-files` before emitting a `MEDIA:` tag; Python read/write the final PDF to a clean path first.

## How to Run

Run the bundled helper through `terminal`:

```bash
python3 /data/.hermes/skills/productivity/cytation-bca-562/scripts/cytation_bca_report.py INPUT.xlsx --output REPORT.pdf [--standards C1:D8] [--exclude A1:B12] [--concentrations 2000,1500,1000,750,500,250,125,0]
```

Optional CLI flags:
- `--standards` / `-s`: Standard coordinate range or pairs (default: `A1:B8`, matching A1+B1 through A8+B8; supports e.g. `C1:D8` for plate reuse).
- `--exclude` / `-e`: Explicit wells or ranges to exclude (e.g. `A1:B12,D1`), rendering them blank on the concentration map. Blank/empty wells with OD < 0.065 remain automatically excluded by default.
- `--concentrations` / `-c`: Comma-separated nominal standards in µg/mL (default: `2000,1500,1000,750,500,250,125,0`).

If the system Python lacks the dependencies, invoke the active Hermes venv Python instead. The script prints a JSON result containing the output path, detected sheet, fitted parameters, fit metrics, and warning flags.

## Quick Reference

```text
Standard coordinates: A1+B1, A2+B2, ..., A8+B8 (or parameterized via --standards / -s, e.g. C1:D8)
Excluded wells: parameterized via --exclude / -e (e.g. A1:B12, D1); optical baseline < 0.065 OD auto-blanked
Nominal standards: 2, 1.5, 1, 0.75, 0.5, 0.25, 0.125, 0 µg/µL (or parameterized via --concentrations / -c)
4PL: bottom + (top-bottom)/(1 + (EC50/x)^HillSlope)
Fit: eight duplicate means, equal weighting, raw absorbance, no blank subtraction
Inverse: every well, including standards
PDF: A4 portrait; raw plate table, curve, estimated-concentration plate table
```

## Procedure

1. Locate the incoming workbook and invoke `scripts/cytation_bca_report.py` with an explicit output path. The command must exit successfully and emit JSON.
2. Validate the metadata: reader text contains `cytation5` or `cytation 5`; the workbook identifies a 96-well plate; the read is an absorbance endpoint; and a unique 562-nm block can be selected. Reject ambiguity instead of guessing.
3. Locate exactly one 8×12 numeric plate block. Map rows A–H and columns 1–12 to well coordinates; use A1+B1 through A8+B8 as duplicate standards in the fixed order above. Required values must be finite numbers.
4. Calculate each standard mean and sample SD (`n−1`, two replicates). Fit all four 4PL parameters freely by unweighted nonlinear least squares in µg/µL, using the zero-standard convention `y(0) = bottom`. Print bottom, top, EC50, Hill slope, R², and RMSE from the eight means on the raw absorbance scale.
5. Assess monotonicity separately: expected standard means increase from the blank toward the 2-µg/µL standard. Fit and retain non-monotonic data, but flag the calibration. Do not impose parameter constraints. Flag a nonphysical fit if its parameters do not represent a normal ascending curve. If a free fit has an unidentifiable upper plateau/EC50 ridge, retain the free-fit result and report that limitation rather than silently bounding or fixing parameters.
6. Invert the fitted model for every well. Return finite mathematical extrapolations and mark them `⚠` for non-standard wells. For absorbance below the fitted bottom or above the fitted top, report the signed inverse-odds diagnostic (negative below bottom, positive above top), retaining the numeric value. A1:B8 are calibration sanity-check wells: show their inverse estimates without `⚠` even when noise puts them slightly outside the nominal range. The separate flags block lists only flagged non-standard wells and their reasons.
7. Render the PDF on A4 portrait pages: (i) metadata header, an 8×12 plate table with raw absorbance values, preserved source decimal precision, continuous white-to-blue min–max scaling, and legend; (ii) a linear x-axis 0–2 µg/µL plot with duplicate points, mean points, sample-SD error bars when nonzero, fitted curve, and fit summary; (iii) an 8×12 plate table in the same geometry and coordinate labelling as section 1, with inverse-4PL estimates in each cell, three decimal places for finite concentrations, a concentration colour scale, and flags shown in the cells plus a flags block. Include no dilution correction.
8. Verify the output with `pypdf` by checking it opens, has expected pages, and contains the three section headings, source filename, well coordinates, fitted parameters, and concentration table text. Only then deliver the cleaned PDF.

## Pitfalls

- Cytation exports often put metadata above the plate, row labels in column B, readings in C:N, and a repeated wavelength in column O; do not assume the plate starts at a fixed row, and do not treat the wavelength column as a well.
- The zero standard cannot be evaluated by the literal 4PL expression; use the explicit limiting convention in the procedure.
- “Raw” means no subtraction of the blank from either standards or samples.
- Standards are supplied as 2000–0 µg/mL in the instrument convention but are converted to 2–0 µg/µL for fitting/reporting.
- The example’s blue fills are discrete; the report must use a continuous white-to-blue scale based on the valid plate minimum and maximum.
- Preserve raw absorbance display precision, but format estimated concentrations to three decimal places.
- Section iii mirrors section 1's 8×12 plate geometry and coordinate labels; use `⚠` in flagged non-standard concentration cells and a separate well-by-well flags block. A1:B8 are never caution-marked because they are calibration sanity checks. Negative concentration entries are signed fit diagnostics, not physical concentrations.
- A non-monotonic standard series is a warning, not an automatic rejection. A missing/non-numeric reading is an input error.
- A free four-parameter fit can legitimately be ill-conditioned when the observed standard range does not reach the upper plateau; this is reported as an identifiability warning, not silently repaired with assay-specific bounds.

## Verification

- Run the helper on the supplied example and inspect its JSON result.
- Open the output with `pypdf`, confirm it has the expected page count, and extract text from every page.
- Render the PDF pages to PNG with the PDF skill’s page-image helper when visual layout inspection is needed, then inspect with `vision_analyze`.
- Confirm the report contains 96 well coordinates and raw readings in section 1, 96 concentration entries (or explicit `N/A`) in the section-3 plate table, all eight standard means, and the fit summary before delivery.
