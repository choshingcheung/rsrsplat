"""Choosing which splats to draw.

Ported from ``akitech/splat`` ``src/render.py``. Both of these exist for the same reason: a
raytraced splat frame costs time roughly in proportion to how many Gaussians a ray has to
wade through, so the cheapest frame is the one with fewer splats in it.

Measured on this machine, at 960x720: 80k splats is 42 ms, 200k is 98 ms, 400k is 285 ms.
The playroom capture holds 1,495,461. It is not a question of whether to subsample.
"""

from __future__ import annotations

import numpy as np

from .ply import Splats


def subsample(splats: Splats, keep: int, *, seed: int = 0) -> Splats:
    """Take a random subset. Deterministic, so a scene looks the same twice."""
    n = len(splats)
    if keep >= n:
        return splats
    idx = np.random.default_rng(seed).choice(n, size=keep, replace=False)
    return splats[np.sort(idx)]


def drop_faint(splats: Splats, min_alpha: float = 0.02) -> Splats:
    """Discard splats too transparent to contribute.

    Usually a large fraction of a real scene, and free: they cost raytracing time and add
    nothing a viewer can see. On the playroom capture only 42.7% clear an alpha of 0.1.
    """
    return splats[splats.rgba[:, 3] >= min_alpha]


__all__ = ["drop_faint", "subsample"]
