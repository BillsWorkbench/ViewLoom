from pathlib import Path
from io import BytesIO
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

import numpy as np
import tifffile
from PIL import ImageCms

from viewloom.composite import Cancelled, Renderer, accumulate, edge_mask, effective_opacity, finish_tile, output_geometry
from viewloom.imaging import linear_to_srgb
from viewloom.project import Frame, Project


class SyntheticRenderer(Renderer):
    def __init__(self, sources):
        super().__init__()
        self.sources = sources

    def _source(self, frame, preview, cancellation):
        rgb = self.sources[frame.id]
        return rgb, (rgb.shape[1], rgb.shape[0])


class BlendTests(unittest.TestCase):
    def test_strength_preserves_base_opacities_and_drives_rendered_output(self):
        main = Frame(Path("main.nef"), opacity=0.4)
        other = Frame(Path("other.nef"), opacity=0.8)
        project = Project(frames=[main, other], strength=0.5, canvas_size=(8, 6))
        self.assertAlmostEqual(effective_opacity(project, main), 0.7)
        self.assertAlmostEqual(effective_opacity(project, other), 0.4)
        renderer = SyntheticRenderer({main.id: np.full((6, 8, 3), 0.8, np.float32),
                                      other.id: np.full((6, 8, 3), 0.2, np.float32)})
        state = np.zeros((6, 8, 4), np.float32)
        renderer.render(project, state)
        np.testing.assert_allclose(finish_tile(state, "average"), (0.7 * 0.8 + 0.4 * 0.2) / 1.1, atol=1e-6)
        project.strength = 1
        renderer.render(project, state)
        np.testing.assert_allclose(finish_tile(state, "average"), 0.8, atol=1e-6)
        self.assertEqual((main.opacity, other.opacity), (0.4, 0.8))
        project.strength = 0
        self.assertEqual(effective_opacity(project, main), 0.4)
        self.assertEqual(effective_opacity(project, other), 0.8)
        project.frames.reverse()
        self.assertEqual(project.main_frame_id, other.id)
        project.strength = 1
        renderer.render(project, state)
        np.testing.assert_allclose(finish_tile(state, "average"), 0.2, atol=1e-6)

    def test_edge_fade_softens_a_smaller_image_without_changing_its_center(self):
        top = Frame(Path("top.nef"), scale=0.5)
        bottom = Frame(Path("bottom.nef"))
        project = Project(frames=[top, bottom], canvas_size=(32, 32), blend_mode="normal")
        renderer = SyntheticRenderer({top.id: np.ones((32, 32, 3), np.float32),
                                      bottom.id: np.zeros((32, 32, 3), np.float32)})
        state = np.zeros((32, 32, 4), np.float32)
        renderer.render(project, state)
        hard = finish_tile(state, "normal").copy()
        top.edge_fade = 0.25
        renderer.render(project, state)
        soft = finish_tile(state, "normal")
        self.assertGreater(hard[16, 8, 0], soft[16, 8, 0])
        self.assertLess(soft[16, 8, 0], soft[16, 10, 0])
        self.assertAlmostEqual(float(soft[16, 16, 0]), 1)
        self.assertEqual(float(soft[0, 0, 0]), 0)
        project.blend_mode = "average"
        renderer.render(project, state)
        averaged = finish_tile(state, "average")
        self.assertLess(averaged[16, 8, 0], averaged[16, 16, 0])
        mask = edge_mask((32, 16), (64, 32), 0.25)
        np.testing.assert_allclose(mask, mask[::-1, ::-1])
        self.assertGreater(float(mask[8, 16]), float(mask[8, 0]))

    def test_modes_against_known_linear_values(self):
        for mode, expected in {"average": 0.5, "normal": 0.8, "multiply": 0.16,
                               "screen": 0.84, "lighten": 0.8}.items():
            with self.subTest(mode=mode):
                state = np.zeros((1, 1, 4), np.float32)
                mask = np.ones((1, 1), np.float32)
                accumulate(state, np.full((1, 1, 3), 0.2, np.float32), mask, 1, mode)
                accumulate(state, np.full((1, 1, 3), 0.8, np.float32), mask, 1, mode)
                np.testing.assert_allclose(finish_tile(state, mode), expected, atol=1e-6)

    def test_weighted_average_ignores_zero_opacity_and_empty_edges(self):
        state = np.zeros((1, 2, 4), np.float32)
        mask = np.array([[1, 0.25]], np.float32)
        source = np.full((1, 2, 3), 0.6, np.float32) * mask[..., None]
        accumulate(state, source, mask, 0.5, "average")
        accumulate(state, np.ones_like(source), np.ones_like(mask), 0, "average")
        np.testing.assert_allclose(finish_tile(state, "average"), 0.6, atol=1e-6)

    def test_partial_alpha_blending_preserves_transparent_backdrop(self):
        state = np.zeros((1, 1, 4), np.float32)
        mask = np.ones((1, 1), np.float32)
        accumulate(state, np.full((1, 1, 3), 0.2, np.float32), mask, 0.5, "multiply")
        accumulate(state, np.full((1, 1, 3), 0.8, np.float32), mask, 0.5, "multiply")
        np.testing.assert_allclose(state[..., :3], 0.29, atol=1e-6)
        np.testing.assert_allclose(state[..., 3], 0.75, atol=1e-6)

    def test_normal_topmost_layer_and_exclusion(self):
        top = Frame(Path("top.nef"), opacity=0.5)
        bottom = Frame(Path("bottom.nef"))
        project = Project(frames=[top, bottom], blend_mode="normal", canvas_size=(8, 6))
        renderer = SyntheticRenderer({top.id: np.full((6, 8, 3), 0.8, np.float32),
                                      bottom.id: np.full((6, 8, 3), 0.2, np.float32)})
        state = np.zeros((6, 8, 4), np.float32)
        renderer.render(project, state)
        np.testing.assert_allclose(finish_tile(state, "normal"), 0.5, atol=1e-6)
        top.enabled = False
        renderer.render(project, state)
        np.testing.assert_allclose(finish_tile(state, "normal"), 0.2, atol=1e-6)
        bottom.enabled = False
        with self.assertRaises(ValueError):
            renderer.render(project, state)

    def test_crop_and_translation_use_full_canvas_coordinates(self):
        frame = Frame(Path("test.nef"), translation=(1, 0))
        pixels = np.zeros((6, 8, 3), np.float32)
        pixels[2, 3] = 0.5
        project = Project(frames=[frame], canvas_size=(8, 6), crop=[0.25, 0, 0.75, 1])
        self.assertEqual(output_geometry(project), ((2, 0, 6, 6), (4, 6)))
        renderer = SyntheticRenderer({frame.id: pixels})
        state = np.zeros((6, 4, 4), np.float32)
        renderer.render(project, state)
        expected = np.zeros((6, 4, 3), np.float32)
        expected[2, 2] = 0.5
        np.testing.assert_allclose(finish_tile(state, "average"), expected)

    def test_tiff_is_uint16_rgb_with_icc_and_matches_preview(self):
        frame = Frame(Path("test.nef"), exposure=1)
        project = Project(frames=[frame], canvas_size=(8, 6))
        renderer = SyntheticRenderer({frame.id: np.full((6, 8, 3), 0.2, np.float32)})
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "result.tiff"
            self.assertEqual(renderer.export(project, destination), (8, 6))
            with tifffile.TiffFile(destination) as image:
                exported = image.asarray()
                self.assertEqual(exported.dtype, np.dtype("uint16"))
                self.assertEqual(image.pages[0].photometric.name, "RGB")
                profile = ImageCms.ImageCmsProfile(BytesIO(image.pages[0].tags[34675].value))
                self.assertIn("sRGB", ImageCms.getProfileName(profile))
                expected = round(float(linear_to_srgb(np.array(0.4))) * 65535)
                np.testing.assert_allclose(exported, expected, atol=1)
            preview = renderer.preview(project)
            np.testing.assert_allclose(preview / 255, exported / 65535, atol=1 / 255)
            # An interrupted export leaves the previous finished file intact.
            before = destination.read_bytes()
            cancellation = Event()
            cancellation.set()
            with self.assertRaises(Cancelled):
                renderer.export(project, destination, cancellation)
            self.assertEqual(destination.read_bytes(), before)
            cancellation = Event()
            def cancel_during_write(value, message):
                if value >= 0.9:
                    cancellation.set()
            with self.assertRaises(Cancelled):
                renderer.export(project, destination, cancellation, cancel_during_write)
            self.assertEqual(destination.read_bytes(), before)
            with patch("viewloom.composite.tifffile.imwrite", side_effect=OSError("Disk full")):
                with self.assertRaises(OSError):
                    renderer.export(project, destination)
            self.assertEqual(destination.read_bytes(), before)
            self.assertEqual(list(Path(directory).glob(".viewloom-render-*")), [])
