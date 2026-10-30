import math
import unittest

from viewloom.alignment import Pose, affine_coefficients


def mapped(coefficients, x, y):
    a, b, c, d, tx, ty = coefficients
    return a * x + c * y + tx, b * x + d * y + ty


class GeometryTests(unittest.TestCase):
    def test_default_same_size_is_identity(self):
        self.assertEqual(affine_coefficients((6000, 4000), (6000, 4000), Pose()),
                         (1, 0, 0, 1, 0, 0))

    def test_different_aspects_fit_and_center(self):
        matrix = affine_coefficients((4000, 2000), (4000, 6000), Pose())
        self.assertEqual(mapped(matrix, 0, 0), (0, 2000))
        self.assertEqual(mapped(matrix, 4000, 2000), (4000, 4000))

    def test_rotation_scale_and_translation_preserve_subject_center(self):
        matrix = affine_coefficients((6000, 4000), (3000, 2000), Pose(50, -25, 90, 2))
        center = mapped(matrix, 3000, 2000)
        self.assertAlmostEqual(center[0], 1550)
        self.assertAlmostEqual(center[1], 975)
        right = mapped(matrix, 3100, 2000)
        self.assertAlmostEqual(right[0], center[0])
        self.assertAlmostEqual(right[1], center[1] + 100)

    def test_preview_and_full_resolution_agree(self):
        pose = Pose(-44, 13, -12, 1.15)
        matrix = affine_coefficients((6000, 4000), (4500, 3000), pose)
        a, b, c, d, tx, ty = matrix
        preview_matrix = (a * 4, b * 4, c * 4, d * 4, tx, ty)
        full = mapped(matrix, 3600, 1800)
        preview = mapped(preview_matrix, 900, 450)
        self.assertTrue(all(math.isclose(x, y) for x, y in zip(full, preview)))
