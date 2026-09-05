"""Verification: the three ways a downloaded file lies about what it is."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
from conftest import splat_ply

from marble.verify import (
    NotAPly,
    NotGaussianSplats,
    Truncated,
    VerifyError,
    inspect,
    sh_degree,
    verify,
)


def write(tmp_path: Path, data: bytes, name: str = "world.ply") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------------------
# The happy answer
# ---------------------------------------------------------------------------------------


def test_a_real_splat_ply_reads_back(tmp_path: Path):
    info = verify(write(tmp_path, splat_ply(count=1000, degree=3)))

    assert info.count == 1000
    assert info.sh_degree == 3
    assert info.format == "binary_little_endian"
    assert info.file_bytes == info.expected_bytes


@pytest.mark.parametrize("degree", [0, 1, 2, 3])
def test_every_sh_degree_is_recognised(tmp_path: Path, degree: int):
    assert verify(write(tmp_path, splat_ply(degree=degree))).sh_degree == degree


def test_the_summary_is_fit_to_print(tmp_path: Path):
    summary = verify(write(tmp_path, splat_ply(count=1234))).summary()
    assert "1,234 splats" in summary
    assert "SH degree 3" in summary


# ---------------------------------------------------------------------------------------
# 1. It is not a PLY at all
# ---------------------------------------------------------------------------------------


def test_an_expired_url_returns_xml_and_that_is_caught(tmp_path: Path):
    """A signed URL past its expiry answers 200 with an error document."""
    body = b'<?xml version="1.0"?><Error><Code>ExpiredToken</Code></Error>'
    with pytest.raises(NotAPly, match="not a PLY"):
        verify(write(tmp_path, body))


def test_an_html_error_page_is_caught(tmp_path: Path):
    with pytest.raises(NotAPly):
        verify(write(tmp_path, b"<!DOCTYPE html><html><body>502 Bad Gateway"))


def test_a_header_that_never_ends_is_not_a_ply(tmp_path: Path):
    with pytest.raises(NotAPly):
        verify(write(tmp_path, b"ply\nformat binary_little_endian 1.0\n" + b"x" * 200))


# ---------------------------------------------------------------------------------------
# 2. It is truncated
# ---------------------------------------------------------------------------------------


def test_a_short_body_is_reported_in_bytes(tmp_path: Path):
    """The header states the exact size, so this holds even with no Content-Length."""
    with pytest.raises(Truncated, match="bytes short"):
        verify(write(tmp_path, splat_ply(count=1000, truncate=4096)))


def test_one_missing_byte_is_still_truncated(tmp_path: Path):
    with pytest.raises(Truncated):
        verify(write(tmp_path, splat_ply(count=10, truncate=1)))


def test_a_longer_file_is_not_an_error(tmp_path: Path):
    """Some writers append comments past the last vertex. Extra is not missing."""
    verify(write(tmp_path, splat_ply(count=10) + b"trailing junk"))


# ---------------------------------------------------------------------------------------
# 3. It is a PLY, but not splats
# ---------------------------------------------------------------------------------------


def test_a_mesh_export_has_vertices_and_is_still_rejected(tmp_path: Path):
    mesh = (
        "ply\nformat binary_little_endian 1.0\n"
        "element vertex 3\n"
        "property float x\nproperty float y\nproperty float z\n"
        "element face 1\nproperty list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii") + struct.pack("<9f", *range(9))

    with pytest.raises(NotGaussianSplats, match="missing"):
        verify(write(tmp_path, mesh))


def test_an_ascii_ply_is_refused_with_a_reason(tmp_path: Path):
    ascii_ply = (
        "ply\nformat ascii 1.0\nelement vertex 1\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float f_dc_0\nproperty float f_dc_1\nproperty float f_dc_2\n"
        "property float opacity\n"
        "property float scale_0\nproperty float scale_1\nproperty float scale_2\n"
        "property float rot_0\nproperty float rot_1\nproperty float rot_2\n"
        "property float rot_3\n"
        "end_header\n0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
    ).encode("ascii")

    with pytest.raises(NotGaussianSplats, match="binary_little_endian"):
        verify(write(tmp_path, ascii_ply))


def test_a_list_property_in_the_vertex_element_is_rejected(tmp_path: Path):
    weird = (
        "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
        "property list uchar int things\nend_header\n"
    ).encode("ascii")

    with pytest.raises(NotGaussianSplats, match="list properties"):
        verify(write(tmp_path, weird))


def test_a_partial_sh_band_is_not_a_degree():
    with pytest.raises(NotGaussianSplats, match="whole SH degree"):
        sh_degree(tuple(f"f_rest_{i}" for i in range(7)))


def test_an_empty_cloud_is_rejected(tmp_path: Path):
    with pytest.raises(NotGaussianSplats, match="no vertices"):
        verify(write(tmp_path, splat_ply(count=0)))


# ---------------------------------------------------------------------------------------
# Cross-checking against what the API said
# ---------------------------------------------------------------------------------------


def test_a_count_that_disagrees_with_the_api_is_an_error(tmp_path: Path):
    path = write(tmp_path, splat_ply(count=1000))
    with pytest.raises(VerifyError, match="1,000 splats"):
        verify(path, expect_count=1832004)


def test_a_matching_count_passes(tmp_path: Path):
    assert verify(write(tmp_path, splat_ply(count=1000)), expect_count=1000).count == 1000


def test_inspect_does_not_read_the_whole_file(tmp_path: Path):
    """The answer is in the first kilobyte; reading 1.5M Gaussians to get it is not free."""
    big = splat_ply(count=200_000)
    info = inspect(write(tmp_path, big))
    assert info.count == 200_000
    assert info.header_bytes < 2048
