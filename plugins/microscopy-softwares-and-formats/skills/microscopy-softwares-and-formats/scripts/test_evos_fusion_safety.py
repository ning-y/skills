#!/usr/bin/env python3
"""Geometry/transaction regressions; real Fiji integration is smoked separately."""

import json
import os
from pathlib import Path
import tempfile
import unittest

import numpy as np
import tifffile

import stitch_evos_m7000 as stitch


def tile(x, y, field, width=20, height=30):
    return stitch.Tile(path=f"tile{field}", slide="S", pas="00", well="0",
                       area="01", fld=field, channel=0, px=0.5,
                       x_um=x*0.5, y_um=y*0.5, width=width, height=height,
                       dtype=np.dtype("uint16"), name="DAPI")


class WindowTests(unittest.TestCase):
    def test_partition_cores_are_disjoint_and_keep_all_overlap_contributors(self):
        tiles = [tile(x, y, n, 64, 48) for n, (x, y) in enumerate(
            ( (x, y) for y in (0, 32, 64) for x in (0, 48, 96)
              if (x, y) != (48, 32)))]
        shape, rectangles, windows = stitch.fusion_windows(tiles, 12500)
        ownership = np.zeros(shape, np.uint8)
        self.assertGreater(len(windows), 1)
        for (x0, y0, x1, y1), selected, bounds in windows:
            ownership[y0:y1, x0:x1] += 1
            self.assertLessEqual((bounds[2]-bounds[0])*(bounds[3]-bounds[1]), 12500)
            for i, (x, y, w, h) in enumerate(rectangles):
                if x < x1 and x+w > x0 and y < y1 and y+h > y0:
                    self.assertIn(i, selected)
        self.assertTrue(np.all(ownership <= 1))
        for x, y, w, h in rectangles:
            self.assertTrue(np.all(ownership[y:y+h, x:x+w] == 1))

    def test_large_canvas_plans_without_allocating_an_image(self):
        tiles = [tile(x, y, i, 2048, 1536) for i, (x, y) in enumerate(
            ((x, y) for y in range(0, 54000, 1333)
             for x in range(0, 43000, 1845)))]
        shape, rectangles, windows = stitch.fusion_windows(tiles, 100_000_000)
        self.assertGreater(np.prod(shape), stitch.IMAGEJ_MAX_PIXELS)
        self.assertTrue(all((b[2]-b[0])*(b[3]-b[1]) <= 100_000_000
                            for core, selected, b in windows))
        self.assertEqual({i for core, selected, b in windows for i in selected},
                         set(range(len(tiles))))

    def test_limit_too_small_for_contributors_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "contributing tiles"):
            stitch.fusion_windows([tile(0, 0, 0)], 20)

    def test_stage_quantization_is_global_not_per_window(self):
        tiles = [tile(-1.2, 3.1, 0), tile(15.4, 20.7, 1)]
        self.assertEqual(stitch.stage_rectangles(tiles),
                         [(0, 0, 20, 30), (17, 18, 20, 30)])


class CoverageTests(unittest.TestCase):
    def test_later_roi_hole_does_not_erase_earlier_tissue_or_dark_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            # A fills the lower-right hole in B's L-shaped bounding rectangle.
            a = np.full((30, 20), 77, np.uint16)
            a[:, :4] = 0  # covered dark data, not an absent tile
            b = np.full((60, 40), 9, np.uint16)
            b[30:, 20:] = 0
            pa, pb = Path(directory, "a.tif"), Path(directory, "b.tif")
            tifffile.imwrite(pa, a)
            tifffile.imwrite(pb, b)
            rois = [[tile(20, 30, 0)],
                    [tile(0, 0, 1), tile(20, 0, 2), tile(0, 30, 3)]]
            stack, positions, _ = stitch.composite(
                {0: rois[0] + rois[1]}, rois, {0: [str(pa), str(pb)]}, margin=0)
            np.testing.assert_array_equal(stack[0, 30:, 20:], a)
            self.assertTrue(np.all(stack[0, :30] == 9))
            self.assertTrue(np.all(stack[0, 30:, :20] == 9))

    def test_independent_rois_cannot_overwrite_each_others_covered_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tile.tif")
            tifffile.imwrite(path, np.full((30, 20), 1, np.uint16))
            rois = [[tile(0, 0, 0)], [tile(10, 10, 1)]]
            with self.assertRaisesRegex(ValueError, "coverage overlaps"):
                stitch.composite({0: rois[0]+rois[1]}, rois,
                                 {0: [str(path), str(path)]}, margin=0)


class PublicationTests(unittest.TestCase):
    def test_failed_verification_leaves_no_final_or_partial_export(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory, "out.ome.tif"))
            with self.assertRaisesRegex(ValueError, "unsigned"):
                stitch.write_ome(path, np.ones((1, 8, 10), np.float32), 0.5, ["DAPI"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_destination_is_never_destroyed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "out.ome.tif")
            path.write_bytes(b"existing user data")
            with self.assertRaises(FileExistsError):
                stitch.write_ome(str(path), np.ones((1, 8, 10), np.uint16),
                                 0.5, ["DAPI"])
            self.assertEqual(path.read_bytes(), b"existing user data")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_published_pixels_and_qc_paths_survive_staging_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory, "out.ome.tif"))
            data = np.arange(160, dtype=np.uint16).reshape(2, 8, 10)
            stitch.write_ome(path, data, 0.5, ["DAPI", "RFP"])
            np.testing.assert_array_equal(tifffile.imread(path), data)
            report = json.loads(Path(path + ".qc", "verification.json").read_text())
            self.assertTrue(report["pixel_equality_checked"])
            for preview in report["previews"].values():
                self.assertTrue(Path(preview).is_file(), preview)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ["out.ome.tif", "out.ome.tif.qc"])


if __name__ == "__main__":
    unittest.main()
