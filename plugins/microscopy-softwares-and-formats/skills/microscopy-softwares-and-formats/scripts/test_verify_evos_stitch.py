#!/usr/bin/env python3
"""Deterministic OME metadata, full-decoding and bounded-preview self-checks."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib

import numpy as np
from PIL import Image
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_evos_stitch as verify  # noqa: E402


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "stitched.ome.tif"
        c, y, x = np.indices((2, 35, 51))
        self.data = (c * 3000 + y * 51 + x).astype(np.uint16)
        self.names = ["DAPI", "FITC"]

    def write(self, data=None, *, tiled=False, metadata=None):
        if data is None:
            data = self.data
        meta = {"axes": "CYX", "Channel": {"Name": self.names[:data.shape[0]]},
                "PhysicalSizeX": 0.5, "PhysicalSizeY": 0.5}
        if metadata:
            meta.update(metadata)
        layout = {"tile": (16, 16)} if tiled else {"rowsperstrip": 7}
        tifffile.imwrite(self.path, data, ome=True, photometric="minisblack",
                         compression="zlib", metadata=meta, **layout)

    def check(self, *, data=None, expected=None, preview_dir=None):
        if data is None:
            data = self.data
        return verify.verify_ome(self.path, data.shape, data.dtype, 0.5,
                                 self.names[:data.shape[0]], expected=expected,
                                 preview_dir=preview_dir)

    def mutate_xml(self, change):
        root = ET.fromstring(tifffile.tiffcomment(self.path))
        change(root.find("{*}Image/{*}Pixels"))
        tifffile.tiffcomment(self.path, ET.tostring(root, encoding="utf-8"))

    def test_every_strip_and_edge_tile_matches_canvas_and_statistics(self):
        for tiled in (False, True):
            with self.subTest(tiled=tiled):
                self.write(tiled=tiled)
                canvas = np.memmap(self.directory / "canvas.raw", mode="w+",
                                   dtype=self.data.dtype, shape=self.data.shape)
                canvas[:] = self.data
                with patch.object(tifffile.TiffPage, "asarray",
                                  side_effect=AssertionError("full-page read forbidden")):
                    report = self.check(expected=canvas)
                del canvas
                json.dumps(report, allow_nan=False)
                self.assertTrue(report["pixel_equality_checked"])
                self.assertEqual(report["physical_planes"], 2)
                for c, stats in enumerate(report["statistics"]):
                    self.assertEqual(stats["min"], int(self.data[c].min()))
                    self.assertEqual(stats["max"], int(self.data[c].max()))
                    self.assertEqual(stats["mean"], float(self.data[c].mean()))
                    self.assertEqual(stats["nonzero"], int(np.count_nonzero(self.data[c])))
                    self.assertEqual(stats["pixels"], 35 * 51)
                    self.assertEqual(stats["segments"], 12 if tiled else 5)

    def test_actual_decoded_pixel_mismatch_at_strip_and_tile_boundaries(self):
        for tiled, y, x in ((False, 7, 50), (True, 16, 16), (True, 34, 50)):
            with self.subTest(tiled=tiled, y=y, x=x):
                changed = self.data.copy()
                changed[1, y, x] += 1
                self.write(changed, tiled=tiled)
                with self.assertRaisesRegex(ValueError, "decoded pixel mismatch in channel 1"):
                    self.check(expected=self.data)

    def test_channel_order_and_channel_count_are_not_inferred_as_correct(self):
        for change, message in (
            (lambda pixels: pixels.findall("{*}Channel")[0].set("Name", "FITC"), "names/order"),
            (lambda pixels: pixels.set("SizeC", "3"), "dimensions"),
            (lambda pixels: pixels.set("SizeZ", "2"), "dimensions"),
            (lambda pixels: pixels.set("SizeT", "2"), "dimensions"),
            (lambda pixels: pixels.set("Type", "uint8"), "Type"),
        ):
            with self.subTest(message=message):
                self.write()
                self.mutate_xml(change)
                with self.assertRaisesRegex(ValueError, message):
                    self.check()

    def test_calibration_values_units_and_missing_calibration_are_checked(self):
        changes = [lambda p: p.set("PhysicalSizeY", "0.6"),
                   lambda p: p.set("PhysicalSizeXUnit", "mm"),
                   lambda p: p.set("PhysicalSizeYUnit", "pixel"),
                   lambda p: p.attrib.pop("PhysicalSizeX"),
                   lambda p: p.set("PhysicalSizeX", "nan")]
        for change in changes:
            with self.subTest(change=change):
                self.write()
                self.mutate_xml(change)
                with self.assertRaises(ValueError):
                    self.check()
        # Equivalent SI units and omitted (OME-default micrometre) units agree.
        self.write(metadata={"PhysicalSizeX": 500, "PhysicalSizeXUnit": "nm",
                             "PhysicalSizeY": 0.0005, "PhysicalSizeYUnit": "mm"})
        report = self.check(expected=self.data)
        self.assertEqual(report["pixel_size_um"], {"X": 0.5, "Y": 0.5})

    def test_missing_channel_name_is_rejected(self):
        self.write()
        self.mutate_xml(lambda pixels: pixels.findall("{*}Channel")[1].attrib.pop("Name"))
        with self.assertRaisesRegex(ValueError, "Name is missing"):
            self.check()

    def test_missing_strip_is_not_silently_treated_as_dark(self):
        self.write()
        with tifffile.TiffFile(self.path, mode="r+b") as tif:
            page = tif.pages[0]
            offsets = list(page.dataoffsets)
            offsets[2] = 0
            page.tags["StripOffsets"].overwrite(offsets)
        with self.assertRaisesRegex(ValueError, "missing, truncated"):
            self.check()

    def test_corrupted_last_compressed_strip_is_actually_decoded(self):
        self.write()
        with tifffile.TiffFile(self.path) as tif:
            page = tif.pages[-1]
            offset, length = page.dataoffsets[-1], page.databytecounts[-1]
        with self.path.open("r+b") as output:
            output.seek(offset)
            output.write(b"\x00" * length)
        with self.assertRaises(ValueError):
            self.check(expected=self.data)

    def test_deflate_expansion_beyond_declared_strip_is_rejected(self):
        data = np.zeros_like(self.data)
        self.write(data)
        payload = zlib.compress(bytes(4096))
        with self.path.open("ab") as output:
            offset = output.tell()
            output.write(payload)
        with tifffile.TiffFile(self.path, mode="r+b") as tif:
            page = tif.pages[0]
            offsets, counts = list(page.dataoffsets), list(page.databytecounts)
            offsets[0], counts[0] = offset, len(payload)
            page.tags["StripOffsets"].overwrite(offsets)
            page.tags["StripByteCounts"].overwrite(counts)
        with self.assertRaisesRegex(ValueError, "Deflate strip/tile exceeds"):
            self.check(data=data, expected=data)

    def test_singleton_invalid_ome_mapping_cannot_use_generic_fallback(self):
        data = self.data[:1]
        self.write(data)
        self.mutate_xml(lambda pixels: pixels.find("{*}TiffData").set("IFD", "99"))
        with self.assertRaisesRegex(ValueError, "OME plane mapping"):
            self.check(data=data, expected=data)

    def test_decoder_missing_segment_cannot_leave_coverage_gaps(self):
        self.write(tiled=True)
        original = tifffile.TiffPage.segments

        def omit_middle(page, **kwargs):
            for index, result in enumerate(original(page, **kwargs)):
                if index != 5:
                    yield result

        with patch.object(tifffile.TiffPage, "segments", omit_middle):
            with self.assertRaisesRegex(ValueError, "gaps|overlap"):
                self.check()

    def test_singleton_dark_channel_is_valid_and_previews_are_black(self):
        dark = np.zeros((1, 35, 51), dtype=np.uint16)
        self.write(dark)
        preview_dir = self.directory / "qc"
        report = self.check(data=dark, expected=dark, preview_dir=preview_dir)
        self.assertEqual(report["axes"], "YX")
        self.assertEqual(report["statistics"][0]["min"], 0)
        self.assertEqual(report["statistics"][0]["max"], 0)
        self.assertEqual(report["statistics"][0]["mean"], 0)
        self.assertEqual(report["statistics"][0]["nonzero"], 0)
        for name in ("c0.png", "composite.png"):
            with Image.open(preview_dir / name) as image:
                self.assertFalse(np.asarray(image).any())

    def test_spatial_previews_are_bounded_and_do_not_modify_pixels(self):
        data = np.zeros((2, 801, 1001), dtype=np.uint16)
        data[0, :, :] = np.arange(1001, dtype=np.uint16)[None, :]
        data[1, :, :] = np.arange(801, dtype=np.uint16)[:, None]
        self.write(data, tiled=True)
        preview_dir = self.directory / "qc"
        report = self.check(data=data, expected=data, preview_dir=preview_dir)
        for channel in (0, 1):
            with Image.open(preview_dir / f"c{channel}.png") as image:
                self.assertEqual(image.size, (verify.PREVIEW_SIZE, verify.PREVIEW_SIZE))
                gray = np.asarray(image)
                self.assertEqual(int(gray.min()), 0)
                self.assertEqual(int(gray.max()), 255)
            self.assertEqual(report["statistics"][channel]["max"], 1000 if channel == 0 else 800)
        with Image.open(preview_dir / "composite.png") as image:
            rgb = np.asarray(image)
            self.assertFalse(rgb[:, :, 0].any())
            self.assertTrue(rgb[:, :, 1].any())
            self.assertTrue(rgb[:, :, 2].any())
        self.check(data=data, expected=data)

    def test_boundary_previews_preserve_tissue_continuity_and_dark_gaps(self):
        canvas = np.memmap(self.directory / "boundaries.raw", mode="w+",
                           dtype=np.uint16, shape=(2, 901, 1001))
        canvas[:] = 10
        canvas[1, :, 500:502] = 0
        boundaries = [
            {"axis": "x", "coordinate": 500, "start": 0, "end": 901},
            {"axis": "y", "coordinate": 450, "start": 0, "end": 1001},
        ]
        reports = verify.save_boundary_previews(canvas, boundaries, self.directory / "edges")
        json.dumps(reports, allow_nan=False)
        self.assertEqual(len(reports), len(boundaries))
        for boundary, report in zip(boundaries, reports):
            for key, value in boundary.items():
                self.assertEqual(report[key], value)
            self.assertTrue(report["visual_inspection_required"])
            with Image.open(report["previews"]["c0"]) as image:
                self.assertLessEqual(max(image.size), verify.PREVIEW_SIZE)
                # Uniform positive tissue remains continuous on both sides.
                self.assertTrue((np.asarray(image) == 255).all())
            with Image.open(report["previews"]["c1"]) as image:
                gray = np.asarray(image)
                self.assertLessEqual(max(image.size), verify.PREVIEW_SIZE)
                if boundary["axis"] == "x":
                    self.assertTrue((gray[:, 32:34] == 0).all())
                    self.assertTrue((gray[:, :32] == 255).all())
                    self.assertTrue((gray[:, 34:] == 255).all())
                else:
                    self.assertTrue((gray[:, 250] == 0).all())
                    self.assertTrue((gray[:, :250] == 255).all())
                    self.assertTrue((gray[:, 251:] == 255).all())
        self.assertEqual(int(canvas[0, 450, 500]), 10)
        self.assertEqual(int(canvas[1, 450, 500]), 0)
        del canvas

    def test_cli_reports_actual_statistics_but_only_self_consistency(self):
        self.write()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = verify.main([str(self.path)])
        self.assertEqual(status, 0)
        report = json.loads(stdout.getvalue())["reports"][0]
        self.assertFalse(report["pixel_equality_checked"])
        self.assertEqual(report["statistics"][1]["max"], int(self.data[1].max()))
        # A self-consistent but false channel identity has no independent truth.
        self.mutate_xml(lambda pixels: pixels.findall("{*}Channel")[0].set("Name", "Wrong"))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = verify.main([str(self.path)])
        report = json.loads(stdout.getvalue())["reports"][0]
        self.assertEqual(status, 0)
        self.assertEqual(report["channel_names"][0], "Wrong")
        self.assertEqual(report["verification_scope"], "self-consistency only")
        with self.assertRaisesRegex(ValueError, "names/order"):
            self.check()


if __name__ == "__main__":
    unittest.main()
