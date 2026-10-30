"""Linear-light compositing shared by previews and tiled, atomic TIFF export."""
from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageCms
import tifffile

from .alignment import Pose, affine_coefficients
from .imaging import decode_linear, linear_to_srgb
from .project import BLEND_MODES
from . import __version__
from .development import develop_pixels


class Cancelled(Exception):
    pass


def check_cancel(cancellation):
    if cancellation is not None and cancellation.is_set():
        raise Cancelled("Cancelled")


def effective_opacity(project, frame):
    main = project.main_frame_id
    if frame.id == main:
        return frame.opacity + project.strength * (1 - frame.opacity)
    return frame.opacity * (1 - project.strength)


def edge_mask(array_size, source_size, amount):
    width, height = array_size
    if amount <= 0:
        return np.ones((height, width), np.float32)
    radius = min(source_size) * amount
    def ramp(count, extent):
        points = (np.arange(count, dtype=np.float32) + 0.5) * extent / count
        distance = np.minimum(points, extent - points)
        t = np.clip(distance / radius, 0, 1)
        return t * t * (3 - 2 * t)
    return ramp(height, source_size[1])[:, None] * ramp(width, source_size[0])[None, :]


def output_geometry(project, max_side=None):
    if project.canvas_size is None:
        raise ValueError("Load photographs to establish a canvas first.")
    width, height = project.canvas_size
    crop = project.crop or [0, 0, 1, 1]
    left, top = math.floor(crop[0] * width), math.floor(crop[1] * height)
    right, bottom = math.ceil(crop[2] * width), math.ceil(crop[3] * height)
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("The crop must have a positive width and height inside the canvas.")
    width, height = right - left, bottom - top
    factor = min(1, max_side / max(width, height)) if max_side else 1
    return (left, top, right, bottom), (max(1, round(width * factor)), max(1, round(height * factor)))


def accumulate(destination, rgb_premultiplied, coverage, opacity, mode):
    """Accumulate one layer. RGB input already contains resampled edge coverage."""
    alpha = coverage * opacity
    if mode == "average":
        destination[..., :3] += rgb_premultiplied * opacity
        destination[..., 3] += alpha
        return
    backdrop_alpha = destination[..., 3:4]
    source_alpha = alpha[..., None]
    source = np.divide(rgb_premultiplied, coverage[..., None],
                       out=np.zeros_like(rgb_premultiplied), where=coverage[..., None] > 1e-8)
    backdrop = np.divide(destination[..., :3], backdrop_alpha,
                         out=np.zeros_like(source), where=backdrop_alpha > 1e-8)
    if mode == "normal":
        blended = source
    elif mode == "multiply":
        blended = backdrop * source
    elif mode == "screen":
        blended = 1 - (1 - backdrop) * (1 - source)
    elif mode == "lighten":
        blended = np.maximum(backdrop, source)
    else:
        raise ValueError("Unknown blend mode")
    destination[..., :3] = ((1 - source_alpha) * destination[..., :3] +
                            (1 - backdrop_alpha) * source_alpha * source +
                            backdrop_alpha * source_alpha * blended)
    destination[..., 3] = alpha + destination[..., 3] * (1 - alpha)


def finish_tile(accumulated, mode):
    if mode == "average":
        weight = accumulated[..., 3:4]
        return np.divide(accumulated[..., :3], weight,
                         out=np.zeros_like(accumulated[..., :3]), where=weight > 1e-8)
    # Flatten remaining transparency onto black; empty canvas is always black.
    return accumulated[..., :3]


