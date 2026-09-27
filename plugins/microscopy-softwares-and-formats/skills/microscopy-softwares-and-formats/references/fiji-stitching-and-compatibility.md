# Fiji compatibility and stitching EVOS tiles

## Compatibility summary

Fiji Bio-Formats can read the verified EVOS pixel data, OME XML, stage positions,
pixel calibration, and exposure metadata. The raw TIFFs are therefore usable in
Fiji. The important limitation is that the EVOS logical OME dimensions can
disagree with the physical per-channel file layout.

BigStitcher's recursive automatic file-list importer is fragile for that layout,
especially when a filename substring selects multiple ROIs or timestamped
acquisitions. Fiji's Grid/Collection Stitching plugin is a compatible alternative
when given an explicit, curated `TileConfiguration` derived from the EVOS stage
metadata.

## Recognizing the BigStitcher failure

The verified automatic import ended with:

```text
java.util.NoSuchElementException
  at java.util.HashMap$KeyIterator.next(...)
  at net.preibisch.mvrecon.fiji.datasetmanager.FileListDatasetDefinition.createDataset(...)
```

The outer `ExecutionException` and `Module threw exception` messages were only
wrappers. The useful frame was `FileListDatasetDefinition.createDataset`. In
this case it indicated that automatic grouping produced no consistent set of
views before an unchecked `iterator().next()` call.

Do not diagnose this stack trace as an out-of-memory error merely because the
dataset is large. First check for:

- single-plane files whose metadata advertises multiple channels;
- missing channel partners;
- multiple acquisitions with field numbers that restart;
- several ROIs combined by recursive search;
- an over-broad filename filter;
- an empty acquisition directory.

BigStitcher is not categorically incompatible with EVOS. Its automatic grouping
was incompatible with this uncurated export layout. A manually defined dataset
could also work, but explicit Grid/Collection configurations were simpler and
more auditable here.

## Fiji-native stitching pattern

Use the Fiji plugin for both registration and fusion. A Groovy script may only
prepare configurations, invoke Fiji, transfer coordinates between channels,
export, and validate.

1. Build a two-dimensional `TileConfiguration` for one ROI and one reference
   channel from `PositionX/Y` and `PhysicalSizeX/Y`.
2. Confirm the exact expected tile count and channel pairing before running the
   plugin.
3. Run `Grid/Collection stitching` with:
   - type `Positions from file`;
   - order `Defined by TileConfiguration`;
   - overlap computation enabled for the reference channel;
   - `Do not fuse images (only write TileConfiguration)` for the registration pass;
   - memory-saving computation for large mosaics.
4. Inspect the generated `.registered.txt` configuration.
5. Apply the chosen coordinates unchanged to other channels by changing only
   the channel filename token, such as `d0.TIF` to `d1.TIF`.
6. Fuse each channel using the same layout and `Linear Blending`, with overlap
   computation disabled during fusion.
7. Merge the fused grayscale images as channels of one calibrated hyperstack.
8. Export with the Fiji Bio-Formats exporter as LZW-compressed OME-TIFF.

Registering channels independently can create channel-to-channel geometric
misalignment. Prefer a structural channel such as DAPI for registration, but
verify that it contains usable overlap information.

## Disconnected registration graphs

Low-information or mostly black overlaps may prevent the Stitching plugin from
connecting every tile. In the verified plugin output, each disconnected
component received its own `(0.0, 0.0)` anchor. Multiple origins in the registered
configuration therefore indicated that components would collapse onto one
another or disappear if fused directly.

Use this decision rule:

- One origin: use the overlap-refined registered coordinates.
- Multiple origins: retain the original EVOS stage-position layout for the whole
  ROI and use it for every channel.

This fallback preserves all tiles and their commanded geometry; it does not
claim subpixel refinement across overlaps that contained insufficient signal.
Save both the initial and final configurations so the choice is auditable.

## Headless execution details

A typical invocation is:

```bash
fiji --ij2 --headless --memory 10g --run script.groovy
```

Observed details worth preserving:

- Calling the Bio-Formats export menu command headlessly can open an AWT-only
  options dialog. `loci.plugins.LociExporter` with `windowless=true` avoids it.
- The Bio-Formats exporter did not reliably truncate an existing destination in
  this mode. Replace only the known generated output immediately before export;
  never delete source TIFFs or a broad directory.
- Plugin-discovery and Java reflective-access warnings can coexist with a
  successful run. Judge success from exit state, explicit script checks, and
  output validation, not from the mere presence of warnings.

## Required validation

After each ROI, reopen the OME-TIFF with Bio-Formats and verify:

- nonzero file size and readable pixels;
- expected mosaic width and height;
- channel count, order, and semantic labels;
- `Z=1` and `T=1` when that is the acquisition;
- 16-bit storage and expected raw numeric range;
- physical pixel calibration;
- identical dimensions and alignment across channels;
- no duplicated origin tiles, missing components, or obvious seam failure.

Generate separate DAPI and GFP previews plus a composite. Fiji's default
composite LUT may display channel 1 as red even when it is DAPI. State channel
order explicitly and never infer identity from the preview color.
