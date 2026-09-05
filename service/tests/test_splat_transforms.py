"""Coordinate conversions, pinned to known points.

The spec asks for "a test asserting a known point maps where expected", because this is a
place where a wrong answer still looks like a room — it renders, it is recognisable, and
every height is negated.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.splat import TRANSFORMS, Splats, apply, quat


def one_splat(position=(1.0, 2.0, 3.0), rotation=(1.0, 0.0, 0.0, 0.0)) -> Splats:
    return Splats(
        position=np.array([position], dtype=np.float32),
        rotation=np.array([rotation], dtype=np.float32),
        scale=np.full((1, 3), 0.01, dtype=np.float32),
        rgba=np.ones((1, 4), dtype=np.float32),
        sh_degree=3,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("identity", [1.0, 2.0, 3.0]),
        # Negate y and z: OpenCV's +y down, +z forward becomes y-up.
        ("opencv_to_opengl", [1.0, -2.0, -3.0]),
        # -90 degrees about x: y-up becomes z-up.
        ("yup_to_zup", [1.0, 3.0, -2.0]),
        # The two composed.
        ("opencv_to_zup", [1.0, -3.0, 2.0]),
    ],
)
def test_a_known_point_lands_where_expected(name, expected):
    moved = apply(one_splat(), name)
    assert moved.position[0].tolist() == pytest.approx(expected)


def test_opencv_to_zup_is_the_composition_of_the_other_two():
    """Stated as a fact in the table; asserted here so the table cannot rot."""
    composed = TRANSFORMS["yup_to_zup"] @ TRANSFORMS["opencv_to_opengl"]
    np.testing.assert_allclose(composed, TRANSFORMS["opencv_to_zup"], atol=1e-6)


@pytest.mark.parametrize("name", sorted(TRANSFORMS))
def test_every_transform_is_a_rotation_not_a_reflection(name):
    """Determinant +1. A reflection would mirror the whole scene, and a mirrored room is
    still a convincing room — which is exactly why this needs asserting rather than eyeing."""
    matrix = TRANSFORMS[name]
    assert np.linalg.det(matrix) == pytest.approx(1.0)
    np.testing.assert_allclose(matrix @ matrix.T, np.eye(3), atol=1e-6)


def test_per_splat_orientations_rotate_with_the_cloud():
    """Rotating positions alone is not enough.

    Each splat is an ellipsoid with its own orientation. Miss this and the centroids all
    look right while a scene of thin surfaces comes out streaked along the wrong axes.
    """
    original = one_splat(rotation=(0.5, 0.5, 0.5, 0.5))
    for name, matrix in TRANSFORMS.items():
        moved = apply(original, name)
        expected = matrix @ quat.to_mat(original.rotation[0])
        np.testing.assert_allclose(
            quat.to_mat(moved.rotation[0]),
            expected,
            atol=1e-6,
            err_msg=f"{name} did not rotate the splat's own orientation",
        )


def test_scale_is_isotropic_so_splat_shapes_stay_valid():
    moved = apply(one_splat(), "identity", scale=2.5)
    assert moved.position[0].tolist() == pytest.approx([2.5, 5.0, 7.5])
    assert moved.scale[0].tolist() == pytest.approx([0.025, 0.025, 0.025])


def test_transformed_clouds_keep_their_sh_degree():
    assert apply(one_splat(), "opencv_to_zup").sh_degree == 3


def test_an_unknown_transform_names_the_ones_that_exist():
    with pytest.raises(ValueError, match="opencv_to_zup"):
        apply(one_splat(), "guess_it_for_me")
