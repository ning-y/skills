#!/usr/bin/env python3
"""Self-checks for stitch_evos_m7000.py (no Fiji required)."""

import os
from dataclasses import replace
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stitch_evos_m7000 as stitch  # noqa: E402


def make_tile(directory, slide, field, channel, x_um, y_um, value,
              px=0.5, width=20, height=30):
    path = os.path.join(
        directory, f"scan_{slide}_R_p00_0_A01f{field:02d}d{channel}.TIF")
    data = np.full((height, width), value, dtype=np.uint16)
    tifffile.imwrite(
        path, data, ome=True,
        metadata={"axes": "YX", "PhysicalSizeX": px, "PhysicalSizeY": px,
                  "Channel": {"Name": f"C{channel}"},
                  "Plane": {"PositionX": x_um, "PositionY": y_um}})
    return path


class DiscoveryTests(unittest.TestCase):
    def test_discovery_pairs_channels_and_ignores_merged_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            for channel in (0, 1):
                make_tile(directory, "Slide A", 0, channel, 0, 0, 10)
                make_tile(directory, "Slide A", 1, channel, 8, 0, 20)
            # The EVOS merged preview uses _M_ and must not become a raw tile.
            Path(directory,
                 "scan_Slide A_M_p00_0_A01f00d0.TIF").write_bytes(b"preview")

            units = stitch.discover(directory)

            self.assertEqual(list(units), [("Slide A", "00")])
            channels = stitch.validate_unit(units[("Slide A", "00")])
            self.assertEqual(sorted(channels), [0, 1])
            self.assertEqual(len(channels[0]), 2)
            self.assertEqual(channels[0][0].key, channels[1][0].key)

    def test_unpaired_channel_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            make_tile(directory, "Slide", 0, 0, 0, 0, 10)
            make_tile(directory, "Slide", 0, 1, 0, 0, 10)
            make_tile(directory, "Slide", 1, 0, 8, 0, 10)
            channels = stitch.discover(directory)[("Slide", "00")]

            with self.assertRaisesRegex(ValueError, "does not pair"):
                stitch.validate_unit(channels)

    def test_workdir_reuse_rejects_other_sources_without_replacing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second, work = [Path(directory, name) for name in ("first", "second", "work")]
            for path in (first, second, work):
                path.mkdir()
            for raw, value in ((first, 100), (second, 900)):
                make_tile(str(raw), "Slide", 0, 0, 0, 0, value)
            old = stitch.discover(first)[("Slide", "00")]
            new = stitch.discover(second)[("Slide", "00")]
            stitch.link_tiles(work, old)
            stitch.link_tiles(work, old)
            link = work / old[0][0].link_name
            with self.assertRaisesRegex(ValueError, "fresh workdir"):
                stitch.link_tiles(work, new)
            self.assertEqual(link.resolve(), Path(old[0][0].path).resolve())
            np.testing.assert_array_equal(tifffile.imread(link), np.full((30, 20), 100))

    def test_existing_regular_work_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            raw, work = Path(directory, "raw"), Path(directory, "work")
            raw.mkdir()
            work.mkdir()
            make_tile(str(raw), "Slide", 0, 0, 0, 0, 100)
            channels = stitch.discover(raw)[("Slide", "00")]
            destination = work / channels[0][0].link_name
            destination.write_bytes(b"user data")
            with self.assertRaisesRegex(ValueError, "fresh workdir"):
                stitch.link_tiles(work, channels)
            self.assertEqual(destination.read_bytes(), b"user data")


class ClusterTests(unittest.TestCase):
    def tile(self, x_um, y_um, field):
        return stitch.Tile(
            path=f"f{field}", slide="S", pas="00", well="0", area="01",
            fld=field, channel=0, px=0.5, x_um=x_um, y_um=y_um,
            width=20, height=30, dtype=np.dtype("uint16"), name="C0")

    def test_gap_splits_rois(self):
        tiles = []
        field = 0
        for y in (0, 12):
            for x in (0, 8):
                tiles.append(self.tile(x, y, field))
                field += 1
        # A second ROI well beyond one tile width.
        for y in (0, 12):
            for x in (200, 208):
                tiles.append(self.tile(x, y, field))
                field += 1

        rois = stitch.cluster_rois(tiles)

        self.assertEqual(sorted(len(roi) for roi in rois), [4, 4])

    def test_single_tile_is_its_own_roi(self):
        tiles = [self.tile(0, 0, 0)]
        self.assertEqual(stitch.cluster_rois(tiles), [tiles])

    def test_singleton_column_stays_in_its_overlapping_roi(self):
        positions = [(0, 0), (0, 1200), (0, 2400), (0, 3600), (1800, 0)]
        tiles = [replace(self.tile(x * 0.5, y * 0.5, n), width=2048, height=1536)
                 for n, (x, y) in enumerate(positions)]
        rois = stitch.cluster_rois(tiles)
        self.assertEqual([{t.key for t in roi} for roi in rois],
                         [{t.key for t in tiles}])

    def test_repeated_nonoverlapping_vertical_gaps_remain_separate_rois(self):
        for step in (1536, 100000):
            with self.subTest(step=step):
                tiles = [replace(self.tile(0, n * step * 0.5, n),
                                 width=2048, height=1536) for n in range(4)]
                rois = stitch.cluster_rois(tiles)
                self.assertEqual([{t.key for t in roi} for roi in rois],
                                 [{t.key} for t in tiles])
                bounds = [(0, n * step, 2048, 1536) for n in range(4)]
                positions = stitch.compress_layout(bounds)
                self.assertEqual(max(y + 1536 for x, y in positions),
                                 4 * 1536 + 3 * min(100, step - 1536))


