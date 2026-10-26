---
name: microscopy-softwares-and-formats
description: Inspect, diagnose, stitch, and quantify microscopy datasets involving Fiji/ImageJ, Bio-Formats, BigStitcher, Grid/Collection Stitching, or EVOS M7000 TIFF exports. Use when file naming, OME metadata, stage coordinates, channel pairing, tile registration, or quantitative fluorescence compatibility matters.
---

# Microscopy Software and Formats

Treat microscope filenames, physical planes, and embedded metadata as separate
claims that must agree before importing or stitching. Preserve source images and
make the transformation from acquisition layout to Fiji inputs reproducible.

## Route the task

- For an EVOS M7000 export, first read
  [references/evos-m7000-exports.md](references/evos-m7000-exports.md). To turn
  a raw scan directory into one stitched TIFF, use
  `scripts/stitch_evos_m7000.py` as described there.
- For scans stored on Dropbox, read
  [references/dropbox-rclone.md](references/dropbox-rclone.md). Dropbox access
  is strictly read-only; download to a local scratch tree before stitching.
- For Fiji import failures, BigStitcher errors, tile registration, fusion, or
  OME-TIFF export, read
  [references/fiji-stitching-and-compatibility.md](references/fiji-stitching-and-compatibility.md).
- For quantitative fluorescence comparisons, masks, channel semantics, or
  exclusion of autofluorescent/acellular tissue, read
  [references/fiji-quantitative-fluorescence.md](references/fiji-quantitative-fluorescence.md).

## Core workflow

1. Honor an explicit local destination; otherwise use a task-named directory
   under the current working directory. Announce its exact absolute path before
   downloads, extraction, or processing; never invent a home-root folder.
   Inventory acquisition directories and parse filenames before opening files
   recursively. Determine the acquisition, field, ROI, channel, Z, and time axes
   from both names and metadata.
2. Open representative files with Fiji Bio-Formats and compare the reported
   dimensions with the physical file layout. Do not trust `SizeC`, `SizeZ`, or
   `SizeT` without checking how many planes are actually present.
3. Verify channel pairs and expected tile counts before registration. Never mix
   field numbers from different acquisition directories merely because their
   sample-name substring matches.
4. Prefer Fiji-native import, registration, fusion, and export operations. A
   small script may translate verified microscope metadata into a documented
   Fiji input format; it must not reimplement the stitcher.
5. Register one structurally informative channel, normally DAPI, and apply the
   same final tile coordinates to all other channels.
6. Validate the result numerically and visually: dimensions, channel count and
   order, bit depth, calibration, missing/duplicated tiles, seams, and alignment.
   Use `scripts/verify_evos_stitch.py` for bounded decoding, metadata checks, and
   previews, not proof that all pixels are correct. Manually inspect informative
   overlaps and every partition boundary; save separate-channel previews and a
   composite. Report checks actually performed separately from conclusions.
   Example: `python3 scripts/verify_evos_stitch.py /absolute/output.ome.tif
   --preview-dir /absolute/task/evidence/qc`. CLI metadata checks establish
   self-consistency, not agreement with trusted expected dimensions/calibration;
   use the API's expected values for that comparison.

## Guardrails

- Keep raw data unchanged; make downloaded/extracted raw files read-only, not
  directories recursively unwritable. Keep raw input, managed scratch, and final
  outputs separate. Clean only task-owned scratch after validation, retaining
  small logs/configurations/verification evidence; never automatically delete
  sources or user-owned files.
- Treat every Dropbox/rclone remote as strictly read-only: never upload, move,
  delete, or modify remote files. Download to local scratch before analysis.
  For ZIP inputs, follow the safe inventory/extraction recipe in the Dropbox
  reference; do not extract blindly.
- Do not normalize scientific outputs for display. Contrast enhancement belongs
  only in previews unless an analysis explicitly defines otherwise. Blending
  and resampling produce derived intensities, not unchanged source pixels.
- Never rerun a user-reported failing command merely to confirm the failure;
  use the reported evidence to diagnose it.
- Treat LUT colors as display metadata, not channel identity.
- Distinguish an image-level comparison from a biological-replicate test.
- Record software versions and all assumptions that were inferred rather than
  encoded in the dataset.

The detailed references describe behavior verified with Fiji/ImageJ2
2.16.0/1.54p, Java 11, Bio-Formats 8.1.1, Grid/Collection Stitching 3.1.9, and
BigStitcher 2.5.0. Recheck menu names and headless behavior after version changes.
