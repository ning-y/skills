# EVOS M7000 TIFF exports

## Scope

These observations were verified on one EVOS M7000 tile-scan export. Treat them
as a recognition pattern, not a universal specification: inspect representative
files from every new acquisition before relying on them.

## Observed directory and filename layout

An acquisition was stored in a timestamped directory such as:

```text
scan.2026-09-25-05-49-50/
```

Files followed this pattern:

```text
scan_<sample>_R_p00_0_A01f<field>d<channel>.TIF
```

For the verified dataset:

- `f65` meant field 65.
- `d0` was DAPI and `d1` was GFP.
- Each field/channel combination was a separate physical TIFF.
- Field numbering restarted in a later timestamped acquisition.
- Several ROIs occupied contiguous field-number ranges within one acquisition,
  but the TIFF filename did not reliably encode the ROI boundary. A manifest was
  required to map acquisition plus field range to ROI.
- An acquisition/ROI entry could exist without any TIFFs.

A directory can also contain `scan_<slide>_M_p...` files, which are RGB merged
previews rather than raw tiles. Ignore them when counting or stitching raw
tiles.

Never generalize the `d0`/`d1` channel meaning or ROI ranges without checking the
new dataset. Pair channels by acquisition and field number, then confirm their
metadata and appearance.

## Metadata mismatch that matters

Each verified TIFF contained one physical image plane, while its embedded OME
`Pixels` element reported `SizeC=2`. DAPI and GFP were actually stored in the
separate `d0` and `d1` files. Bio-Formats therefore reported a two-channel core
model even though the single file did not contain the two expected physical
channel planes.

This logical-versus-physical mismatch is the central compatibility hazard. An
automatic importer that trusts only the OME dimensions may seek a channel plane
that is not in that file or may fail to form a consistent view set.

Fiji's Grid/Collection Stitching plugin reads such a file as two channels, the
second empty, even though ImageJ's plain TIFF opener reports one plane. After
fusion, keep the channel that contains data (the stitching helper below does
this). Do not assume the real plane is always channel 0.

Check all of the following:

- TIFF/OME logical `SizeX`, `SizeY`, `SizeC`, `SizeZ`, and `SizeT`.
- Number of physically readable planes in the file.
- Whether channels are separate `dN` files.
- Whether corresponding field numbers exist for every required channel.

## Useful embedded OME fields

The verified export stored enough information to construct a Fiji tile layout:

- `Plane PositionX` and `Plane PositionY`: commanded stage position.
- `Pixels PhysicalSizeX` and `PhysicalSizeY`: pixel calibration.
- `Plane ExposureTime`: exposure in seconds.
- `Pixels SignificantBits`/reader bit depth: acquisition precision.

The stage positions were in micrometres. Convert them to pixel coordinates for a
Fiji `TileConfiguration` using:

```text
x_px = (PositionX - minimum_PositionX) / PhysicalSizeX
y_px = (PositionY - minimum_PositionY) / PhysicalSizeY
```

Normalizing by each ROI's minimum coordinates keeps values compact without
changing relative placement. Confirm axis signs and orientation with a preview;
do not silently flip axes to make an image look plausible.

In the verified data, tiles were 2048 x 1536 and acquired at 12 significant
bits in a 16-bit integer container. The derived OME-TIFF mosaics were therefore
correctly exported as 16-bit images while retaining the original numeric range.

## Inventory checklist

Before using Fiji:

1. Group files by timestamped acquisition directory.
2. Parse field and `dN` channel indices with an anchored filename pattern.
3. Count tiles per channel and report missing channel partners.
4. Identify ROI boundaries from acquisition records, stage-position discontinuity,
   or an explicit manifest; do not infer them from a loose sample-name filter.
5. Compare pixel size, tile dimensions, bit depth, and exposure across groups.
6. Sample stage positions to confirm a plausible grid and overlap.

If the dataset does not encode ROI identity, ask for the acquisition map or make
the inferred manifest explicit and reviewable before a full stitch.

## Scans stored on Dropbox

EVOS scans are usually written to the lab Dropbox. Access is strictly
read-only: download the scan to a local scratch directory and run the stitcher
there. Never upload, move, delete, or modify remote files, and never point the
stitcher's output or work directory at Dropbox.

See [dropbox-rclone.md](dropbox-rclone.md) for the allowed rclone commands, URL
mapping and the verified EVOS download pattern.

## Stitching raw tiles into one TIFF

`scripts/stitch_evos_m7000.py` writes one OME-TIFF per (slide, pass) from one
acquisition directory. Separate timestamped acquisitions remain separate, even
when slide names match. No viewer or pyramid is generated.

Before invoking Fiji the helper:

1. Requires exactly one physical 2-D grayscale TIFF page, matching OME X/Y,
   and Z=T=1. A phantom OME channel count is allowed; extra physical pages or
   SubIFDs are not. `_M_`, `TM`, and `.stitch` files are not raw inputs.
2. Requires both pixel calibrations, normalizes supported SI length units to
   micrometres, and rejects nonfinite coordinates, nonpositive calibration,
   anisotropy, and unsupported units. Missing length units use the verified
   EVOS micrometre convention; do not apply that assumption to unrelated
   microscope exports.
3. Pairs unique field identities across channels, checks dimensions,
   uint8/uint16 dtype and channel-name consistency, and rejects corresponding
   stage positions differing by more than 0.01 pixel on either axis.
