"""The PLY parser, against a file written byte by byte here.

Ported from ``akitech/splat`` ``tests/test_splat_io.py``, whose reasoning still holds: the
real captures are gitignored, so on a fresh clone a test that needs one skips and the parser
has no coverage at all. That is the wrong thing to leave unguarded, because all four of the
conversions below are silent when they go wrong.

So this writes a genuine binary little-endian 3DGS PLY with values whose correct parse is
arithmetic rather than a guess, and checks the parser recovers them. It runs anywhere.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from app.splat import SH_C0, Splats, crop, load, sh_degree, sigmoid, splat_count, synth_box

# The property order real trainers emit, including the 45 f_rest terms of SH degree 3.
FIELDS = (
    ["x", "y", "z", "nx", "ny", "nz"]
    + [f"f_dc_{i}" for i in range(3)]
    + [f"f_rest_{i}" for i in range(45)]
    + ["opacity"]
    + [f"scale_{i}" for i in range(3)]
    + [f"rot_{i}" for i in range(4)]
)


def write_ply(path, rows: list[dict], fields: list[str] | None = None) -> None:
    """A genuine binary little-endian 3DGS PLY, in the layout the trainers produce."""
    fields = fields or FIELDS
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(rows)}\n"
        + "".join(f"property float {f}\n" for f in fields)
        + "end_header\n"
    )
    with open(path, "wb") as fh:
        fh.write(header.encode("ascii"))
        for row in rows:
            fh.write(
                struct.pack("<" + "f" * len(fields), *(float(row.get(f, 0.0)) for f in fields))
            )


@pytest.fixture
def sample(tmp_path):
    """Three splats chosen so every conversion has an exact expected answer."""
    rows = [
        # log scale 0 -> exp(0) = 1; opacity logit 0 -> sigmoid 0.5; f_dc 0 -> mid grey.
        # nx/ny/nz set to 9 so that picking them up anywhere would be obvious.
        {
            "x": 1, "y": 2, "z": 3,
            "scale_0": 0, "scale_1": 0, "scale_2": 0,
            "opacity": 0.0, "rot_0": 1.0,
            "f_dc_0": 0.0, "f_dc_1": 0.0, "f_dc_2": 0.0,
            "nx": 9, "ny": 9, "nz": 9,
        },
        # log scale ln(2) -> 2; a large positive logit -> alpha ~ 1.
        {
            "x": -1, "y": 0, "z": 0.5,
            "scale_0": np.log(2), "scale_1": np.log(4), "scale_2": np.log(0.5),
            "opacity": 30.0, "rot_0": 0.0, "rot_1": 1.0,
            "f_dc_0": 1.0 / SH_C0 * 0.5, "f_dc_1": 0.0, "f_dc_2": 0.0,
        },
        # A large negative logit -> alpha ~ 0, and a deliberately unnormalised quaternion.
        {
            "x": 0, "y": 0, "z": 0,
            "scale_0": 0, "scale_1": 0, "scale_2": 0,
            "opacity": -30.0,
            "rot_0": 2.0, "rot_1": 2.0, "rot_2": 2.0, "rot_3": 2.0,
            "f_dc_0": 0.0, "f_dc_1": 0.0, "f_dc_2": 0.0,
        },
    ]
    path = tmp_path / "sample.ply"
    write_ply(path, rows)
    return path


def test_it_parses_positions_and_count(sample):
    s = load(sample)
    assert len(s) == 3
    assert s.position[0].tolist() == [1.0, 2.0, 3.0]
    assert s.position.dtype == np.float32


def test_scales_are_exponentiated(sample):
    """Stored in LOG space. Left raw, a stored scale of 0 means a zero-size splat."""
    s = load(sample)
    assert s.scale[0].tolist() == pytest.approx([1.0, 1.0, 1.0])
    assert s.scale[1].tolist() == pytest.approx([2.0, 4.0, 0.5], rel=1e-5)


def test_opacity_is_put_through_a_sigmoid(sample):
    """Stored PRE-sigmoid. Real captures reach past +-30 in the logits."""
    s = load(sample)
    assert s.rgba[0, 3] == pytest.approx(0.5)
    assert s.rgba[1, 3] == pytest.approx(1.0, abs=1e-6)
    assert s.rgba[2, 3] == pytest.approx(0.0, abs=1e-6)


def test_colour_comes_from_band_zero_spherical_harmonics(sample):
    """rgb = 0.5 + C0 * f_dc, so an f_dc of zero is mid grey, not black."""
    s = load(sample)
    assert s.rgba[0, :3].tolist() == pytest.approx([0.5, 0.5, 0.5])
    assert s.rgba[1, 0] == pytest.approx(1.0)


def test_quaternions_are_normalised(sample):
    s = load(sample)
    assert np.linalg.norm(s.rotation, axis=1) == pytest.approx([1, 1, 1], abs=1e-6)
    assert s.rotation[2].tolist() == pytest.approx([0.5, 0.5, 0.5, 0.5])


def test_normals_are_ignored(sample):
    """nx, ny, nz are written by every trainer and are meaningless."""
    s = load(sample)
    assert not hasattr(s, "normal")
    assert s.position[0].tolist() == [1.0, 2.0, 3.0]


def test_sh_degree_is_read_from_the_property_list(sample):
    assert sh_degree(sample) == 3
    assert load(sample).sh_degree == 3


def test_sh_degree_of_a_file_with_no_rest_terms(tmp_path):
    path = tmp_path / "dc_only.ply"
    write_ply(
        path,
        [{"x": 0, "y": 0, "z": 0, "rot_0": 1.0, "opacity": 0.0}],
        fields=[f for f in FIELDS if not f.startswith("f_rest_")],
    )
    assert sh_degree(path) == 0


def test_an_unrecognised_rest_count_reports_unknown_rather_than_guessing(tmp_path):
    path = tmp_path / "odd.ply"
    write_ply(
        path,
        [{"x": 0, "y": 0, "z": 0, "rot_0": 1.0}],
        fields=[f for f in FIELDS if not f.startswith("f_rest_")] + ["f_rest_0", "f_rest_1"],
    )
    assert sh_degree(path) is None


# ---- header-only reads ------------------------------------------------------------------


def test_splat_count_comes_from_the_header_without_parsing_the_body(sample):
    """The cross-check against the browser's parse, and it must be cheap on a 370 MB file."""
    assert splat_count(sample) == 3


