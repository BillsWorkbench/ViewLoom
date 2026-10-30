from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from viewloom.development import develop_pixels, recipe_key, white_balance_kwargs
from viewloom.imaging import linear_to_srgb
from viewloom.project import Frame, Project, load_project, save_project


class DevelopmentTests(unittest.TestCase):
    def test_neutral_and_exposure(self):
        pixels = np.array([[[0.05, 0.2, 0.4]]], np.float32)
        frame = Frame(Path("photo.nef"))
        np.testing.assert_array_equal(develop_pixels(pixels, frame), pixels)
        frame.exposure = 1
        np.testing.assert_allclose(develop_pixels(pixels, frame), pixels * 2)

    def test_saturation_zero_and_contrast(self):
        pixels = np.array([[[0.05, 0.2, 0.4], [0.04, 0.04, 0.04], [0.7, 0.7, 0.7]]], np.float32)
        frame = Frame(Path("photo.nef"), saturation=-100)
        gray = develop_pixels(pixels, frame)
        np.testing.assert_allclose(gray[..., 0], gray[..., 1], atol=1e-6)
        np.testing.assert_allclose(gray[..., 1], gray[..., 2], atol=1e-6)
        frame.saturation, frame.contrast = 0, 50
        contrasted = develop_pixels(pixels, frame)
        self.assertLess(float(contrasted[0, 1, 0]), float(pixels[0, 1, 0]))
        self.assertGreater(float(contrasted[0, 2, 0]), float(pixels[0, 2, 0]))

    def test_white_balance_is_applied_to_raw_gains(self):
        raw = SimpleNamespace(camera_whitebalance=[2, 1, 1.5, 0],
                              daylight_whitebalance=[2, 1, 1.5, 1], color_desc=b"RGBG")
        frame = Frame(Path("photo.nef"))
        self.assertEqual(white_balance_kwargs(raw, frame), {"use_camera_wb": True})
        frame.warmth = 100
        parameters = white_balance_kwargs(raw, frame)
        np.testing.assert_allclose(parameters["user_wb"], [4, 1, 0.75, 1])
        frame.wb_multipliers, frame.warmth, frame.tint = [1.8, 1, 1.2, 1], 0, 100
        parameters = white_balance_kwargs(raw, frame)
        np.testing.assert_allclose(parameters["user_wb"], [1.8, 2 / 3, 1.2, 2 / 3])

    def test_development_roundtrip_and_legacy_defaults(self):
        frame = Frame(Path("photo.nef"), sha256="a" * 64, exposure=0.5, contrast=20,
                      saturation=-15, warmth=12, tint=-4, wb_multipliers=[2, 1, 1.5, 1], wb_camera="Nikon")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "develop.viewloom"
            save_project(Project(frames=[frame]), path)
            self.assertEqual(recipe_key(load_project(path).frames[0]), recipe_key(frame))
            import json
            data = json.loads(path.read_text())
            data["schema_version"] = 1
            for key in ("contrast", "saturation", "white_balance"):
                data["frames"][0].pop(key)
            path.write_text(json.dumps(data))
            loaded = load_project(path).frames[0]
            self.assertEqual((loaded.contrast, loaded.saturation, loaded.warmth, loaded.tint), (0, 0, 0, 0))
            self.assertIsNone(loaded.wb_multipliers)
