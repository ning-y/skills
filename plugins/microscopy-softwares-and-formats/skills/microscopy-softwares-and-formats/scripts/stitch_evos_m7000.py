#!/usr/bin/env python3
"""Stitch EVOS M7000 raw tiles into one large OME-TIFF per (slide, pass).

The EVOS export uses ``scan_<slide>_R_p<pass>_<well>_A<area>f<field>d<channel>.TIF``.
The field number is acquisition order, not ROI identity, so ROIs are recovered
from the OME stage positions. ROIs fitting the fusion budget are registered and
fused by Fiji's Grid/Collection Stitching plugin on a reference channel; sibling
channels reuse the layout. Larger ROIs use globally aligned, bounded stage-only
fusion windows. Covered ROI pixels are composed without overwriting holes.
The OME-TIFF is fully decoded/compared before atomic publication with QC previews.

Two EVOS quirks drive the implementation:

* A raw file contains one physical plane but its OME ``Pixels`` element may
  advertise ``SizeC=2``.  Bio-Formats therefore hands Fiji a two-channel image
  whose second channel is empty.  The fusion macro keeps only the channel that
  contains data.
* Registration that cannot connect every tile produces several ``(0, 0)``
  anchors.  In that case the original stage layout is used for the whole ROI.

Requires Python 3.10+, numpy, tifffile, Pillow and Fiji.
Raw tiles are never modified. Failed work is kept with diagnostic logs;
successful temporary work is removed unless --workdir was supplied.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import tifffile

from verify_evos_stitch import save_boundary_previews, verify_ome

IMAGEJ_MAX_PIXELS = 2**31 - 1
DEFAULT_MAX_FUSION_PIXELS = 100_000_000


TILE_RE = re.compile(
    r"^scan_(?P<slide>.+)_R_p(?P<pass>\d+)_(?P<well>\d+)_A(?P<area>\d+)"
    r"f(?P<field>\d+)d(?P<channel>\d+)\.(?:tif|tiff)$",
    re.IGNORECASE,
)


@dataclass
class Tile:
    path: str
    slide: str
    pas: str
    well: str
    area: str
    fld: int
    channel: int
    px: float
    x_um: float
    y_um: float
    width: int
    height: int
    dtype: np.dtype
    name: str

    @property
    def key(self):
        return f"w{self.well}a{self.area}f{self.fld:05d}"

    @property
    def link_name(self):
        return f"{self.key}d{self.channel}.tif"

    @property
    def x_px(self):
        return self.x_um / self.px

    @property
    def y_px(self):
        return self.y_um / self.px


_LENGTH_TO_UM = {
    f"{prefix}m": 10.0 ** (power + 6)
    for prefix, power in (
        ("Y", 24), ("Z", 21), ("E", 18), ("P", 15), ("T", 12),
        ("G", 9), ("M", 6), ("k", 3), ("h", 2), ("da", 1),
        ("", 0), ("d", -1), ("c", -2), ("m", -3), ("µ", -6),
        ("n", -9), ("p", -12), ("f", -15), ("a", -18),
        ("z", -21), ("y", -24),
    )
}
_LENGTH_TO_UM.update({"μm": 1.0, "um": 1.0})


def _length_um(element, attribute, path, positive=False):
    value = element.get(attribute)
    if value is None:
        raise ValueError(f"{path}: OME metadata lacks {attribute}")
    unit = element.get(attribute + "Unit", "µm")
    if unit not in _LENGTH_TO_UM:
        raise ValueError(f"{path}: unsupported {attribute} unit {unit!r}")
    try:
        value = float(value) * _LENGTH_TO_UM[unit]
    except ValueError as exc:
        raise ValueError(f"{path}: invalid {attribute}") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(
            f"{path}: {attribute} must be "
            f"{'positive and finite' if positive else 'finite'}")
    return value


def read_tile(path, match):
    """Read one raw tile's OME metadata without decoding its pixels."""
    with tifffile.TiffFile(path) as tif:
        if len(tif.pages) != 1:
            raise ValueError(
                f"{path}: expected one physical 2-D TIFF page, "
                f"got {len(tif.pages)}")
        page = tif.pages[0]
        if len(page.shape) != 2 or page.samplesperpixel != 1 or page.subifds:
            raise ValueError(
                f"{path}: expected one physical 2-D TIFF page, got {page.shape}")
        height, width = page.shape
        dtype = np.dtype(page.dtype).newbyteorder("=")
        ome = tif.ome_metadata
        if ome is None:
            raise ValueError(f"{path}: no OME-XML in ImageDescription")
        root = ET.fromstring(ome)
        images = root.findall(".//{*}Image")
        if len(images) != 1:
            raise ValueError(f"{path}: expected one OME Image")
        image = images[0]
        pixels = image.find("{*}Pixels")
        planes = [] if pixels is None else pixels.findall("{*}Plane")
        if pixels is None or len(planes) != 1:
            raise ValueError(f"{path}: OME metadata requires Pixels and one Plane")
        plane = planes[0]
        try:
            dimensions = {axis: int(pixels.get("Size" + axis, ""))
                          for axis in "XYZCT"}
            plane_axes = [int(plane.get("The" + axis, "0")) for axis in "ZT"]
        except ValueError as exc:
            raise ValueError(f"{path}: invalid OME dimensions or plane axes") from exc
        if any(size <= 0 for size in dimensions.values()):
            raise ValueError(f"{path}: OME dimensions must be positive")
        if (dimensions["X"], dimensions["Y"]) != (width, height):
            raise ValueError(
                f"{path}: OME dimensions {dimensions['X']}x{dimensions['Y']} "
                f"do not match TIFF page {width}x{height}")
        if dimensions["Z"] != 1 or dimensions["T"] != 1 or any(plane_axes):
            raise ValueError(f"{path}: unsupported OME Z/T axes")
        # EVOS exports may declare phantom channels, but only one page is real.
        sizes = [_length_um(pixels, "PhysicalSize" + axis, path, positive=True)
                 for axis in "XY"]
        if not math.isclose(sizes[0], sizes[1], rel_tol=1e-3, abs_tol=0.0):
            raise ValueError(
                f"{path}: anisotropic pixels {sizes[0]:g}x{sizes[1]:g} µm "
                f"are not supported")
        px = sizes[0]
        pos_x = _length_um(plane, "PositionX", path)
        pos_y = _length_um(plane, "PositionY", path)
        image_name = ((image.get("Name") if image is not None else "") or "").strip()
        channel_el = root.find(".//{*}Channel")
        channel_name = ((channel_el.get("Name") if channel_el is not None
                         else "") or "").strip()
        if not image_name or re.fullmatch(r"Image\d*:?", image_name):
            name = channel_name
        else:
            name = image_name

    return Tile(
        path=path,
        slide=match["slide"],
        pas=match["pass"],
        well=match["well"],
        area=match["area"],
        fld=int(match["field"]),
        channel=int(match["channel"]),
        px=px,
        x_um=pos_x,
        y_um=pos_y,
        width=width,
        height=height,
        dtype=dtype,
        name=name,
    )


