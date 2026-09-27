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
  [references/evos-m7000-exports.md](references/evos-m7000-exports.md).
- For Fiji import failures, BigStitcher errors, tile registration, fusion, or
  OME-TIFF export, read
  [references/fiji-stitching-and-compatibility.md](references/fiji-stitching-and-compatibility.md).
- For quantitative fluorescence comparisons, masks, channel semantics, or
  exclusion of autofluorescent/acellular tissue, read
  [references/fiji-quantitative-fluorescence.md](references/fiji-quantitative-fluorescence.md).

## Core workflow

1. Inventory acquisition directories and parse filenames before opening files
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
   Save separate-channel previews as well as a composite.

## Guardrails

- Keep raw TIFFs read-only. Write configurations, mosaics, previews, masks, and
  reports to a separate output tree.
- Preserve raw intensity values during quantitative work. Contrast enhancement
  belongs only in previews unless an analysis explicitly defines otherwise.
- Treat LUT colors as display metadata, not channel identity.
- Distinguish an image-level comparison from a biological-replicate test.
- Record software versions and all assumptions that were inferred rather than
  encoded in the dataset.

The detailed references describe behavior verified with Fiji/ImageJ2
2.16.0/1.54p, Java 11, Bio-Formats 8.1.1, Grid/Collection Stitching 3.1.9, and
BigStitcher 2.5.0. Recheck menu names and headless behavior after version changes.
