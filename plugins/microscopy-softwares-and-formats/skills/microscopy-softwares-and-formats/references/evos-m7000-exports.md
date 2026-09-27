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
