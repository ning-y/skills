#!/usr/bin/env python3
"""Decode and verify stitched OME-TIFFs without loading a whole image."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
import zlib

import numpy as np
from PIL import Image
import tifffile


MAX_SEGMENT_BYTES = 64 * 1024 * 1024
PREVIEW_SIZE = 768
_LENGTH_UM = {prefix + "m": 10.0 ** (power + 6) for prefix, power in (
    ("Y", 24), ("Z", 21), ("E", 18), ("P", 15), ("T", 12), ("G", 9),
    ("M", 6), ("k", 3), ("h", 2), ("da", 1), ("", 0), ("d", -1),
    ("c", -2), ("m", -3), ("µ", -6), ("n", -9), ("p", -12),
    ("f", -15), ("a", -18), ("z", -21), ("y", -24))}
_LENGTH_UM.update({"μm": 1.0, "um": 1.0})
LIMITS = [
    "Geometry, source-tile coverage, missing/duplicated source tiles, seams and "
    "registration require stitcher checks and visual inspection.",
    "Exact equality to the fused canvas does not prove correct registration.",
    "Previews are bounded spatial samples with percentile stretching, not "
    "full-resolution quantitative images or proof that every artifact is visible.",
    "Only single-image, single-resolution, unsigned grayscale CYX OME-TIFFs "
    "with Z=T=1 are accepted; RGB samples and external TIFF planes are rejected.",
    "Encoded and decoded strips/tiles must each be at most 64 MiB; rewrite "
    "oversized strips/tiles before verification. TIFF metadata and decoder "
    "working storage are additional to this per-segment bound.",
]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _pixels(tif):
    _require(bool(tif.ome_metadata), "missing OME metadata")
    try:
        root = ET.fromstring(tif.ome_metadata)
    except ET.ParseError as exc:
        raise ValueError(f"invalid OME XML: {exc}") from exc
    images = root.findall("{*}Image")
    _require(len(images) == 1, "expected exactly one OME Image")
    pixels = images[0].findall("{*}Pixels")
    _require(len(pixels) == 1, "expected exactly one OME Pixels element")
    return pixels[0]


def _calibration(pixels, axis):
    attribute = "PhysicalSize" + axis
    try:
        value = float(pixels.attrib[attribute])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"missing or invalid OME {attribute}") from exc
    unit = pixels.get(attribute + "Unit", "µm")
    _require(unit in _LENGTH_UM, f"unsupported OME {attribute}Unit: {unit!r}")
    value *= _LENGTH_UM[unit]
    _require(math.isfinite(value) and value > 0,
             f"invalid OME {attribute}: expected positive finite calibration")
    return value, unit


def _metadata(tif):
    pixels = _pixels(tif)
    try:
        shape = tuple(int(pixels.attrib["Size" + axis]) for axis in "CYX")
        z, t = (int(pixels.attrib["Size" + axis]) for axis in "ZT")
        dtype = np.dtype(pixels.attrib["Type"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid OME dimensions or Type: {exc}") from exc
    channels = pixels.findall("{*}Channel")
    names = [channel.get("Name") for channel in channels]
    _require(all(name is not None for name in names), "OME channel Name is missing")
    _require(all(channel.get("SamplesPerPixel", "1") == "1"
                 for channel in channels), "OME channels must have SamplesPerPixel=1")
    return pixels, shape, z, t, dtype, names


def _stretch(samples):
    low, high = np.percentile(samples, [1, 99])
    if high <= low:
        low, high = 0.0, float(samples.max())
    if high == 0:
        return np.zeros(samples.shape, dtype=np.uint8)
    return np.clip((samples.astype(np.float64) - low) * (255 / (high - low)),
                   0, 255).astype(np.uint8)


def save_boundary_previews(canvas, boundaries, preview_dir):
    """Sample both sides of each partition edge; these are not visual passes.

    Boundaries use half-open start/end coordinates along the edge. Each PNG
    spans up to 32 pixels on each side and samples the full edge extent.
    """
    _require(canvas.ndim == 3 and all(size > 0 for size in canvas.shape)
             and canvas.dtype.kind == "u", "boundary canvas must be unsigned CYX")
    count, height, width = canvas.shape
    directory = Path(preview_dir)
    reports = []
    for index, boundary in enumerate(boundaries):
        axis = boundary["axis"]
        coordinate, start, end = (boundary[key] for key in ("coordinate", "start", "end"))
        _require(axis in ("x", "y")
                 and all(isinstance(value, (int, np.integer))
                         for value in (coordinate, start, end)),
                 "boundary axis/coordinates are invalid")
        coordinate, start, end = int(coordinate), int(start), int(end)
        across, along = (width, height) if axis == "x" else (height, width)
        _require(0 < coordinate < across and 0 <= start < end <= along,
                 "boundary lies outside canvas or has an empty extent")
        before, after = max(0, coordinate - 32), min(across, coordinate + 32)
        y0, y1, x0, x1 = ((start, end, before, after) if axis == "x"
                          else (before, after, start, end))
        ystep = max(1, math.ceil((y1 - y0) / PREVIEW_SIZE))
        xstep = max(1, math.ceil((x1 - x0) / PREVIEW_SIZE))
        directory.mkdir(parents=True, exist_ok=True)
        filenames = {}
        for channel in range(count):
            samples = canvas[channel, y0:y1:ystep, x0:x1:xstep]
            filename = directory / f"boundary{index:04d}_c{channel}.png"
            Image.fromarray(_stretch(samples)).save(filename)
            filenames[f"c{channel}"] = str(filename)
        reports.append({
            "axis": axis, "coordinate": coordinate, "start": start, "end": end,
            "window": {"y0": y0, "y1": y1, "x0": x0, "x1": x1},
            "sample_stride": {"y": ystep, "x": xstep},
            "previews": filenames, "visual_inspection_required": True,
        })
    return reports


def verify_ome(path, expected_shape, dtype, px, names, *, expected=None,
               preview_dir=None):
    """Return a JSON-safe report, or raise ValueError before publication.

    expected_shape and expected use CYX even for a singleton channel. px is
    isotropic micrometres/pixel. expected may be a disk-backed NumPy memmap.
    No page.asarray calls or full-image QC arrays are used.
    """
    shape = tuple(expected_shape)
    dtype = np.dtype(dtype)
    names = list(names)
    _require(len(shape) == 3 and all(isinstance(n, (int, np.integer)) and n > 0
                                    for n in shape), "expected_shape must be positive CYX")
    shape = tuple(int(n) for n in shape)
    count, height, width = shape
    _require(dtype.kind == "u", "expected dtype must be unsigned integer")
    _require(math.isfinite(px) and px > 0, "expected px must be positive and finite")
    _require(len(names) == count and all(isinstance(name, str) for name in names),
             "expected names must contain one string per channel")
    if expected is not None:
        _require(expected.shape == shape and np.dtype(expected.dtype) == dtype,
                 "expected canvas shape/dtype does not match expected metadata")

    ys = np.linspace(0, height - 1, min(height, PREVIEW_SIZE), dtype=np.int64)
    xs = np.linspace(0, width - 1, min(width, PREVIEW_SIZE), dtype=np.int64)
    preview_path = Path(preview_dir) if preview_dir is not None else None
    composite = (np.zeros((len(ys), len(xs), 3), dtype=np.uint8)
                 if preview_path is not None else None)
    stats, previews = [], {}
    with tifffile.TiffFile(path) as tif:
        pixels, declared_shape, z, t, declared_dtype, declared_names = _metadata(tif)
        _require(declared_shape == shape and z == t == 1,
                 f"OME dimensions C/Y/X/Z/T differ from expected {shape}/1/1")
        _require(declared_dtype == dtype, "OME Type differs from expected dtype")
        _require(declared_names == names, "OME channel names/order differ from expected")
        x_um, x_unit = _calibration(pixels, "X")
        y_um, y_unit = _calibration(pixels, "Y")
        _require(math.isclose(x_um, px, rel_tol=1e-9, abs_tol=0.0)
                 and math.isclose(y_um, px, rel_tol=1e-9, abs_tol=0.0),
                 "OME PhysicalSizeX/Y calibration differs from expected micrometres")
        _require(len(tif.series) == 1, "expected exactly one TIFF series")
        series = tif.series[0]
        _require(series.kind == "ome", "invalid OME plane mapping: generic TIFF fallback")
        valid_shape = (series.axes == "CYX" and tuple(series.shape) == shape)
        valid_shape |= (count == 1 and series.axes == "YX"
                        and tuple(series.shape) == shape[1:])
        _require(valid_shape, f"TIFF axes/shape {series.axes}/{series.shape} differ from CYX {shape}")
        _require(series.dtype == dtype, "TIFF series dtype differs from expected")
        _require(len(series.levels) == 1, "pyramidal TIFFs are not supported")
        _require(len(tif.pages) == count and len(series.pages) == count,
                 "physical TIFF plane count differs from expected channels")
        physical_offsets = {page.offset for page in tif.pages}
        logical_offsets = [page.offset for page in series.pages if page is not None]
        _require(len(set(logical_offsets)) == count
                 and set(logical_offsets) == physical_offsets,
                 "OME plane mapping is missing, duplicated, or external")
        for channel, logical_page in enumerate(series.pages):
            # Resolve TiffFrames to physical pages so every IFD's tags are checked.
            page = logical_page.aspage()
            _require(page.parent is tif, "OME plane is not in the current TIFF")
            _require(page.axes == "YX" and tuple(page.shape) == (height, width)
                     and page.dtype == dtype and page.samplesperpixel == 1,
                     f"physical plane {channel} dimensions/axes/dtype/samples differ")
            _require(not page.subifds, "SubIFD planes are not supported")
            block_h = page.tilelength if page.is_tiled else min(page.rowsperstrip, height)
            block_w = page.tilewidth if page.is_tiled else width
            _require(block_h > 0 and block_w > 0, "invalid strip/tile dimensions")
            _require(block_h * block_w * dtype.itemsize <= MAX_SEGMENT_BYTES,
                     "decoded strip/tile exceeds 64 MiB; rewrite using smaller strips/tiles")
            rows = (height + block_h - 1) // block_h
            cols = (width + block_w - 1) // block_w
            _require(len(page.dataoffsets) == len(page.databytecounts) == rows * cols,
                     "strip/tile count leaves pixel gaps or extra segments")
            for offset, bytecount in zip(page.dataoffsets, page.databytecounts):
                _require(offset > 0 and 0 < bytecount <= MAX_SEGMENT_BYTES
                         and offset + bytecount <= tif.filehandle.size,
                         "missing, truncated, or oversized encoded strip/tile")
                if page.compression in (8, 32946, 50013):
                    # tifffile's stdlib fallback ignores the decoder output limit.
                    tif.filehandle.seek(offset)
                    encoded = tif.filehandle.read(bytecount)
                    decoder = zlib.decompressobj()
                    try:
                        decoded = decoder.decompress(
                            encoded, block_h * block_w * dtype.itemsize + 1)
                    except zlib.error as exc:
                        raise ValueError(f"invalid Deflate strip/tile: {exc}") from exc
                    _require(len(decoded) <= block_h * block_w * dtype.itemsize
                             and decoder.eof,
                             "Deflate strip/tile exceeds declared size or is truncated")
                    del encoded, decoded, decoder
            samples = np.zeros((len(ys), len(xs)), dtype=dtype)
            minimum, maximum, total, nonzero, covered, segments = None, None, 0.0, 0, 0, 0
            try:
                for index, (decoded, position, segment_shape) in enumerate(
                        page.segments(maxworkers=1, buffersize=1, sort=False)):
                    y, x = (index // cols) * block_h, (index % cols) * block_w
                    h, w = min(block_h, height - y), min(block_w, width - x)
                    _require(index < rows * cols and position == (0, 0, y, x, 0),
                             "decoded strip/tile bounds/order leave gaps or overlap")
                    _require(decoded is not None and decoded.ndim == 4
                             and decoded.shape[0] == decoded.shape[3] == 1
                             and decoded.shape[1] >= h and decoded.shape[2] >= w
                             and tuple(decoded.shape) == tuple(segment_shape)
                             and decoded.shape[1] <= block_h and decoded.shape[2] <= block_w
                             and decoded.dtype == dtype,
                             "missing or malformed decoded strip/tile")
                    block = decoded[0, :h, :w, 0]
                    if expected is not None:
                        _require(np.array_equal(block, expected[channel, y:y + h, x:x + w]),
                                 f"decoded pixel mismatch in channel {channel} at strip/tile ({y}, {x})")
                    block_min, block_max = int(block.min()), int(block.max())
                    minimum = block_min if minimum is None else min(minimum, block_min)
                    maximum = block_max if maximum is None else max(maximum, block_max)
                    total += float(block.sum(dtype=np.float64))
                    nonzero += int(np.count_nonzero(block))
                    covered += h * w
                    segments += 1
                    y0, y1 = np.searchsorted(ys, [y, y + h])
                    x0, x1 = np.searchsorted(xs, [x, x + w])
                    samples[y0:y1, x0:x1] = block[np.ix_(ys[y0:y1] - y, xs[x0:x1] - x)]
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(f"failed decoding channel {channel}: {exc}") from exc
            _require(segments == rows * cols and covered == height * width,
                     "decoded coverage has pixel gaps")
            stats.append({"channel": channel, "name": names[channel], "min": minimum,
                          "max": maximum, "mean": total / covered, "nonzero": nonzero,
                          "pixels": covered, "segments": segments})
            if preview_path is not None:
                preview_path.mkdir(parents=True, exist_ok=True)
                gray = _stretch(samples)
                filename = preview_path / f"c{channel}.png"
                Image.fromarray(gray).save(filename)
                previews[f"c{channel}"] = str(filename)
                component = (2, 1, 0)[channel % 3]
                np.maximum(composite[:, :, component], gray,
                           out=composite[:, :, component])
        if preview_path is not None:
            if count == 1:
                composite[:] = gray[:, :, None]
            filename = preview_path / "composite.png"
            Image.fromarray(composite).save(filename)
            previews["composite"] = str(filename)
    checks = ["physical plane count and unique OME-to-IFD mapping",
              "physical plane dimensions, CYX/YX axes and unsigned integer dtype",
              "OME C/Y/X dimensions, Z=T=1 and Type",
              "channel names and order", "PhysicalSizeX/Y and units normalized to micrometres",
              "every encoded strip/tile is present and within file bounds",
              "every pixel decoded with disjoint, complete strip/tile coverage"]
    if expected is not None:
        checks.append("every decoded pixel equals the supplied fused CYX canvas exactly")
    return {"path": os.fspath(path), "passed": True, "shape": list(shape),
            "axes": series.axes, "dtype": dtype.name, "physical_planes": count,
            "channel_names": names, "pixel_size_um": {"X": x_um, "Y": y_um},
            "ome_length_units": {"X": x_unit, "Y": y_unit},
            "pixel_equality_checked": expected is not None, "checks": checks,
            "statistics": stats, "previews": previews, "limits": list(LIMITS)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="existing single-image OME-TIFF(s)")
    parser.add_argument("--preview-dir", help="write bounded c0/c1/composite PNG previews")
    args = parser.parse_args(argv)
    reports, failed = [], False
    for index, path in enumerate(args.paths):
        try:
            with tifffile.TiffFile(path) as tif:
                pixels, shape, _z, _t, dtype, names = _metadata(tif)
                px, _unit = _calibration(pixels, "X")
            preview_dir = args.preview_dir
            if preview_dir is not None and len(args.paths) > 1:
                preview_dir = str(Path(preview_dir) / f"{index}_{Path(path).stem}")
            report = verify_ome(path, shape, dtype, px, names, preview_dir=preview_dir)
            report["verification_scope"] = "self-consistency only"
            report["expectations_source"] = "OME metadata in the inspected file; not independent acquisition expectations"
            report["limits"].append("No independent expected metadata or fused canvas was supplied; self-consistency does not establish channel identity, correct calibration, or pixel equality to an intended output.")
        except Exception as exc:
            failed = True
            report = {"path": path, "passed": False, "error": str(exc),
                      "verification_scope": "self-consistency only"}
        reports.append(report)
    print(json.dumps({"reports": reports}, indent=2, allow_nan=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