class Renderer:
    def __init__(self):
        self.cache = OrderedDict()

    def clear(self):
        self.cache.clear()

    def _source(self, frame, preview, cancellation):
        check_cancel(cancellation)
        stat = frame.source.stat()
        key = (str(frame.source), frame.sha256, stat.st_size, stat.st_mtime_ns,
               tuple(frame.wb_multipliers) if frame.wb_multipliers else None, frame.warmth, frame.tint)
        if preview and key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        rgb = decode_linear(frame.source, frame.sha256, frame)
        check_cancel(cancellation)
        size = (rgb.shape[1], rgb.shape[0])
        if preview:
            factor = min(1, 1600 / max(size))
            rgb = cv2.resize(rgb, (max(1, round(size[0] * factor)), max(1, round(size[1] * factor))),
                             interpolation=cv2.INTER_AREA)
            self.cache[key] = (rgb, size)
            while len(self.cache) > 4:
                self.cache.popitem(last=False)
        return rgb, size

    def render(self, project, accumulated, max_side=None, cancellation=None, progress=None):
        bounds, (width, height) = output_geometry(project, max_side)
        if project.blend_mode not in BLEND_MODES:
            raise ValueError("Unknown blend mode")
        frames = [frame for frame in reversed(project.frames) if frame.enabled and effective_opacity(project, frame) > 0]
        if not frames:
            raise ValueError("Include at least one photograph with opacity above zero.")
        accumulated[:] = 0
        left, top, right, bottom = bounds
        out_x, out_y = width / (right - left), height / (bottom - top)
        rows = 128
        for index, frame in enumerate(frames):
            check_cancel(cancellation)
            if not math.isfinite(frame.exposure) or not -10 <= frame.exposure <= 10:
                raise ValueError("Exposure must be between −10 and +10 stops.")
            if progress:
                progress(index / len(frames), f"Reading {frame.source.name}")
            rgb, source_size = self._source(frame, max_side is not None, cancellation)
            a, b, c, d, tx, ty = affine_coefficients(source_size, project.canvas_size, Pose.from_frame(frame))
            sx, sy = source_size[0] / rgb.shape[1], source_size[1] / rgb.shape[0]
            matrix = np.array([[a * sx * out_x, c * sy * out_x,
                                (tx - left) * out_x + 0.5 * (a * sx + c * sy) * out_x - 0.5],
                               [b * sx * out_y, d * sy * out_y,
                                (ty - top) * out_y + 0.5 * (b * sx + d * sy) * out_y - 0.5]], dtype=np.float64)
            # Coverage is warped separately to avoid darkening rotated image edges.
            mask = edge_mask((rgb.shape[1], rgb.shape[0]), source_size, frame.edge_fade)
            source_pixels = rgb * mask[..., None] if frame.edge_fade > 0 else rgb
            for y in range(0, height, rows):
                check_cancel(cancellation)
                count = min(rows, height - y)
                tile_matrix = matrix.copy()
                tile_matrix[1, 2] -= y
                warped = cv2.warpAffine(source_pixels, tile_matrix, (width, count), flags=cv2.INTER_LINEAR)
                coverage = cv2.warpAffine(mask, tile_matrix, (width, count), flags=cv2.INTER_LINEAR)
                # Apply exposure to unassociated colors, preserving edge coverage.
                unassociated = np.divide(warped, coverage[..., None], out=np.zeros_like(warped),
                                         where=coverage[..., None] > 1e-8)
                warped = develop_pixels(unassociated, frame) * coverage[..., None]
                accumulate(accumulated[y:y + count], warped, coverage, effective_opacity(project, frame), project.blend_mode)
                if progress:
                    progress((index + (y + count) / height) / len(frames),
                             f"Compositing {index + 1} of {len(frames)}")
            del rgb, mask, source_pixels
        check_cancel(cancellation)

    def preview(self, project, cancellation=None):
        _, (width, height) = output_geometry(project, 1200)
        accumulated = np.zeros((height, width, 4), np.float32)
        self.render(project, accumulated, 1200, cancellation)
        linear = finish_tile(accumulated, project.blend_mode)
        return np.ascontiguousarray(np.rint(linear_to_srgb(linear) * 255).astype(np.uint8))

    def export(self, project, destination, cancellation=None, progress=None):
        destination = Path(destination).resolve()
        if destination.suffix.lower() not in {".tif", ".tiff"}:
            raise ValueError("Choose a .tif or .tiff filename.")
        if any(destination == frame.source.resolve() for frame in project.frames):
            raise ValueError("Export cannot overwrite an original photograph.")
        _, (width, height) = output_geometry(project)
        temporary = None
        # A disk-backed accumulator bounds RAM use regardless of photograph count.
        with tempfile.TemporaryDirectory(prefix=".viewloom-render-", dir=destination.parent) as directory:
            root = Path(directory)
            accumulator = np.memmap(root / "accumulator.bin", mode="w+", dtype=np.float32,
                                    shape=(height, width, 4))
            pixels = None
            try:
                self.render(project, accumulator, cancellation=cancellation,
                            progress=(lambda value, message: progress(value * 0.85, message)) if progress else None)
                pixels = np.memmap(root / "pixels.bin", mode="w+", dtype=np.uint16, shape=(height, width, 3))
                for y in range(0, height, 128):
                    check_cancel(cancellation)
                    linear = finish_tile(accumulator[y:y + 128], project.blend_mode)
                    pixels[y:y + 128] = np.rint(linear_to_srgb(linear) * 65535).astype(np.uint16)
                profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
                check_cancel(cancellation)
                temporary = root / "export.tiff"
                if progress:
                    progress(0.9, "Writing 16-bit TIFF")
                tifffile.imwrite(temporary, pixels, photometric="rgb", rowsperstrip=128,
                                 bigtiff=width * height * 6 > 3_800_000_000,
                                 software=f"Viewloom {__version__}", metadata=None,
                                 description=json.dumps({"blend_mode": project.blend_mode,
                                                         "working_space": "linear-sRGB",
                                                         "encoding": "sRGB", "bit_depth": 16}),
                                 extratags=[(34675, "B", len(profile), profile, False)])
                check_cancel(cancellation)
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                if progress:
                    progress(1.0, "Export complete")
            finally:
                # Explicitly close file mappings for Windows temporary-folder cleanup.
                accumulator._mmap.close()
                if pixels is not None:
                    pixels._mmap.close()
        return width, height