class ConfigTests(unittest.TestCase):
    def tile(self, name_x, name_y=0.0, field=0):
        return stitch.Tile(
            path=f"f{field}", slide="S", pas="00", well="0", area="01",
            fld=field, channel=0, px=0.5, x_um=name_x, y_um=name_y,
            width=20, height=30, dtype=np.dtype("uint16"), name="C0")

    def test_stage_config_normalizes_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "TileConfiguration.txt")
            text = stitch.write_stage_config(
                path, [self.tile(10, 20, 0), self.tile(18, 20, 1)])

        self.assertEqual(text.splitlines()[0], "dim = 2")
        self.assertIn("w0a01f00000d0.tif; ; (0.00, 0.00)", text)
        self.assertIn("w0a01f00001d0.tif; ; (16.00, 0.00)", text)

    def test_config_choice_requires_one_origin_and_every_tile(self):
        stage = "dim = 2\n"
        one = "a.tif; ; (0.0, 0.0)\nb.tif; ; (10.0, 0.0)\n"
        two = "a.tif; ; (0.0, 0.0)\nb.tif; ; (0.0, 0.0)\n"
        short = "a.tif; ; (0.0, 0.0)\n"

        self.assertEqual(stitch.choose_config(stage, one, 2),
                         (one, "registered"))
        self.assertEqual(stitch.choose_config(stage, two, 2),
                         (stage, "stage"))
        self.assertEqual(stitch.choose_config(stage, short, 2),
                         (stage, "stage"))

    def test_retarget_only_changes_the_channel_token(self):
        text = "w0a1f00000d0.tif; ; (0.0, 0.0)\n"
        self.assertEqual(
            stitch.retarget_config(text, 0, 1),
            "w0a1f00000d1.tif; ; (0.0, 0.0)\n")


class CompositeTests(unittest.TestCase):
    def tile(self, x_um, field):
        return stitch.Tile(
            path=f"f{field}", slide="S", pas="00", well="0", area="01",
            fld=field, channel=0, px=0.5, x_um=x_um, y_um=0,
            width=20, height=30, dtype=np.dtype("uint16"), name="C0")

    def test_channels_are_stacked_on_one_shared_canvas(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for channel, value in ((0, 100), (1, 200)):
                for index, roi_x in enumerate((0, 200)):
                    path = os.path.join(directory, f"c{channel}r{index}.tif")
                    tifffile.imwrite(
                        path, np.full((30, 20), value, dtype=np.uint16))
                    paths[(channel, index)] = path
            channels = {0: [self.tile(0, 0)], 1: [self.tile(0, 0)]}
            rois = [[self.tile(0, 0)], [self.tile(200, 1)]]
            stitched = {channel: [paths[(channel, 0)], paths[(channel, 1)]]
                        for channel in channels}

            stack, positions, bounds = stitch.composite(
                channels, rois, stitched, margin=0)

            self.assertEqual(stack.shape, (2, 30, 40))
            self.assertEqual(stack.dtype, np.uint16)
            self.assertTrue((stack[0, :, :20] == 100).all())
            self.assertTrue((stack[1, :, :20] == 200).all())
            self.assertTrue((stack[0, :, 20:] == 100).all())
            self.assertEqual(positions, [(0.0, 0.0), (20.0, 0.0)])

    def test_ome_output_keeps_channels_and_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "out.tif")
            stack = np.zeros((2, 8, 10), dtype=np.uint16)
            stack[1] = 7

            stitch.write_ome(path, stack, 0.62, ["DAPI", "GFP"])

            with tifffile.TiffFile(path) as tif:
                self.assertEqual(tif.series[0].shape, (2, 8, 10))
                self.assertEqual(tif.series[0].axes, "CYX")
                ome = tif.ome_metadata
            self.assertIn('Name="DAPI"', ome)
            self.assertIn('Name="GFP"', ome)
            self.assertIn('PhysicalSizeX="0.62"', ome)

            single = os.path.join(directory, "single.tif")
            stitch.write_ome(single, np.zeros((1, 4, 5), dtype=np.uint16),
                             0.62, ["DAPI"])
            with tifffile.TiffFile(single) as tif:
                self.assertEqual(tif.series[0].shape, (4, 5))


if __name__ == "__main__":
    unittest.main()
