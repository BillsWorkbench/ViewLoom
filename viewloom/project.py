"""Versioned, non-destructive project data. No Qt dependencies."""
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4


SCHEMA_VERSION = 2
BLEND_MODES = {"average", "normal", "multiply", "screen", "lighten"}
PROCESSING = {"version": 1, "working_space": "linear-sRGB",
              "white_balance": "camera", "auto_bright": False}


@dataclass
class Frame:
    source: Path
    id: str = field(default_factory=lambda: str(uuid4()))
    sha256: str | None = None
    enabled: bool = True
    opacity: float = 1.0
    exposure: float = 0.0
    translation: tuple[float, float] = (0.0, 0.0)
    rotation: float = 0.0
    scale: float = 1.0
    edge_fade: float = 0.0
    contrast: float = 0.0
    saturation: float = 0.0
    warmth: float = 0.0
    tint: float = 0.0
    wb_multipliers: list[float] | None = None
    wb_camera: str = ""


@dataclass
class Project:
    frames: list[Frame] = field(default_factory=list)
    reference_id: str | None = None
    blend_mode: str = "average"
    crop: list[float] | None = None
    canvas_size: tuple[int, int] | None = None
    strength: float = 0.0
    lock_main_transform: bool = False

    @property
    def main_frame_id(self):
        return self.frames[0].id if self.frames else None

    def find(self, frame_id):
        return next((frame for frame in self.frames if frame.id == frame_id), None)


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f"Invalid {name}")
    return float(value)


def load_project(path: Path) -> Project:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != "viewloom":
        raise ValueError("This file is not a Viewloom project.")
    if type(data.get("schema_version")) is not int or data["schema_version"] not in (1, SCHEMA_VERSION):
        raise ValueError("Unsupported project version. Open it with a compatible Viewloom version.")
    if data.get("processing") != PROCESSING:
        raise ValueError("Unsupported processing settings. Open it with a compatible Viewloom version.")
    entries = data.get("frames")
    if not isinstance(entries, list):
        raise ValueError("Invalid photograph list")
    project = Project()
    canvas = data.get("canvas_size")
    if canvas is not None:
        if (not isinstance(canvas, list) or len(canvas) != 2 or
                any(type(v) is not int or v <= 0 or v > 1000000 for v in canvas)):
            raise ValueError("Invalid canvas dimensions")
        project.canvas_size = tuple(canvas)
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid photograph entry")
        identity, source = entry.get("id"), entry.get("source")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Invalid or duplicate photograph identity")
        if not isinstance(source, str) or not source or "\x00" in source:
            raise ValueError("Invalid source path")
        seen.add(identity)
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = path.parent / source_path
        fingerprint = entry.get("sha256")
        if fingerprint is not None and (not isinstance(fingerprint, str) or
                                        re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None):
            raise ValueError("Invalid source fingerprint")
        transform = entry.get("transform", {})
        if not isinstance(transform, dict):
            raise ValueError("Invalid alignment settings")
        translation = transform.get("translation", [0, 0])
        if not isinstance(translation, list) or len(translation) != 2:
            raise ValueError("Invalid translation")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Invalid inclusion setting")
        wb = entry.get("white_balance", {})
        if not isinstance(wb, dict):
            raise ValueError("Invalid white balance")
        gains = wb.get("multipliers")
        if gains is not None:
            if not isinstance(gains, list) or len(gains) != 4:
                raise ValueError("Invalid white-balance multipliers")
            gains = [_number(v, "white-balance multiplier", 0.05, 32) for v in gains]
        camera = wb.get("camera", "")
        if not isinstance(camera, str):
            raise ValueError("Invalid white-balance camera identity")
        project.frames.append(Frame(
            source=source_path.resolve(), id=identity, sha256=fingerprint, enabled=enabled,
            opacity=_number(entry.get("opacity", 1), "opacity", 0, 1),
            exposure=_number(entry.get("exposure", 0), "exposure", -10, 10),
            translation=tuple(_number(v, "translation") for v in translation),
            rotation=_number(transform.get("rotation", 0), "rotation"),
            scale=_number(transform.get("scale", 1), "scale", 0.000001),
            edge_fade=_number(entry.get("edge_fade", 0), "edge fade", 0, 0.5),
            contrast=_number(entry.get("contrast", 0), "contrast", -100, 100),
            saturation=_number(entry.get("saturation", 0), "saturation", -100, 100),
            warmth=_number(wb.get("warmth", 0), "warmth", -100, 100),
            tint=_number(wb.get("tint", 0), "tint", -100, 100),
            wb_multipliers=gains, wb_camera=camera,
        ))
    project.reference_id = data.get("reference_id")
    if project.reference_id is not None and project.reference_id not in seen:
        raise ValueError("The reference photograph does not exist in this project")
    project.strength = _number(data.get("strength", 0), "strength", 0, 1)
    project.lock_main_transform = data.get("lock_main_transform", False)
    if not isinstance(project.lock_main_transform, bool):
        raise ValueError("Invalid main image lock")
    project.blend_mode = data.get("blend_mode", "average")
    if not isinstance(project.blend_mode, str) or project.blend_mode not in BLEND_MODES:
        raise ValueError("Unknown blend mode")
    crop = data.get("crop")
    if crop is not None:
        if not isinstance(crop, list) or len(crop) != 4:
            raise ValueError("Invalid crop")
        crop = [_number(v, "crop", 0, 1) for v in crop]
        if crop[2] <= crop[0] or crop[3] <= crop[1]:
            raise ValueError("Invalid crop bounds")
    project.crop = crop
    return project


def save_project(project: Project, path: Path):
    """Replace the destination only after a complete, flushed temporary write."""
    path = path.resolve()
    if path.suffix.lower() != ".viewloom":
        raise ValueError("Project filenames must end with .viewloom")
    if any(frame.source.resolve() == path for frame in project.frames):
        raise ValueError("A project cannot overwrite an original photograph")
    frames = []
    for frame in project.frames:
        try:
            source = Path(os.path.relpath(frame.source.resolve(), path.parent)).as_posix()
        except ValueError:  # Different Windows drives cannot use a relative path.
            source = str(frame.source)
        frames.append({"id": frame.id, "source": source, "sha256": frame.sha256,
                       "enabled": frame.enabled, "opacity": frame.opacity,
                       "exposure": frame.exposure, "edge_fade": frame.edge_fade,
                       "contrast": frame.contrast, "saturation": frame.saturation,
                       "white_balance": {"warmth": frame.warmth, "tint": frame.tint,
                                         "multipliers": frame.wb_multipliers, "camera": frame.wb_camera},
                       "transform": {"translation": list(frame.translation),
                                     "rotation": frame.rotation, "scale": frame.scale}})
    data = {"format": "viewloom", "schema_version": SCHEMA_VERSION,
            "processing": PROCESSING,
            "reference_id": project.reference_id, "blend_mode": project.blend_mode,
            "crop": project.crop, "canvas_size": project.canvas_size, "frames": frames,
            "main_frame_id": project.main_frame_id, "strength": project.strength,
            "lock_main_transform": project.lock_main_transform}
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".viewloom-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