def discover(directory):
    """Return {(slide, pass): {channel: [Tile]}} for the raw tiles found."""
    units = defaultdict(lambda: defaultdict(list))
    for entry in os.scandir(directory):
        if not entry.is_file():
            continue
        match = TILE_RE.match(entry.name)
        if not match:
            continue
        tile = read_tile(entry.path, match)
        units[(tile.slide, tile.pas)][tile.channel].append(tile)
    return units


def validate_unit(channels):
    """Check that channels share one pixel grid and one set of fields."""
    ordered = {channel: sorted(tiles, key=lambda t: t.fld)
               for channel, tiles in sorted(channels.items())}
    if not ordered:
        raise ValueError("no channels selected")
    for channel, tiles in ordered.items():
        if not tiles:
            raise ValueError(f"channel d{channel} has no tiles")
    first = next(iter(ordered.values()))[0]
    by_key = {}
    for channel, tiles in ordered.items():
        by_key[channel] = {}
        channel_name = tiles[0].name.strip()
        for tile in tiles:
            if tile.key in by_key[channel]:
                raise ValueError(
                    f"channel d{channel}: duplicate field identity {tile.key}")
            by_key[channel][tile.key] = tile
            if tile.channel != channel or (tile.slide, tile.pas) != (
                    first.slide, first.pas):
                raise ValueError(f"{tile.path}: inconsistent channel or acquisition")
            if tile.name.strip() != channel_name:
                raise ValueError(f"channel d{channel}: inconsistent channel names")
            if not math.isfinite(tile.px) or tile.px <= 0:
                raise ValueError(f"{tile.path}: pixel calibration must be positive and finite")
            if not all(math.isfinite(value) for value in (tile.x_um, tile.y_um)):
                raise ValueError(f"{tile.path}: stage coordinates must be finite")
            if not math.isclose(tile.px, first.px, rel_tol=1e-6, abs_tol=0.0):
                raise ValueError(
                    f"channel d{channel}: {tile.path} has {tile.px:g} µm/px, "
                    f"expected {first.px:g} µm/px")
            if (tile.width, tile.height) != (first.width, first.height):
                raise ValueError(
                    f"channel d{channel}: {tile.path} is "
                    f"{tile.width}x{tile.height}, expected "
                    f"{first.width}x{first.height}")
            if tile.dtype != first.dtype:
                raise ValueError(
                    f"channel d{channel}: {tile.path} is {tile.dtype}, "
                    f"expected {first.dtype}")
            if tile.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
                raise ValueError(f"{tile.path}: Fiji fusion supports uint8/uint16 tiles")
    reference = next(iter(by_key.values()))
    reference_keys = set(reference)
    for channel, fields in by_key.items():
        keys = set(fields)
        if keys != reference_keys:
            missing = sorted(reference_keys - keys)
            extra = sorted(keys - reference_keys)
            raise ValueError(
                f"channel d{channel} does not pair with the other channels "
                f"(missing {missing[:3]}, unexpected {extra[:3]})")
        for key, tile in fields.items():
            other = reference[key]
            tolerance = first.px * 0.01
            if (abs(tile.x_um - other.x_um) > tolerance or
                    abs(tile.y_um - other.y_um) > tolerance):
                raise ValueError(
                    f"channel d{channel}: field {key} stage positions "
                    "differ by more than 0.01 pixel")
    return ordered


# --- ROI discovery ---------------------------------------------------------
# Field numbers are acquisition order; adjacent tiles are found from stage
# positions.  This mirrors the column/row adjacency already validated on EVOS
# exports: snap tiles into columns, discover the row step from within-column
# distances, connect matching rows, then split on gaps wider than one tile.

def cluster_rois(tiles):
    if len(tiles) <= 1:
        return [list(tiles)] if tiles else []

    tile_w, tile_h, px = tiles[0].width, tiles[0].height, tiles[0].px
    columns = {}
    for index, tile in enumerate(tiles):
        for column_x in columns:
            if abs(tile.x_um - column_x) <= px * 50:
                columns[column_x].append((index, tile))
                break
        else:
            columns[tile.x_um] = [(index, tile)]
    for column in columns.values():
        column.sort(key=lambda item: -item[1].y_um)

    gaps = [abs(a[1].y_um - b[1].y_um)
            for column in columns.values()
            for a, b in zip(column, column[1:])]
    if len(gaps) < 3:
        return _cluster_overlap(tiles)
    grid_y, tolerance_y = _grid_step(gaps, tile_h * px)
    if grid_y is None:
        return _cluster_overlap(tiles)

    adjacency = {index: set() for index in range(len(tiles))}
    for column in columns.values():
        for (index_a, a), (index_b, b) in zip(column, column[1:]):
            gap = abs(a.y_um - b.y_um)
            if gap < tile_h * px and abs(gap - grid_y) <= tolerance_y:
                adjacency[index_a].add(index_b)
                adjacency[index_b].add(index_a)

    column_xs = sorted(columns)
    for i, x_a in enumerate(column_xs):
        tiles_a = columns[x_a]
        for x_b in column_xs[i + 1:]:
            tiles_b = columns[x_b]
            by_y = {round(tile.y_um, 6): (index, tile)
                    for index, tile in tiles_b}
            for index_a, tile_a in tiles_a:
                for y_b, (index_b, _tile_b) in by_y.items():
                    if abs(tile_a.y_um - y_b) <= tolerance_y:
                        adjacency[index_a].add(index_b)
                        adjacency[index_b].add(index_a)
                        break

    gap_threshold = tile_w * px * 1.3
    column_indices = {x: {index for index, _ in column}
                      for x, column in columns.items()}
    for i in range(len(column_xs) - 1):
        if column_xs[i + 1] - column_xs[i] >= gap_threshold:
            left = set().union(*(column_indices[x]
                                 for x in column_xs[:i + 1]))
            right = set().union(*(column_indices[x]
                                  for x in column_xs[i + 1:]))
            for index_a in left:
                for index_b in adjacency[index_a] & right:
                    adjacency[index_a].discard(index_b)
                    adjacency[index_b].discard(index_a)

    return _components(adjacency, tiles)


