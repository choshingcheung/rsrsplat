"""Splat I/O.

The physics service does not need Gaussians — the browser owns them and they never cross the
socket. This package exists so the browser's parser has a reference implementation to be
checked against, and so a capture can be inspected headlessly before anyone trusts it.
"""

from .ply import (
    SH_C0,
    SH_DEGREE_BY_REST,
    Splats,
    crop,
    header_lines,
    load,
    property_names,
    sh_degree,
    sigmoid,
    splat_count,
    synth_box,
)
from .transforms import TRANSFORMS, apply

__all__ = [
    "SH_C0",
    "SH_DEGREE_BY_REST",
    "TRANSFORMS",
    "Splats",
    "apply",
    "crop",
    "header_lines",
    "load",
    "property_names",
    "sh_degree",
    "sigmoid",
    "splat_count",
    "synth_box",
]