def test_a_file_that_is_not_a_ply_fails_with_a_readable_error(tmp_path):
    path = tmp_path / "not.ply"
    path.write_bytes(b"this is not a ply file at all")
    with pytest.raises(ValueError, match="end_header"):
        splat_count(path)


# ---- the container ----------------------------------------------------------------------


def test_indexing_keeps_every_field_in_step():
    cloud = synth_box(100, seed=0)
    sub = cloud[np.array([3, 7, 11])]
    assert len(sub) == 3
    for i, j in enumerate([3, 7, 11]):
        assert sub.position[i].tolist() == cloud.position[j].tolist()
        assert sub.rgba[i].tolist() == cloud.rgba[j].tolist()
        assert sub.scale[i].tolist() == cloud.scale[j].tolist()


def test_bounds_and_extent_agree():
    cloud = synth_box(500, centre=(1, 2, 3), size=(2, 4, 6), seed=1)
    lo, hi = cloud.bounds()
    assert (hi - lo).tolist() == pytest.approx(cloud.extent().tolist())
    assert cloud.extent()[2] > cloud.extent()[0]


def test_synth_box_fills_the_extents_it_was_asked_for():
    """A5 checks PCA half-extents against these numbers, so the generator must be honest."""
    cloud = synth_box(20_000, centre=(0.5, -1.0, 2.0), size=(0.6, 0.6, 0.85), seed=2)
    assert cloud.extent().tolist() == pytest.approx([0.6, 0.6, 0.85], abs=0.02)
    assert cloud.position.mean(0).tolist() == pytest.approx([0.5, -1.0, 2.0], abs=0.01)


def test_crop_returns_indices_inside_the_box():
    cloud = Splats(
        position=np.array([[0, 0, 0], [5, 5, 5], [1, 1, 1]], dtype=np.float32),
        rotation=np.tile(np.float32([1, 0, 0, 0]), (3, 1)),
        scale=np.full((3, 3), 0.01, np.float32),
        rgba=np.ones((3, 4), np.float32),
    )
    assert crop(cloud, [-0.5, -0.5, -0.5], [2, 2, 2]).tolist() == [0, 2]


def test_sigmoid_does_not_overflow_on_real_logits():
    x = np.array([-800.0, -30.0, 0.0, 30.0, 800.0], dtype=np.float32)
    out = sigmoid(x)
    assert np.all(np.isfinite(out))
    assert out[0] == pytest.approx(0.0)
    assert out[-1] == pytest.approx(1.0)
    assert out[2] == pytest.approx(0.5)


def test_report_names_the_numbers_worth_checking(sample):
    text = load(sample).report()
    for expected in ("splats", "sh degree", "bbox", "extent", "opacity"):
        assert expected in text