def _grid_step(distances_um, tile_dim_um):
    bins = defaultdict(int)
    for distance in distances_um:
        bins[int(distance // 10.0) * 10] += 1
    for center, count in sorted(bins.items(), key=lambda item: -item[1]):
        if center < tile_dim_um * 0.05 or center >= tile_dim_um:
            continue
        if count >= 3:
            return center + 5.0, 30.0
    return None, None


def _cluster_overlap(tiles):
    """Fallback for sparse grids: connect tiles whose stage boxes overlap."""
    adjacency = {index: set() for index in range(len(tiles))}
    for i, a in enumerate(tiles):
        a_x0, a_y0 = a.x_px - a.width / 2, a.y_px - a.height / 2
        a_x1, a_y1 = a_x0 + a.width, a_y0 + a.height
        for j in range(i + 1, len(tiles)):
            b = tiles[j]
            b_x0, b_y0 = b.x_px - b.width / 2, b.y_px - b.height / 2
            b_x1, b_y1 = b_x0 + b.width, b_y0 + b.height
            if a_x0 < b_x1 and b_x0 < a_x1 and a_y0 < b_y1 and b_y0 < a_y1:
                adjacency[i].add(j)
                adjacency[j].add(i)
    return _components(adjacency, tiles)


def _components(adjacency, tiles):
    visited, rois = set(), []
    for start in range(len(tiles)):
        if start in visited:
            continue
        component, stack = [], [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.append(tiles[node])
            stack.extend(adjacency[node] - visited)
        rois.append(component)
    return rois


# --- Fiji stitching --------------------------------------------------------

def write_stage_config(path, tiles):
    lines = ["dim = 2\n"]
    xs = [tile.x_px for tile in tiles]
    ys = [tile.y_px for tile in tiles]
    min_x, min_y = min(xs), min(ys)
    for tile, x, y in sorted(zip(tiles, xs, ys),
                             key=lambda item: (item[1], item[2])):
        lines.append(f"{tile.link_name}; ; ({x - min_x:.2f}, {y - min_y:.2f})\n")
    text = "".join(lines)
    Path(path).write_text(text)
    return text


def registered_origins(text):
    """Count (0, 0) anchors and tile lines in a Fiji registered configuration."""
    coordinates = [
        (float(match.group(1)), float(match.group(2)))
        for match in re.finditer(
            r"\(\s*([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\)", text)
    ]
    origins = sum(1 for x, y in coordinates
                  if abs(x) < 1e-6 and abs(y) < 1e-6)
    return origins, len(coordinates)


def choose_config(stage_text, registered_text, tile_count):
    """Use registered coordinates only when they form one connected graph."""
    if registered_text:
        origins, count = registered_origins(registered_text)
        if count == tile_count and origins == 1:
            return registered_text, "registered"
    return stage_text, "stage"


def retarget_config(text, source, target):
    return re.sub(rf"d{source}\.tif\b", f"d{target}.tif", text)


def fusion_macro(workdir, config_name, output_path, compute_overlap):
    overlap = "compute_overlap " if compute_overlap else ""
    return f'''run("Grid/Collection stitching", "type=[Positions from file] order=[Defined by TileConfiguration] directory=[{workdir}] layout_file={config_name} fusion_method=[Linear Blending] regression_threshold=0.30 max/avg_displacement_threshold=2.50 absolute_displacement_threshold=3.50 {overlap}computation_parameters=[Save memory (but be slower)] image_output=[Fuse and display]");
getDimensions(width, height, channels, slices, frames);
if (channels > 1) {{
  run("Split Channels");
  bestTitle = getTitle();
  bestScore = -1;
  for (i = 1; i <= nImages; i++) {{
    selectImage(i);
    getStatistics(area, mean, min, max, sd, hist);
    if (max > bestScore) {{ bestScore = max; bestTitle = getTitle(); }}
  }}
  for (i = nImages; i >= 1; i--) {{
    selectImage(i);
    if (getTitle() != bestTitle) close();
  }}
  selectWindow(bestTitle);
}}
saveAs("Tiff", "{output_path}");
print("EVOS_FUSION_OK");
eval("script", "System.exit(0);");
'''


def run_fiji(fiji, macro_path, expected_output=None, memory_gb=None,
             marker="EVOS_FUSION_OK"):
    """Keep the complete log even when Fiji returns zero after a macro failure."""
    log_path = macro_path + ".log"
    environment = os.environ.copy()
    if memory_gb is not None:
        environment["_JAVA_OPTIONS"] = (
            environment.get("_JAVA_OPTIONS", "") + f" -Xmx{memory_gb}g").strip()
    with open(log_path, "w") as log:
        result = subprocess.run(
            [fiji, "--headless", "--console", "-macro", macro_path],
            stdout=log, stderr=subprocess.STDOUT, text=True, env=environment)
    tail, errors, completed = deque(maxlen=50), [], False
    with open(log_path, errors="replace") as log:
        for line in log:
            tail.append(line.rstrip())
            completed |= line.strip().startswith(marker)
            if re.search(r"^Macro Error:|^Exception in thread|"
                         r"^java\.[\w.]+(?:Exception|Error):|"
                         r"<Out of memory>", line):
                errors.append(line.strip())
    failure = None
    if result.returncode or errors or not completed:
        failure = f"exit {result.returncode}; completion marker={completed}"
    elif expected_output is not None:
        try:
            with tifffile.TiffFile(expected_output) as tif:
                if len(tif.pages) != 1 or len(tif.pages[0].shape) != 2:
                    raise ValueError("expected one fused grayscale plane")
        except (OSError, ValueError, tifffile.TiffFileError) as error:
            failure = f"missing or invalid fused output: {error}"
    if failure:
        raise RuntimeError(
            f"Fiji failed for {macro_path}: {failure}\n"
            f"Log: {log_path}\n" + "\n".join(errors[-5:] + list(tail)))
    return log_path


def fiji_heap(fiji, workdir, memory_gb):
    macro = os.path.join(workdir, "heap.ijm")
    Path(macro).write_text(
        'eval("script", "print(\'EVOS_HEAP=\' + '
        'Java.type(\'java.lang.Runtime\').getRuntime().maxMemory());");\n'
        'eval("script", "System.exit(0);");\n')
    log = run_fiji(fiji, macro, memory_gb=memory_gb, marker="EVOS_HEAP=")
    match = re.search(r"^EVOS_HEAP=(\d+)\s*$", Path(log).read_text(),
                      re.MULTILINE)
    if match is None:
        raise RuntimeError(f"cannot determine Fiji heap; see {log}")
    return int(match[1])


def stage_rectangles(tiles):
    """Quantize stage-only placement once on the ROI's global pixel grid."""
    min_x = min(tile.x_px for tile in tiles)
    min_y = min(tile.y_px for tile in tiles)
    return [(int(math.floor(tile.x_px - min_x + 0.5)),
             int(math.floor(tile.y_px - min_y + 0.5)),
             tile.width, tile.height) for tile in tiles]


def fusion_bytes(tiles, pixels):
    # Estimate includes EVOS's phantom channel, output and blending workspace.
    inputs = sum(tile.width * tile.height * tile.dtype.itemsize for tile in tiles)
    return 2 * inputs + 12 * pixels


def fusion_windows(tiles, max_pixels, heap_bytes=None):
    """Partition output ownership, not tiles; keep every contributor at edges."""
    rectangles = stage_rectangles(tiles)
    width = max(x + w for x, y, w, h in rectangles)
    height = max(y + h for x, y, w, h in rectangles)
    pending = [(0, 0, width, height)]
    windows = []
    while pending:
        x0, y0, x1, y1 = core = pending.pop()
        selected = [i for i, (x, y, w, h) in enumerate(rectangles)
                    if x < x1 + 1 and x + w > x0 - 1
                    and y < y1 + 1 and y + h > y0 - 1]
        if not selected:
            continue
        left = min(rectangles[i][0] for i in selected)
        top = min(rectangles[i][1] for i in selected)
        right = max(rectangles[i][0] + rectangles[i][2] for i in selected)
        bottom = max(rectangles[i][1] + rectangles[i][3] for i in selected)
        pixels = (right - left) * (bottom - top)
        subset = [tiles[i] for i in selected]
        fits_heap = (heap_bytes is None
                     or fusion_bytes(subset, pixels) <= heap_bytes * 0.75)
        if pixels <= max_pixels and fits_heap:
            windows.append((core, selected, (left, top, right, bottom)))
        else:
            if x1 - x0 == 1 and y1 - y0 == 1:
                raise ValueError(
                    "even one output pixel's contributing tiles exceed the "
                    "fusion limit/heap budget; raise --max-fusion-pixels "
                    "(below ImageJ's limit) or --memory-gb")
            if x1 - x0 >= y1 - y0 and x1 - x0 > 1:
                middle = (x0 + x1) // 2
                pending.extend([(middle, y0, x1, y1), (x0, y0, middle, y1)])
            else:
                middle = (y0 + y1) // 2
                pending.extend([(x0, middle, x1, y1), (x0, y0, x1, middle)])
    included = {i for core, selected, bounds in windows for i in selected}
    if included != set(range(len(tiles))):
        raise ValueError("fusion plan omits source tiles")
    return (height, width), rectangles, windows


def bounded_fusion(index, roi, channels, reference, workdir, fiji,
                   max_pixels, heap_bytes, memory_gb):
    shape, rectangles, windows = fusion_windows(roi, max_pixels, heap_bytes)
    by_key = {channel: {tile.key: tile for tile in tiles}
              for channel, tiles in channels.items()}
    outputs = {}
    for channel in channels:
        path = os.path.join(workdir, f"roi_{index}_d{channel}.tif")
        outputs[channel] = path
        canvas = tifffile.memmap(path, shape=shape, dtype=roi[0].dtype,
                                bigtiff=math.prod(shape) * roi[0].dtype.itemsize
                                > 3_500_000_000)
        try:
            for number, (core, selected, bounds) in enumerate(windows):
                left, top, right, bottom = bounds
                config = f"window_{number}_d{channel}.txt"
                lines = ["dim = 2\n"]
                for i in selected:
                    tile = by_key[channel][roi[i].key]
                    x, y, w, h = rectangles[i]
                    lines.append(f"{tile.link_name}; ; ({x-left}, {y-top})\n")
                Path(workdir, config).write_text("".join(lines))
                fused = os.path.join(workdir, f"window_{number}_d{channel}.tif")
                macro = os.path.join(workdir, f"window_{number}_d{channel}.ijm")
                Path(macro).write_text(fusion_macro(workdir, config, fused, False))
                print(f"    window {number+1}/{len(windows)} d{channel}: "
                      f"core {core}, {len(selected)} contributing tiles",
                      flush=True)
                run_fiji(fiji, macro, fused, memory_gb)
                image = read_image(fused)
                if image.shape != (bottom-top, right-left):
                    raise ValueError(
                        f"Fiji window shape {image.shape} disagrees with "
                        f"global bounds {bounds}")
                x0, y0, x1, y1 = core
                x0, y0 = max(x0, left), max(y0, top)
                x1, y1 = min(x1, right), min(y1, bottom)
                # Cores are disjoint: uncovered rectangle pixels never erase
                # another window, and legitimate dark pixels stay legitimate.
                for row in range(y0, y1, 256):
                    end = min(row + 256, y1)
                    canvas[row:end, x0:x1] = image[
                        row-top:end-top, x0-left:x1-left]
                del image
                _remove(fused)
            canvas.flush()
        finally:
            del canvas
    return outputs, rectangles, {
        "method": "bounded stage-coordinate fusion",
        "stage_rounding_max_pixels": 0.5,
        "shape": list(shape),
        "cores_xyxy": [list(core) for core, selected, bounds in windows],
        "contributing_tiles_checked": len(roi),
        "manual_checks_required": "Inspect every core boundary and informative overlaps",
    }


def _remove(*paths):
    for path in paths:
        if os.path.exists(path):
            os.remove(path)


def link_tiles(workdir, channels):
    for tiles in channels.values():
        for tile in tiles:
            link = os.path.join(workdir, tile.link_name)
            if os.path.lexists(link):
                if not os.path.islink(link) or os.path.realpath(link) != os.path.realpath(tile.path):
                    raise ValueError(f"{link}: existing input does not match {tile.path}; use a fresh workdir")
            else:
                os.symlink(os.path.abspath(tile.path), link)


def stitch_roi(index, roi, channels, reference, workdir, fiji,
               max_pixels=DEFAULT_MAX_FUSION_PIXELS, heap_bytes=None,
               memory_gb=None):
    """Return fused paths, coverage rectangles, and performed geometry checks."""
    by_key = {channel: {tile.key: tile for tile in tiles}
              for channel, tiles in channels.items()}
    keys = [tile.key for tile in roi]

    stage_path = os.path.join(workdir, "TileConfiguration.txt")
    stage_text = write_stage_config(stage_path, roi)

    shape, rectangles, windows = fusion_windows(roi, max_pixels, heap_bytes)
    if len(windows) > 1:
        print(f"  ROI {index}: {shape[1]}x{shape[0]} exceeds the fusion "
              "pixel/heap budget; stage-only bounded fusion (no registration)",
              flush=True)
        return bounded_fusion(index, roi, channels, reference, workdir, fiji,
                              max_pixels, heap_bytes, memory_gb)
    if len(roi) == 1:
        print(f"  ROI {index}: {len(roi)} tile - no stitching")
        outputs = {channel: by_key[channel][keys[0]].path for channel in channels}
        return outputs, stage_rectangles(roi), {"method": "single source tile"}

    print(f"  ROI {index}: {len(roi)} tiles - registering on d{reference}")
    reference_path = os.path.join(workdir, f"roi_{index}_d{reference}.tif")
    registered_path = os.path.join(workdir, "TileConfiguration.registered.txt")
    _remove(reference_path, registered_path)
    macro_path = os.path.join(workdir, "register.ijm")
    Path(macro_path).write_text(
        fusion_macro(workdir, "TileConfiguration.txt", reference_path, True))
    run_fiji(fiji, macro_path, reference_path, memory_gb)

    registered_text = None
    if os.path.exists(registered_path):
        registered_text = Path(registered_path).read_text()
        os.replace(registered_path,
                   os.path.join(workdir, f"roi_{index}_registered.txt"))

    config_text, source = choose_config(stage_text, registered_text, len(roi))
    print(f"    registration: {source} coordinates")
    if source == "stage":
        print("    warning: no single connected registration; "
              "using stage layout")
        _remove(reference_path)
        macro_path = os.path.join(workdir, "stage.ijm")
        Path(macro_path).write_text(
            fusion_macro(workdir, "TileConfiguration.txt", reference_path, False))
        run_fiji(fiji, macro_path, reference_path, memory_gb)

    outputs = {reference: reference_path}
    for channel in channels:
        if channel == reference:
            continue
        channel_config = retarget_config(config_text, reference, channel)
        config_name = f"roi_{index}_d{channel}.txt"
        Path(os.path.join(workdir, config_name)).write_text(channel_config)
        output_path = os.path.join(workdir, f"roi_{index}_d{channel}.tif")
        _remove(output_path)
        macro_path = os.path.join(workdir, f"fuse_d{channel}.ijm")
        Path(macro_path).write_text(
            fusion_macro(workdir, config_name, output_path, False))
        print(f"    fusing d{channel} with the same coordinates")
        run_fiji(fiji, macro_path, output_path, memory_gb)
        outputs[channel] = output_path

    coordinates = {}
    for name, x, y in re.findall(
            r"^([^;\r\n]+);[ \t]*;[ \t]*\(\s*([-+0-9.eE]+),\s*([-+0-9.eE]+)\)",
            config_text, re.MULTILINE):
        coordinates[name.strip()] = (float(x), float(y))
    if set(coordinates) != {tile.link_name for tile in roi}:
        raise ValueError("chosen layout does not contain exactly the ROI tiles")
    min_x = min(x for x, y in coordinates.values())
    min_y = min(y for x, y in coordinates.values())
    rectangles = [(int(math.floor(coordinates[t.link_name][0] - min_x + 0.5)),
                   int(math.floor(coordinates[t.link_name][1] - min_y + 0.5)),
                   t.width, t.height) for t in roi]
    return outputs, rectangles, {
        "method": f"{source} coordinates",
        "contributing_tiles_checked": len(roi),
        "manual_checks_required": "Inspect informative overlaps; connectivity is not alignment proof",
    }


# --- Canvas layout ---------------------------------------------------------

def _merge_intervals(intervals):
    if not intervals:
        return []
    merged = [list(sorted(intervals)[0])]
    for start, end in sorted(intervals)[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def compress_layout(roi_bounds, margin=100):
    """Collapse empty bands between ROIs, leaving *margin* pixels between them."""
    if len(roi_bounds) <= 1:
        return [(0, 0)] * len(roi_bounds)

    x_bands = _merge_intervals([(x, x + w) for x, y, w, h in roi_bounds])
    y_bands = _merge_intervals([(y, y + h) for x, y, w, h in roi_bounds])
    x_gaps = [(a[1], b[0]) for a, b in zip(x_bands, x_bands[1:])
              if b[0] > a[1]]
    y_gaps = [(a[1], b[0]) for a, b in zip(y_bands, y_bands[1:])
              if b[0] > a[1]]

    def shift(coord, gaps):
        return sum(max(0.0, (end - start) - margin)
                   for start, end in gaps if coord >= end)

    positions = [(x - shift(x, x_gaps), y - shift(y, y_gaps))
                 for x, y, _w, _h in roi_bounds]
    min_x = min(x for x, _y in positions)
    min_y = min(y for _x, y in positions)
    return [(x - min_x, y - min_y) for x, y in positions]


def read_image(path):
    try:
        array = tifffile.memmap(path)
    except ValueError:
        with tifffile.TiffFile(path) as tif:
            array = tif.pages[0].asarray()
    if array.ndim != 2:
        raise ValueError(f"{path}: expected a single-channel image, "
                         f"got shape {array.shape}")
    return array.astype(array.dtype.newbyteorder("="), copy=False)


def coverage_mask(rectangles, x0, y0, width, height):
    mask = np.zeros((height, width), dtype=bool)
    for x, y, w, h in rectangles:
        left, top = max(x, x0), max(y, y0)
        right, bottom = min(x+w, x0+width), min(y+h, y0+height)
        if left < right and top < bottom:
            mask[top-y0:bottom-y0, left-x0:right-x0] = True
    return mask


def composite(channels, rois, stitched, margin, canvas_path=None, coverage=None):
    """Place each channel's fused ROIs on one shared canvas.

    ``canvas_path`` backs the canvas with a memory-mapped file so a large
    mosaic does not need its full size in RAM.
    """
    first_channel = next(iter(channels))
    bounds = []
    for roi_index, roi in enumerate(rois):
        image = read_image(stitched[first_channel][roi_index])
        x0 = min(tile.x_px - tile.width / 2 for tile in roi)
        y0 = min(tile.y_px - tile.height / 2 for tile in roi)
        bounds.append((x0, y0, image.shape[1], image.shape[0]))
        del image

    global_x = min(bound[0] for bound in bounds)
    global_y = min(bound[1] for bound in bounds)
    bounds = [(x - global_x, y - global_y, w, h)
              for x, y, w, h in bounds]
    positions = compress_layout(bounds, margin)
    coverage = coverage or [stage_rectangles(roi) for roi in rois]
    # Separate ROIs may have overlapping bounding boxes, not overlapping data.
    placed = []
    for (nx, ny), rectangles in zip(positions, coverage):
        current = [(round(nx)+x, round(ny)+y, w, h) for x, y, w, h in rectangles]
        for x, y, w, h in current:
            if any(x < a+aw and x+w > a and y < b+bh and y+h > b
                   for a, b, aw, bh in placed):
                raise ValueError("independent ROI coverage overlaps on the output canvas")
        placed.extend(current)

    canvas_w = max(int(math.ceil(nx + w)) for (nx, ny), (_x, _y, w, _h)
                   in zip(positions, bounds))
    canvas_h = max(int(math.ceil(ny + h)) for (nx, ny), (_x, _y, _w, h)
                   in zip(positions, bounds))

    dtype = channels[first_channel][0].dtype
    if canvas_path is None:
        stack = np.zeros((len(channels), canvas_h, canvas_w), dtype=dtype)
    else:
        stack = np.memmap(canvas_path, dtype=dtype, mode="w+",
                          shape=(len(channels), canvas_h, canvas_w))
    for channel_index, channel in enumerate(channels):
        for roi_index, path in enumerate(stitched[channel]):
            image = read_image(path)
            if image.dtype != dtype:
                raise ValueError(
                    f"{path}: fused dtype {image.dtype}, expected {dtype}")
            if image.shape != (bounds[roi_index][3], bounds[roi_index][2]):
                raise ValueError(f"{path}: sibling channels have different shapes")
            x, y = (int(round(v)) for v in positions[roi_index])
            for row in range(0, image.shape[0], 256):
                end = min(row + 256, image.shape[0])
                for column in range(0, image.shape[1], 4096):
                    stop = min(column + 4096, image.shape[1])
                    mask = coverage_mask(coverage[roi_index], column, row,
                                         stop-column, end-row)
                    np.copyto(stack[channel_index, y+row:y+end,
                                    x+column:x+stop],
                              image[row:end, column:stop], where=mask)
            del image
    return stack, positions, bounds


def write_ome(path, stack, px, names, geometry=None):
    """Decode/compare a staged TIFF before atomically publishing without overwrite."""
    path = os.path.abspath(path)
    qc_path = path + ".qc"
    if os.path.lexists(path) or os.path.lexists(qc_path):
        raise FileExistsError(f"refusing to overwrite output or QC: {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".evos-export-", dir=os.path.dirname(path))
    temporary = os.path.join(staging, "image.ome.tif")
    published = False
    try:
        metadata = {
            "axes": "CYX",
            "PhysicalSizeX": px, "PhysicalSizeXUnit": "µm",
            "PhysicalSizeY": px, "PhysicalSizeYUnit": "µm",
            "Channel": {"Name": names},
            "Description": "EVOS mosaic; blending/resampling creates derived intensities",
        }
        rows = max(1, min(256, 16_777_216 // (stack.shape[-1] * stack.dtype.itemsize)))
        tifffile.imwrite(
            temporary, stack, ome=True, compression="zlib", rowsperstrip=rows,
            bigtiff=stack.nbytes > 3_500_000_000, metadata=metadata)
        report = verify_ome(temporary, stack.shape, stack.dtype, px, names,
                            expected=stack, preview_dir=staging)
        report["path"] = path
        if geometry is not None:
            report["geometry"] = geometry
        report["previews"] = {
            key: os.path.join(qc_path, os.path.basename(value))
            for key, value in report["previews"].items()}
        if geometry is not None:
            edges = set()
            for roi, (nx, ny) in zip(geometry["rois"], geometry["canvas_positions_xy"]):
                height, width = roi.get("shape", (0, 0))
                for x0, y0, x1, y1 in roi.get("cores_xyxy", []):
                    for coordinate in (x0, x1):
                        if 0 < coordinate < width:
                            edges.add(("x", round(nx)+coordinate,
                                       round(ny)+y0, round(ny)+y1))
                    for coordinate in (y0, y1):
                        if 0 < coordinate < height:
                            edges.add(("y", round(ny)+coordinate,
                                       round(nx)+x0, round(nx)+x1))
            boundaries = [dict(zip(("axis", "coordinate", "start", "end"), edge))
                          for edge in sorted(edges)]
            report["partition_boundaries"] = save_boundary_previews(
                stack, boundaries, staging)
            for boundary in report["partition_boundaries"]:
                boundary["previews"] = {
                    key: os.path.join(qc_path, os.path.basename(value))
                    for key, value in boundary["previews"].items()}
        Path(staging, "verification.json").write_text(
            json.dumps(report, indent=2) + "\n")
        # A hard link publishes one complete inode and cannot clobber another run.
        os.link(temporary, path)
        published = True
        os.unlink(temporary)
        os.rename(staging, qc_path)
    except BaseException:
        if published:
            os.unlink(path)
        raise
    finally:
        if os.path.isdir(staging):
            shutil.rmtree(staging)
    return report


def channel_names(channels):
    names = []
    for channel in channels:
        name = channels[channel][0].name.strip() or f"d{channel}"
        if name in names:
            name = f"{name} d{channel}"
        names.append(name)
    return names


# --- Driver ----------------------------------------------------------------

def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "slide"


def parse_channels(value):
    if value is None:
        return None
    channels = []
    for part in value.split(","):
        text = part.strip().lower()
        if text.startswith("d"):
            text = text[1:]
        if not text.isdigit():
            raise ValueError(f"invalid channel {part!r} (expected e.g. d0)")
        channels.append(int(text))
    if len(set(channels)) != len(channels):
        raise ValueError("duplicate channel selection")
    return channels


def unit_output(directory, slide, pas, pass_count, output_dir):
    name = f"{os.path.basename(os.path.normpath(directory))}_{slugify(slide)}"
    if pass_count > 1:
        name += f"_p{pas}"
    return os.path.join(output_dir, name + ".tif")


def print_preflight(channels, reference, max_pixels, heap_bytes=None,
                    workdir=None, output_dir=None, margin=100):
    rois = cluster_rois(channels[reference])
    bounds, roi_bytes, largest_window = [], 0, 0
    first = channels[reference][0]
    for number, roi in enumerate(rois):
        shape, rectangles, windows = fusion_windows(roi, max_pixels, heap_bytes)
        height, width = shape
        pixels = width * height
        roi_bytes += pixels * first.dtype.itemsize * len(channels)
        window_pixels = max((b[2]-b[0]) * (b[3]-b[1])
                            for core, selected, b in windows)
        largest_window = max(largest_window, window_pixels)
        bounds.append((min(t.x_px for t in roi), min(t.y_px for t in roi),
                       width, height))
        method = "stage-only bounded fusion" if len(windows) > 1 else "registration/fusion"
        print(f"  preflight ROI {number}: {width}x{height}, {pixels:,} pixels/plane; "
              f"{len(windows)} window(s), {method}; whole-ROI heap estimate "
              f"{fusion_bytes(roi, pixels)/2**30:.2f} GiB", flush=True)
    positions = compress_layout(bounds, margin)
    width = max(math.ceil(x+w) for (x, y), (a, b, w, h) in zip(positions, bounds))
    height = max(math.ceil(y+h) for (x, y), (a, b, w, h) in zip(positions, bounds))
    canvas_bytes = width * height * first.dtype.itemsize * len(channels)
    output_bytes = math.ceil(canvas_bytes * 1.1) + 64 * 2**20
    work_bytes = roi_bytes + canvas_bytes + largest_window * first.dtype.itemsize
    print(f"  estimated canvas {width}x{height}, {canvas_bytes/2**30:.2f} GiB raw; "
          f"scratch peak ~{work_bytes/2**30:.2f} GiB plus "
          f"~{output_bytes/2**30:.2f} GiB staged output "
          "(stage-layout estimates, not allocation guarantees)", flush=True)
    if workdir is not None:
        if os.stat(workdir).st_dev == os.stat(output_dir).st_dev:
            required = work_bytes + output_bytes
            if required > shutil.disk_usage(workdir).free:
                raise ValueError(f"insufficient shared disk: estimate {required/2**30:.2f} GiB")
        elif (work_bytes > shutil.disk_usage(workdir).free
              or output_bytes > shutil.disk_usage(output_dir).free):
            raise ValueError("insufficient scratch/output disk for estimated peak")


def stitch_unit(key, channels, reference, output_path, args, workdir):
    slide, pas = key
    rois = cluster_rois(channels[reference])
    print(f"\n=== slide '{slide}', pass {pas} ===")
    print(f"  {len(channels)} channel(s), {len(channels[reference])} tiles, "
          f"{len(rois)} ROI(s): {[len(roi) for roi in rois]}")

    stitched = {channel: [] for channel in channels}
    coverage, geometry = [], []
    for index, roi in enumerate(rois):
        roi_dir = os.path.join(workdir, f"roi_{index}")
        os.makedirs(roi_dir, exist_ok=True)
        keys = {tile.key for tile in roi}
        link_tiles(roi_dir, {c: [t for t in ts if t.key in keys]
                            for c, ts in channels.items()})
        outputs, rectangles, checks = stitch_roi(
            index, roi, channels, reference, roi_dir, args.fiji,
            args.max_fusion_pixels, args.heap_bytes, args.memory_gb)
        coverage.append(rectangles)
        geometry.append(checks)
        for channel in channels:
            stitched[channel].append(outputs[channel])

    canvas_path = os.path.join(workdir, "canvas.raw")
    stack, positions, _bounds = composite(
        channels, rois, stitched, args.margin, canvas_path, coverage)
    names = channel_names(channels)
    px = channels[reference][0].px
    expected_shape = stack.shape
    try:
        write_ome(output_path, stack, px, names, {
            "rois": geometry, "canvas_positions_xy": positions,
            "coverage": "All source tile keys included; only covered ROI pixels copied",
            "limits": "Coverage bookkeeping is not image-registration or seam proof",
            "manual_checks_required": [
                "Inspect informative tile overlaps",
                "Inspect every bounded-fusion core boundary",
            ],
        })
    finally:
        del stack
        if os.path.exists(canvas_path):
            os.remove(canvas_path)
    height, width = expected_shape[1:]
    print(f"  composed {len(rois)} ROI(s) at {positions} on "
          f"{width}x{height} px")
    print(f"  wrote {output_path} "
          f"({len(channels)} channel(s) {names}, {px:g} µm/px)")
    return output_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Stitch EVOS M7000 raw tiles into one OME-TIFF per "
                    "(slide, pass).")
    parser.add_argument("directory", help="EVOS scan directory")
    parser.add_argument("--output-dir",
                        help="local destination (default: ./<scan-name>-stitched)")
    parser.add_argument("--slide", help="only this exact slide name")
    parser.add_argument("--channels", help="channels to stitch, e.g. d0,d1 "
                                          "(default: all captured channels)")
    parser.add_argument("--reference",
                        help="channel used for registration (default: lowest)")
    parser.add_argument("--margin", type=int, default=100,
                        help="pixels between ROIs after empty bands collapse "
                             "(default: 100)")
    parser.add_argument("--fiji", default=os.environ.get("FIJI_CMD", "fiji"),
                        help="Fiji launcher (default: $FIJI_CMD or fiji)")
    parser.add_argument("--workdir",
                        help="keep symlinks, configurations and fused ROIs here")
    parser.add_argument("--max-fusion-pixels", type=int,
                        default=DEFAULT_MAX_FUSION_PIXELS,
                        help="maximum pixels per Fiji fusion window (default: 100000000)")
    parser.add_argument("--memory-gb", type=int,
                        help="set Fiji's JVM heap in GiB via _JAVA_OPTIONS")
    parser.add_argument("--dry-run", action="store_true",
                        help="report units, ROIs and outputs without stitching")
    args = parser.parse_args(argv)
    args.directory = os.path.realpath(os.path.expanduser(args.directory))
    args.output_dir = os.path.realpath(os.path.expanduser(
        args.output_dir or (os.path.basename(args.directory) + "-stitched")))
    print(f"Output directory: {args.output_dir}", flush=True)
    if (not 1 <= args.max_fusion_pixels <= IMAGEJ_MAX_PIXELS
            or args.memory_gb is not None and args.memory_gb < 1):
        print("Error: fusion limit must be 1..2^31-1; memory must be positive",
              file=sys.stderr)
        return 1
    if os.path.commonpath([args.directory, args.output_dir]) == args.directory:
        print("Error: output must be outside the raw acquisition directory",
              file=sys.stderr)
        return 1

    if not os.path.isdir(args.directory):
        print(f"Error: not a directory: {args.directory}", file=sys.stderr)
        return 1
    if args.margin < 0:
        print("Error: --margin must be >= 0", file=sys.stderr)
        return 1
    try:
        requested = parse_channels(args.channels)
        requested_reference = (parse_channels(args.reference)
                               if args.reference is not None else None)
        if requested_reference is not None and len(requested_reference) != 1:
            raise ValueError("--reference accepts exactly one channel")
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if not args.dry_run:
        launcher = (args.fiji if os.path.sep in args.fiji
                    else shutil.which(args.fiji))
        if not launcher or not os.path.exists(launcher):
            print(f"Error: Fiji launcher not found: {args.fiji}",
                  file=sys.stderr)
            return 1

    try:
        units = discover(args.directory)
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if args.slide is not None:
        units = {key: value for key, value in units.items()
                 if key[0] == args.slide}
    if not units:
        print("Error: no raw EVOS tiles (scan_*_R_p*_*_A*f*d*.TIF) found",
              file=sys.stderr)
        return 1

    prepared, problems = {}, []
    for key, channels in sorted(units.items()):
        slide, pas = key
        try:
            if requested is not None:
                missing = [c for c in requested if c not in channels]
                if missing:
                    available = ", ".join(f"d{c}" for c in sorted(channels))
                    raise ValueError(
                        f"requested channel(s) {missing} not found "
                        f"(available: {available})")
                channels = {c: channels[c] for c in requested}
            channels = validate_unit(channels)
            reference = (requested_reference[0]
                         if requested_reference is not None
                         else next(iter(channels)))
            if reference not in channels:
                raise ValueError(
                    f"reference d{reference} is not among "
                    f"{[f'd{c}' for c in channels]}")
            prepared[key] = (channels, reference)
        except ValueError as error:
            problems.append(f"slide '{slide}', pass {pas}: {error}")

    for problem in problems:
        print(f"Error: {problem}", file=sys.stderr)

    pass_counts = defaultdict(int)
    for slide, pas in prepared:
        pass_counts[slide] += 1
    outputs = {
        key: unit_output(args.directory, key[0], key[1],
                         pass_counts[key[0]], args.output_dir)
        for key in prepared
    }
    if len(set(outputs.values())) != len(outputs):
        print("Error: slide names collide after filename normalization", file=sys.stderr)
        return 1
    for path in outputs.values():
        if os.path.lexists(path) or os.path.lexists(path + ".qc"):
            print(f"Error: refusing to overwrite output or QC: {path}", file=sys.stderr)
            return 1

    if args.dry_run:
        for (slide, pas), (channels, reference) in prepared.items():
            rois = cluster_rois(channels[reference])
            print(f"\nslide '{slide}', pass {pas}: "
                  f"{len(channels)} channel(s), "
                  f"{len(channels[reference])} tiles, "
                  f"{len(rois)} ROI(s) {[len(roi) for roi in rois]}")
            print(f"  channels: {', '.join(f'd{c}' for c in channels)}; "
                  f"reference d{reference}")
            print(f"  output: {outputs[(slide, pas)]}")
            try:
                print_preflight(channels, reference, args.max_fusion_pixels,
                                margin=args.margin)
            except ValueError as error:
                print(f"Error: {error}", file=sys.stderr)
                return 1
        return 1 if problems or not prepared else 0

    if not prepared:
        return 1

    os.makedirs(args.output_dir, exist_ok=True)

    temporary = args.workdir is None
    workroot = os.path.realpath(os.path.expanduser(args.workdir) if args.workdir
                               else tempfile.mkdtemp(prefix="evos-stitch-"))
    if os.path.commonpath([args.directory, workroot]) == args.directory:
        print("Error: work directory must be outside raw acquisition", file=sys.stderr)
        return 1
    os.makedirs(workroot, exist_ok=True)
    if any(character in workroot for character in ("]", '"', "\n", "\r", "\\")):
        print("Error: workdir contains unsupported Fiji macro path characters",
              file=sys.stderr)
        return 1

    try:
        args.heap_bytes = fiji_heap(args.fiji, workroot, args.memory_gb)
        print(f"Fiji heap: {args.heap_bytes / 2**30:.2f} GiB; "
              "fusion planning reserves 25% headroom", flush=True)
        for channels, reference in prepared.values():
            print_preflight(channels, reference, args.max_fusion_pixels,
                            args.heap_bytes, workroot, args.output_dir, args.margin)
    except Exception as error:
        print(f"Error: {error}\nDiagnostics kept in {workroot}", file=sys.stderr)
        return 1
    failures = len(problems)
    try:
        for key, (channels, reference) in prepared.items():
            slide, pas = key
            unit_workdir = os.path.join(workroot, f"{slugify(slide)}_p{pas}")
            os.makedirs(unit_workdir, exist_ok=True)
            try:
                stitch_unit(key, channels, reference, outputs[key],
                            args, unit_workdir)
            except Exception as error:
                failures += 1
                print(f"Error: slide '{slide}', pass {pas}: "
                      f"{type(error).__name__}: {error}", file=sys.stderr)
    finally:
        if temporary and failures == 0:
            shutil.rmtree(workroot, ignore_errors=True)
        else:
            print(f"Intermediates kept in {workroot}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
