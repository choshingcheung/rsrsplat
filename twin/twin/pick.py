"""Picking the block up: a scripted sequence, not a learned one.

**Scripted on purpose.** akitech can train a policy for this and evaluate it honestly, but a
demo wants the same motion every time. A checkpoint that succeeds eight times in ten is a
better *robot* and a worse *demonstration*, and the failure lands in front of an audience.

The motion is the obvious four beats -- above, down, close, lift -- and each one is an IK
solve through `akitech.cell.kinematics.Arm`, which is the "use akitech to control" half of
this. The arm's IK works **in its base frame**, and the base is no longer at the world
origin here, so every target is translated before it is solved and never after. That class
says so in its own docstring, and it is the single easiest thing to get wrong in this file.

The actuators are position servos: `ctrl` is where the arm is *asked* to go, and it tracks
there with real dynamics and real lag. So each beat is eased over a number of control ticks
rather than commanded in one step, or the arm snaps and flings the block away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import mujoco
import numpy as np

from . import deps
from .scene import BLOCK_HALF, Placement, block_position

#: The control tick akitech's own loop runs at, so timing measured here is timing it sees.
CONTROL_HZ = 30

#: How far above the block the arm stages before descending. Enough that the jaws clear the
#: cube on the way in rather than knocking it over, which is the commonest scripted-pick
#: failure and looks exactly like a physics bug.
STAGE_ABOVE_M = 0.10

#: How far to lift once holding it. Small: the point is to show it left the surface.
LIFT_M = 0.12

#: Jaw gaps. Open clears a 4 cm cube; closed is deliberately *narrower* than the cube, so
#: the servo keeps squeezing and friction holds it. Closing to exactly 4 cm rests against it.
OPEN_GAP_M = 0.070
GRIP_GAP_M = 0.034


@dataclass
class Beat:
    """One step of the sequence, and what it is for."""

    name: str
    ticks: int
    q: np.ndarray | None = None
    jaw_gap: float | None = None


@dataclass
class Result:
    """What happened, in terms a demo can be judged by."""

    lifted_m: float
    held: bool
    beats: list[str] = field(default_factory=list)
    unreachable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.held and not self.unreachable

    def describe(self) -> str:
        verdict = "PICKED IT UP" if self.ok else "did not pick it up"
        line = f"{verdict}: block rose {self.lifted_m * 1000:+.0f} mm"
        if self.unreachable:
            line += f"; unreachable: {', '.join(self.unreachable)}"
        return line


def _top_down(kin, position: np.ndarray):
    """A grasp from directly above, jaws closing across x.

    Straight down is the one approach that does not depend on which way the block is turned,
    and a cube has no preferred face.
    """
    return kin.pose_from(
        position,
        approach=np.array([0.0, 0.0, -1.0]),
        closing=np.array([1.0, 0.0, 0.0]),
    )


def plan(placement: Placement, block: np.ndarray) -> tuple[list[Beat], list[str]]:
    """Solve the four beats. Returns the beats and the names of any that cannot be reached."""
    kin = deps.kinematics()
    arm = kin.Arm()

    base = np.asarray(placement.arm, dtype=float)
    local = np.asarray(block, dtype=float) - base  # world -> the arm's base frame

    # The TCP goes to the middle of the cube, not its top face: the jaws close around it.
    grasp = local.copy()
    staged = local + np.array([0.0, 0.0, STAGE_ABOVE_M])
    lifted = local + np.array([0.0, 0.0, LIFT_M])

    open_jaw = arm.jaw_for_gap(OPEN_GAP_M)
    grip_jaw = arm.jaw_for_gap(GRIP_GAP_M)

    beats: list[Beat] = []
    unreachable: list[str] = []
    seed = None

    for name, target, jaw, ticks in (
        ("above", staged, open_jaw, 45),
        ("down", grasp, open_jaw, 35),
        ("close", grasp, grip_jaw, 25),
        ("lift", lifted, grip_jaw, 40),
    ):
        result = arm.ik(_top_down(kin, target), seed_rad=seed, jaw_ref=jaw)
        if not result.reachable:
            unreachable.append(f"{name} ({result.pos_mm:.0f}mm out)")
        seed = result.q_rad
        beats.append(Beat(name=name, ticks=ticks, q=np.array(result.q_rad), jaw_gap=jaw))

    return beats, unreachable


def run(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    placement: Placement,
    *,
    on_tick=None,
) -> Result:
    """Drive the sequence and report whether the block actually left the surface."""
    start = block_position(model, data).copy()
    beats, unreachable = plan(placement, start)

    steps_per_tick = max(1, int(round((1.0 / CONTROL_HZ) / model.opt.timestep)))
    nu = model.nu
    current = np.array(data.ctrl[:nu], dtype=float)

    done: list[str] = []
    for beat in beats:
        if beat.q is None:
            continue
        target = np.array(data.ctrl[:nu], dtype=float)
        target[: len(beat.q)] = beat.q[:nu]

        for tick in range(beat.ticks):
            # Ease rather than snap: these are position servos, and a step input throws
            # the block across the room before the jaws are anywhere near it.
            alpha = (tick + 1) / beat.ticks
            data.ctrl[:nu] = current + (target - current) * alpha
            for _ in range(steps_per_tick):
                mujoco.mj_step(model, data)
            if on_tick is not None:
                on_tick()
        current = np.array(data.ctrl[:nu], dtype=float)
        done.append(beat.name)

    end = block_position(model, data)
    lifted = float(end[2] - start[2])
    # Held, not merely nudged: off the surface by more than half its own height, and not
    # simply knocked sideways off the edge and falling.
    held = lifted > BLOCK_HALF[2]

    return Result(lifted_m=lifted, held=held, beats=done, unreachable=unreachable)


__all__ = [
    "CONTROL_HZ",
    "GRIP_GAP_M",
    "LIFT_M",
    "OPEN_GAP_M",
    "STAGE_ABOVE_M",
    "Beat",
    "Result",
    "plan",
    "run",
]
