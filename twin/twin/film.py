"""Recording the pick, so there is something to watch without running anything.

Two reasons this exists rather than only the interactive viewer. A file can be watched on a
phone, and it is the demo's last line of insurance: `capture/DEMO.md`'s tiers end with "a
recorded run, played full screen", and that insurance only exists if someone records it while
it works.

**The camera has to be told where to look.** MuJoCo's default free camera frames the whole
model, and this model is a room eight metres across with a 30 cm arm somewhere in it -- the
first render came back at a mean pixel value of 8 out of 255, which reads as "rendering is
broken" and is really "the subject is four pixels wide". So the camera is placed against the
placement, not against the model.
"""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np

from .scene import Placement

#: Far enough back to hold the arm and the block, close enough that they fill the frame.
DISTANCE_M = 0.95

#: Looking slightly down, the angle a person standing at the desk would have.
ELEVATION_DEG = -22.0

#: Around from straight-on, so the grasp is seen from the side rather than end-on -- a
#: gripper closing directly towards the camera hides the one thing worth seeing.
AZIMUTH_DEG = 55.0

FPS = 30


def camera(placement: Placement, *, distance: float = DISTANCE_M) -> mujoco.MjvCamera:
    """A camera pointed at the work, not at the room."""
    cam = mujoco.MjvCamera()
    cam.type = mujoco.mjtCamera.mjCAMERA_FREE

    # Halfway between the arm's base and the block, lifted to the height of the work.
    ax, ay, az = placement.arm
    bx, by, bz = placement.block
    cam.lookat = np.array([(ax + bx) / 2, (ay + by) / 2, (az + bz) / 2 + 0.06])
    cam.distance = distance
    cam.elevation = ELEVATION_DEG
    cam.azimuth = AZIMUTH_DEG
    return cam


def record(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    placement: Placement,
    destination: Path,
    *,
    run,
    width: int = 960,
    height: int = 720,
    fps: int = FPS,
    hold_s: float = 1.0,
) -> Path:
    """Run the pick, writing a frame per control tick, and hold on the result.

    ``run`` is the callable that drives the arm -- injected rather than imported so this
    module has no opinion about what is being filmed.
    """
    import imageio.v2 as imageio

    destination.parent.mkdir(parents=True, exist_ok=True)
    cam = camera(placement)
    renderer = mujoco.Renderer(model, height, width)
    frames: list[np.ndarray] = []

    def grab() -> None:
        renderer.update_scene(data, camera=cam)
        frames.append(renderer.render())

    grab()  # the scene before anything moves
    result = run(model, data, placement, on_tick=grab)

    # Hold on the lifted block, or nobody sees what happened.
    for _ in range(int(hold_s * fps)):
        for _ in range(max(1, int(round((1.0 / fps) / model.opt.timestep)))):
            mujoco.mj_step(model, data)
        grab()

    renderer.close()
    imageio.mimwrite(destination, frames, fps=fps, quality=8, macro_block_size=1)
    return destination, result


def orbit(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    placement: Placement,
    destination: Path,
    *,
    seconds: float = 4.0,
    width: int = 960,
    height: int = 720,
    fps: int = FPS,
) -> Path:
    """A slow turn around the scene, with nothing moving.

    Useful for judging whether the room came out the right way up, which is the one thing a
    still frame cannot settle and a rotation settles immediately.
    """
    import imageio.v2 as imageio

    destination.parent.mkdir(parents=True, exist_ok=True)
    cam = camera(placement, distance=1.6)
    renderer = mujoco.Renderer(model, height, width)
    frames = []

    total = int(seconds * fps)
    for i in range(total):
        cam.azimuth = 360.0 * i / total
        # Ease the elevation too, so the floor and the ceiling both come into view.
        cam.elevation = -20.0 + 12.0 * math.sin(2 * math.pi * i / total)
        renderer.update_scene(data, camera=cam)
        frames.append(renderer.render())

    renderer.close()
    imageio.mimwrite(destination, frames, fps=fps, quality=8, macro_block_size=1)
    return destination


__all__ = ["AZIMUTH_DEG", "DISTANCE_M", "ELEVATION_DEG", "FPS", "camera", "orbit", "record"]
