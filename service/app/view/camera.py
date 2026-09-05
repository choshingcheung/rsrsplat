"""A camera you drive with the mouse.

Written into the MuJoCo model's camera each frame rather than kept separately, because that
is the camera MJWarp raytraces through -- there is no second view matrix anywhere.
"""

from __future__ import annotations

import math

import numpy as np


class Orbit:
    """Azimuth, elevation and distance about a target point."""

    def __init__(self, target=(0.0, 0.0, 1.0), distance: float = 4.0):
        self.az = 90.0
        self.el = -12.0
        self.dist = distance
        self.target = np.asarray(target, dtype=np.float64).copy()

    def eye(self) -> np.ndarray:
        a, e = math.radians(self.az), math.radians(self.el)
        return self.target + self.dist * np.array(
            [math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)]
        )

    def write(self, model, camera_id: int) -> None:
        """Put this camera into the model, as position and quaternion."""
        import mujoco

        eye = self.eye()
        fwd = self.target - eye
        fwd = fwd / np.linalg.norm(fwd)

        right = np.cross(fwd, [0.0, 0.0, 1.0])
        norm = np.linalg.norm(right)
        # Looking straight down: the cross product degenerates, so pick an arbitrary right.
        right = np.array([1.0, 0.0, 0.0]) if norm < 1e-6 else right / norm
        up = np.cross(right, fwd)

        model.cam_pos[camera_id] = eye
        # A camera looks down its own -z, so the third column is -forward.
        mat = np.stack([right, up, -fwd], axis=1)
        mujoco.mju_mat2Quat(model.cam_quat[camera_id], mat.flatten())

    # -- input ----------------------------------------------------------------------------

    def orbit(self, dx: float, dy: float) -> None:
        self.az += dx * 0.3
        # Stop just short of the poles, where the up vector degenerates and the view rolls.
        self.el = max(-89.0, min(89.0, self.el + dy * 0.3))

    def pan(self, dx: float, dy: float) -> None:
        a = math.radians(self.az)
        right = np.array([-math.sin(a), math.cos(a), 0.0])
        self.target += (-dx * right + dy * np.array([0.0, 0.0, 1.0])) * self.dist * 0.0015

    def zoom(self, amount: float) -> None:
        self.dist = max(0.15, self.dist * (0.9**amount))

    def frame(self, positions: np.ndarray) -> None:
        """Point at a cloud, using a robust extent.

        Percentiles rather than the bounding box: a trained scene carries floaters well
        outside the room, and framing on those puts the room at a speck in the distance.
        """
        lo = np.percentile(positions, 2, axis=0)
        hi = np.percentile(positions, 98, axis=0)
        self.target = (lo + hi) / 2
        self.dist = float(np.max(hi - lo)) * 0.9 or 4.0


__all__ = ["Orbit"]
