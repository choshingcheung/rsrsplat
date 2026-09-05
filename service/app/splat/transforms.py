"""Getting a capture into scene coordinates.

Ported from ``akitech/splat`` ``src/splat_io.py``.

A trained splat scene arrives in whatever frame its reconstruction happened to pick. Marble
and most COLMAP-derived captures write OpenCV convention — **+x left, +y down, +z forward** —
and rsrsplat's scene coordinates are z-up, so a capture needs one explicit conversion.

**Which conversion is an empirical question, and it is the caller's to answer.** These are
candidates, not a detector. Applying one silently inside the loader is how a scene ends up
subtly inside out: it renders, it looks like a room, and every height is negated. So the
choice is made once, written down in ``DECISIONS.md``, and passed in.

The prototype found that for the playroom capture *none* of these produced a clean floor,
because a COLMAP frame is not one of a small set of conventions — the up direction has to be
derived from the cloud itself. That is what the ground fitting does, and it supersedes
guessing from this table. These stay for captures that do follow a convention, and because
a named transform is a far better record than a matrix pasted into a call site.
"""

from __future__ import annotations

import numpy as np

from . import quat
from .ply import Splats

TRANSFORMS: dict[str, np.ndarray] = {
    # No change. Start here and look at the render before reaching for anything else.
    "identity": np.eye(3, dtype=np.float32),
    # OpenCV to a y-up frame: negate y and z. The usual first fix when a render is upside
    # down. This is the one the spec names for Marble.
    "opencv_to_opengl": np.diag([1.0, -1.0, -1.0]).astype(np.float32),
    # y-up to z-up: rotate -90 degrees about x.
    "yup_to_zup": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float32),
    # The two above composed, which is what a Marble capture usually needs whole.
    "opencv_to_zup": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=np.float32),
}


def apply(splats: Splats, name: str, scale: float = 1.0) -> Splats:
    """Rotate a cloud into scene coordinates, optionally rescaling it to metres.

    Rotating positions is not enough. Each splat is an ellipsoid with its own orientation,
    so the per-splat quaternions rotate too — miss that and a scene of thin surfaces comes
    out streaked along the wrong axes while the centroids all look correct.

    Scale is isotropic, so the per-splat shapes stay valid under it.
    """
    if name not in TRANSFORMS:
        raise ValueError(f"unknown transform {name!r}. known: {sorted(TRANSFORMS)}")
    rot = TRANSFORMS[name]
    return Splats(
        position=((splats.position @ rot.T) * scale).astype(np.float32),
        rotation=quat.mul(quat.from_mat(rot)[None, :], splats.rotation),
        scale=(splats.scale * scale).astype(np.float32),
        rgba=splats.rgba.copy(),
        sh_degree=splats.sh_degree,
    )
