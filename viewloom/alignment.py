"""Shared alignment geometry in full-resolution canvas pixels."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Pose:
    x: float = 0.0
    y: float = 0.0
    rotation: float = 0.0
    scale: float = 1.0

    @classmethod
    def from_frame(cls, frame):
        return cls(*frame.translation, frame.rotation, frame.scale)

    def apply(self, frame):
        frame.translation = (self.x, self.y)
        frame.rotation = self.rotation
        frame.scale = self.scale


def affine_coefficients(source_size, canvas_size, pose):
    """Map oriented source pixels into canvas pixels, rotating around its center.

    At the default pose, a photograph fits within the fixed canvas. Translation
    uses canvas pixels, rotation is clockwise in image coordinates, and scale is
    relative to that initial fit. Preview and later export share this geometry.
    Returns (a, b, c, d, tx, ty) for x'=a*x+c*y+tx; y'=b*x+d*y+ty.
    """
    width, height = source_size
    canvas_width, canvas_height = canvas_size
    scale = min(canvas_width / width, canvas_height / height) * pose.scale
    radians = math.radians(pose.rotation)
    a, b = scale * math.cos(radians), scale * math.sin(radians)
    c, d = -b, a
    tx = canvas_width / 2 + pose.x - a * width / 2 - c * height / 2
    ty = canvas_height / 2 + pose.y - b * width / 2 - d * height / 2
    return a, b, c, d, tx, ty