4. Recovers ROIs from stage positions and reports canvas dimensions,
   uncompressed size, estimated scratch/output space, and fusion windows.
   Estimates use the stage layout, not a guarantee about registration results
   or JVM/OS allocation overhead.
   Singleton neighbouring columns can remain in the same ROI; repeated vertical
   gaps do not connect tiles whose footprints do not overlap.

For ROIs that fit the fusion budget, Fiji registers/fuses the reference
channel with Grid/Collection Stitching and Linear Blending. The helper checks
the registered configuration and falls back to stage coordinates when it has
multiple origins or missing entries. All sibling channels use the same chosen
coordinates. A single-origin configuration is a heuristic, not alignment proof.

### Oversized ROIs and memory

ImageJ cannot hold a 2-D plane above 2^31-1 pixels. More heap does not fix that
limit. The helper defaults to **100,000,000 pixels per fusion window**, also
limiting its working-set estimate to 75% of the measured Fiji heap.
`--max-fusion-pixels N` can change the window ceiling, never above ImageJ's
limit. `--memory-gb N` sets `_JAVA_OPTIONS=-XmxNg` and the helper probes the
actual JVM maximum before fusion. Ensure the machine can support that heap;
the probe is not a physical-RAM availability guarantee.

An ROI requiring multiple windows uses **stage-only fusion, without
registration**; this is announced before processing and recorded in QC.
Stage offsets are rounded once to the nearest global ROI pixel (at most
0.5 pixel per axis), not independently per window. Each window includes every
source tile intersecting its core plus a one-pixel support halo. Fiji fuses
the full contributing tiles with Linear Blending. Only the window's disjoint
core is copied into a disk-backed ROI canvas. There is no rectangular
last-writer merge of overlapping partitions, no gap collapse within a
partitioned ROI, and valid zero-valued pixels remain data.

If even one output pixel's full contributor set exceeds the chosen budget,
the helper fails with an actionable limit/heap error rather than dropping
tiles. Separate ROI placement is also coverage-aware: background holes cannot
erase another ROI, and overlapping covered independent ROIs are rejected.

```bash
python3 scripts/stitch_evos_m7000.py /path/to/scan.2026-09-20-09-04-34 \
  --output-dir /path/to/out --channels d0,d1 --reference d0 --memory-gb 8
```

Requires Python 3.10+ with numpy, tifffile and Pillow, plus a Fiji launcher
(`--fiji` or `FIJI_CMD`). Run `--dry-run` first: it inventories and plans without
starting Fiji or creating output. Without `--output-dir`, the destination is
`./<acquisition-directory-name>-stitched`; its absolute path is printed first.
Explicit local destinations take precedence. Outputs/work must be outside
raw inputs, and never under Dropbox.

The helper refuses existing TIFF or matching `.qc` destinations. It writes
and validates a temporary TIFF beside the destination, then atomically
publishes it without overwriting another file. A failed export leaves no
apparently finished TIFF. The destination filesystem must support hard links.
Each final `name.tif` has a `name.tif.qc/` directory containing:

- `verification.json`: expected metadata, decoded statistics, performed checks,
  source-key/coverage bookkeeping, partition cores and explicit limitations;
- `c0.png`, `c1.png`, etc. and `composite.png`: preview-only percentile stretches;
- per-channel `boundaryNNNN_cN.png` crops for every artificial core edge, with
  global coordinates and sampling strides in the report.

All TIFF pixels are decoded in bounded strips and compared exactly to the
fused CYX canvas before publication. Channel names/order, dtype, plane mapping,
Z/T, calibration, and segment coverage are checked. This proves export fidelity,
not correct registration or biological interpretation. Inspect informative
overlaps and **every partition boundary**; sampled previews can miss small
artifacts. Record visual checks separately from automated checks.

For an existing output, run the same bounded verifier independently:

```bash
python3 scripts/verify_evos_stitch.py /path/to/image.tif --preview-dir /path/to/qc
```

Independent inspection derives expectations from that TIFF's metadata and is
explicitly labelled self-consistency only. It cannot establish the correct
channel identity, calibration, or equality to an intended acquisition.
Encoded/decoded segments above 64 MiB must be rewritten with smaller strips
or tiles before this verifier can decode them within its bound.
Deflate streams receive a bounded decompression preflight against their declared
strip/tile size before normal TIFF decoding, adding one read/decompression pass.
Invalid OME mappings are rejected rather than accepted through tifffile's
generic-series fallback.

Every Fiji invocation retains a full `.ijm.log` while running. Exit zero is
insufficient: completion, fatal macro/JVM diagnostics, and readable output are
checked. On failure the working tree is kept and its path reported. Use
`--workdir DIR` to retain successful intermediates too; otherwise successful
temporary work is removed. Retain small scientific evidence before manually
cleaning any task-managed work.
Existing work-directory input links are reused only when they resolve to the
current source tile. A conflicting link or regular file is left unchanged and
the run fails; use a fresh work directory for a different acquisition.

Empty bands **between independent ROIs** are collapsed (100 px margin by
default), so inter-ROI distances are not physical. Pixel size remains valid
within each ROI. Blending and resampling create derived intensities; raw
sources remain unchanged, and display stretching affects previews only.

