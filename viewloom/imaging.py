"""RAW preview decoding, independent of the user interface."""
from dataclasses import dataclass
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image
import rawpy
from .development import develop_pixels, recipe_key, white_balance_kwargs


SUPPORTED_SUFFIXES = {".nef", ".dng"}


def linear_to_srgb(rgb):
    rgb = np.clip(rgb, 0, 1)
    return np.where(rgb <= 0.0031308, rgb * 12.92,
                    1.055 * rgb ** (1 / 2.4) - 0.055)


def decode_linear(path, expected_sha256=None, frame=None):
    """Decode one oriented RAW in linear sRGB, verifying its original identity."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Please choose a Nikon NEF or Leica DNG file.")
    before = path.stat()
    identity = fingerprint(path)
    if expected_sha256 is None or identity != expected_sha256:
        raise ValueError(f"Original verification failed for {path.name}. Import or relink the original first.")
    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(**white_balance_kwargs(raw, frame), no_auto_bright=True, gamma=(1, 1),
                              output_bps=16, output_color=rawpy.ColorSpace.sRGB)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError(f"{path.name} changed while being read.")
    return rgb.astype(np.float32) / 65535


@dataclass
class Preview:
    rgb: np.ndarray
    sha256: str
    width: int
    height: int
    recipe: tuple = recipe_key(None)


def fingerprint(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def decode_preview(path: Path, expected_sha256=None, max_side=1600, frame=None) -> Preview:
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("Please choose a Nikon NEF or Leica DNG file.")
    before = path.stat()
    identity = fingerprint(path)
    if expected_sha256 is not None and identity != expected_sha256:
        raise ValueError("This photograph differs from the original saved in the project. "
                         "Relink to the unchanged original, or import this file as a new photograph.")
    with rawpy.imread(str(path)) as raw:
        sizes = raw.sizes
        width, height = sizes.width, sizes.height
        if sizes.flip in (5, 6):
            width, height = height, width
        # Half-resolution demosaicing bounds the memory cost for interactive previews.
        linear = raw.postprocess(**white_balance_kwargs(raw, frame), no_auto_bright=True, gamma=(1, 1),
                                 output_bps=16, output_color=rawpy.ColorSpace.sRGB,
                                 half_size=True)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("The source changed while it was being read. Please import it again.")
    channels = []
    for channel in range(3):
        plane = Image.fromarray(linear[:, :, channel].astype(np.float32) / 65535)
        plane.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        channels.append(np.asarray(plane))
    rgb = np.clip(np.stack(channels, axis=-1), 0, 1)
    if frame is not None:
        rgb = develop_pixels(rgb, frame)
    display = np.where(rgb <= 0.0031308, rgb * 12.92,
                       1.055 * rgb ** (1 / 2.4) - 0.055)
    return Preview(np.ascontiguousarray(np.rint(display * 255).astype(np.uint8)),
                   identity, width, height, recipe_key(frame))
