import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from viewloom.project import Frame, Project, load_project, save_project


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "photos" / "photo.NEF"
        self.source.parent.mkdir()
        self.source.write_bytes(b"original")
        self.frame = Frame(source=self.source, sha256="a" * 64, opacity=0.4,
                           translation=(12.5, -9), rotation=2.5, scale=1.2)
        self.project = Project(frames=[self.frame], reference_id=self.frame.id,
                               blend_mode="screen", crop=[0.1, 0.2, 0.8, 0.9])
        self.path = self.root / "example.viewloom"

    def test_roundtrip_and_moving_whole_project(self):
        save_project(self.project, self.path)
        payload = json.loads(self.path.read_text())
        self.assertEqual(payload["frames"][0]["source"], "photos/photo.NEF")
        moved = self.root / "moved"
        moved.mkdir()
        self.source.parent.rename(moved / "photos")
        self.path.rename(moved / self.path.name)
        loaded = load_project(moved / self.path.name)
        self.assertEqual(loaded.frames[0].source, moved / "photos/photo.NEF")
        self.assertEqual(loaded.frames[0].translation, (12.5, -9))
        self.assertEqual(loaded.frames[0].opacity, 0.4)
        self.assertEqual(loaded.blend_mode, "screen")
        self.assertEqual(loaded.crop, self.project.crop)
        self.assertEqual(loaded.reference_id, self.frame.id)

    def test_missing_source_still_loads(self):
        save_project(self.project, self.path)
        self.source.unlink()
        self.assertEqual(load_project(self.path).frames[0].sha256, "a" * 64)

    def test_interrupted_save_preserves_previous_file(self):
        save_project(self.project, self.path)
        original = self.path.read_bytes()
        self.project.blend_mode = "multiply"
        with patch("viewloom.project.os.replace", side_effect=OSError("Disk write failed")):
            with self.assertRaises(OSError):
                save_project(self.project, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".viewloom-*.tmp")), [])

    def test_rejects_future_schema_bad_transforms_and_duplicate_ids(self):
        save_project(self.project, self.path)
        original = json.loads(self.path.read_text())
        for kind in ("schema", "duplicate", "nan", "opacity", "processing"):
            data = json.loads(json.dumps(original))
            if kind == "schema":
                data["schema_version"] = 100
            elif kind == "duplicate":
                data["frames"].append(data["frames"][0])
            elif kind == "nan":
                data["frames"][0]["transform"]["rotation"] = float("nan")
            elif kind == "opacity":
                data["frames"][0]["opacity"] = 2
            else:
                data["processing"]["version"] = 100
            self.path.write_text(json.dumps(data))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                load_project(self.path)

    def test_cannot_save_over_raw_file(self):
        with self.assertRaises(ValueError):
            save_project(self.project, self.source)
        self.assertEqual(self.source.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
