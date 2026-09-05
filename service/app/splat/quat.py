"""Vectorised quaternion maths, (w, x, y, z) throughout.

Ported from ``akitech/splat`` ``src/splat_io.py``. The order matches MuJoCo and matches what
a 3DGS PLY stores in ``rot_0..rot_3``, so nothing needs reordering on the way in.

The browser is the other story: Three.js ``Quaternion.set`` takes (x, y, z, w). That
conversion lives in ``web/src/net/`` and happens exactly once. Nothing here reorders.
"""

from __future__ import annotations

import numpy as np


def normalize(q: np.ndarray) -> np.ndarray:
    """Unit-length quaternions. Zero-length input is passed through rather than dividing by 0."""
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    return (q / np.where(norm > 0, norm, 1.0)).astype(np.float32)


def conj(q: np.ndarray) -> np.ndarray:
    """The inverse of a unit quaternion."""
    return np.stack([q[..., 0], -q[..., 1], -q[..., 2], -q[..., 3]], -1).astype(np.float32)


def mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Hamilton product, broadcasting. ``mul(q_body, q_local)`` applies q_local, then q_body."""
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        -1,
    ).astype(np.float32)


def to_mat(q: np.ndarray) -> np.ndarray:
    """(..., 4) unit quaternions to (..., 3, 3) rotation matrices."""
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    return (
        np.stack(
            [
                1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y),
            ],
            -1,
        )
        .reshape(*q.shape[:-1], 3, 3)
        .astype(np.float32)
    )


def from_mat(m: np.ndarray) -> np.ndarray:
    """A single 3x3 rotation matrix to (w, x, y, z).

    Shepperd's method: pick the branch whose divisor is largest, so the square root never
    lands near zero. The naive trace-only formula loses precision at 180 degrees.
    """
    trace = m[0, 0] + m[1, 1] + m[2, 2]
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s]
    return normalize(np.asarray(q, dtype=np.float32))
