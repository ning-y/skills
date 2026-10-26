#!/usr/bin/env python3
"""Physical TIFF and OME validation regressions; no Fiji required."""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stitch_evos_m7000 as stitch


class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write_tile(self, channel=0, field="00", pixels=None, plane=None,
                   image_name="DAPI", channel_name="Channel 0", pages=1,
                   shape=(6, 8), dtype="uint16"):
        path = Path(self.directory.name,
                    f"scan_Slide_R_p00_0_A01f{field}d{channel}.TIF")
        root = ET.Element("OME", xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06")
        image = ET.SubElement(root, "Image", ID="Image:0", Name=image_name)
        attributes = dict(ID="Pixels:0", DimensionOrder="XYZCT", Type=dtype,
                          SizeX="8", SizeY="6", SizeZ="1", SizeC="1", SizeT="1",
                          PhysicalSizeX="0.5", PhysicalSizeY="0.5")
        for key, value in (pixels or {}).items():
            if value is None:
                attributes.pop(key, None)
            else:
                attributes[key] = str(value)
        pixel_el = ET.SubElement(image, "Pixels", attributes)
        ET.SubElement(pixel_el, "Channel", ID="Channel:0:0", Name=channel_name,
                      SamplesPerPixel="1")
        ET.SubElement(pixel_el, "TiffData", IFD="0", PlaneCount="1")
        attributes = dict(TheC="0", TheZ="0", TheT="0", PositionX="10", PositionY="20")
        attributes.update({key: str(value) for key, value in (plane or {}).items()})
        ET.SubElement(pixel_el, "Plane", attributes)
        description = ET.tostring(root, encoding="ascii").decode("ascii")
        with tifffile.TiffWriter(path) as writer:
            for index in range(pages):
                writer.write(np.zeros(shape, dtype=dtype), photometric="minisblack",
                             description=description if index == 0 else None,
                             metadata=None)
        return path

    def read(self, path):
        return stitch.read_tile(str(path), stitch.TILE_RE.match(path.name))

    def test_single_physical_plane_accepts_evos_phantom_sizec(self):
        tile = self.read(self.write_tile(pixels={"SizeC": 2}, plane={"TheC": 1}))
        self.assertEqual((tile.width, tile.height, tile.px), (8, 6, 0.5))
        self.assertEqual(tile.name, "DAPI")

    def test_multiple_ifds_are_rejected_even_when_first_page_is_2d(self):
        with self.assertRaisesRegex(ValueError, "one physical 2-D TIFF page"):
            self.read(self.write_tile(pages=2))

    def test_metadata_dimensions_must_match_physical_page(self):
        for axis in "XY":
            with self.subTest(axis=axis):
                with self.assertRaisesRegex(ValueError, "do not match TIFF page"):
                    self.read(self.write_tile(pixels={"Size" + axis: 99}))

    def test_z_and_time_axes_are_not_phantom_channels(self):
        for axis in "ZT":
            with self.subTest(axis=axis):
                with self.assertRaisesRegex(ValueError, "unsupported OME Z/T"):
                    self.read(self.write_tile(pixels={"Size" + axis: 2}))
                with self.assertRaisesRegex(ValueError, "unsupported OME Z/T"):
                    self.read(self.write_tile(plane={"The" + axis: 1}))

    def test_equivalent_si_units_pair_after_normalization(self):
        first = self.read(self.write_tile())
        second = self.read(self.write_tile(
            channel=1, image_name="GFP",
            pixels={"PhysicalSizeX": 500, "PhysicalSizeXUnit": "nm",
                    "PhysicalSizeY": 0.0005, "PhysicalSizeYUnit": "mm"},
            plane={"PositionX": 0.00001, "PositionXUnit": "m",
                   "PositionY": 20000, "PositionYUnit": "nm"}))
        channels = stitch.validate_unit({0: [first], 1: [second]})
        self.assertAlmostEqual(channels[1][0].px, 0.5)
        self.assertAlmostEqual(channels[1][0].x_um, 10)
        self.assertAlmostEqual(channels[1][0].y_um, 20)

    def test_explicit_micrometre_symbol_matches_omitted_unit(self):
        tile = self.read(self.write_tile(
            pixels={"PhysicalSizeXUnit": "µm", "PhysicalSizeYUnit": "µm"},
            plane={"PositionXUnit": "µm", "PositionYUnit": "µm"}))
        self.assertEqual((tile.px, tile.x_um, tile.y_um), (0.5, 10, 20))

    def test_unknown_and_nonphysical_units_are_rejected(self):
        for unit in ("furlong", "pixel", "reference frame", "s"):
            for element, attribute in (("pixels", "PhysicalSizeXUnit"),
                                       ("plane", "PositionYUnit")):
                with self.subTest(unit=unit, attribute=attribute):
                    with self.assertRaisesRegex(ValueError, "unsupported .* unit"):
                        self.read(self.write_tile(**{element: {attribute: unit}}))

    def test_nonfinite_positions_and_invalid_calibrations_are_rejected(self):
        for value in ("nan", "inf", "-inf"):
            for attribute in ("PositionX", "PositionY"):
                with self.subTest(value=value, attribute=attribute):
                    with self.assertRaisesRegex(ValueError, "must be finite"):
                        self.read(self.write_tile(plane={attribute: value}))
        for value in ("nan", "inf", "-inf", "0", "-1"):
            for attribute in ("PhysicalSizeX", "PhysicalSizeY"):
                with self.subTest(value=value, attribute=attribute):
                    with self.assertRaisesRegex(ValueError, "positive and finite"):
                        self.read(self.write_tile(pixels={attribute: value}))

    def test_both_calibrations_are_required(self):
        for axis in "XY":
            with self.subTest(axis=axis):
                with self.assertRaisesRegex(ValueError, "lacks PhysicalSize" + axis):
                    self.read(self.write_tile(pixels={"PhysicalSize" + axis: None}))

    def test_anisotropy_is_rejected_after_unit_conversion(self):
        with self.assertRaisesRegex(ValueError, "anisotropic"):
            self.read(self.write_tile(pixels={"PhysicalSizeY": 600,
                                              "PhysicalSizeYUnit": "nm"}))

    def test_image_name_semantics_and_channel_name_fallback(self):
        self.assertEqual(self.read(self.write_tile(
            image_name="GFP", channel_name="Channel 0")).name, "GFP")
        for image_name in ("", "Image0", "Image0:"):
            with self.subTest(image_name=image_name):
                self.assertEqual(self.read(self.write_tile(
                    image_name=image_name, channel_name="GFP")).name, "GFP")

    def test_duplicate_field_identities_are_not_hidden_by_sets(self):
        self.write_tile(field="0")
        self.write_tile(field="00")
        channels = stitch.discover(self.directory.name)[("Slide", "00")]
        with self.assertRaisesRegex(ValueError, "duplicate field identity"):
            stitch.validate_unit(channels)

    def test_paired_positions_have_point_zero_one_pixel_tolerance(self):
        first = self.read(self.write_tile())
        second = self.read(self.write_tile(channel=1, image_name="GFP"))
        stitch.validate_unit({0: [first], 1: [replace(second, x_um=10.0049,
                                                   y_um=20.0049)]})
        for attribute in ("x_um", "y_um"):
            with self.subTest(attribute=attribute):
                with self.assertRaisesRegex(ValueError, "stage positions"):
                    stitch.validate_unit({0: [first], 1: [replace(
                        second, **{attribute: getattr(second, attribute) + 0.0051})]})

    def test_channel_names_calibration_dimensions_and_dtype_are_consistent(self):
        first = self.read(self.write_tile())
        second = replace(first, fld=1)
        for changes, message in (({"name": "GFP"}, "channel names"),
                                 ({"px": 0.6}, "µm/px"),
                                 ({"width": 9}, "expected 8x6"),
                                 ({"height": 7}, "expected 8x6"),
                                 ({"dtype": np.dtype("uint8")}, "expected uint16")):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    stitch.validate_unit({0: [first, replace(second, **changes)]})


if __name__ == "__main__":
    unittest.main()
