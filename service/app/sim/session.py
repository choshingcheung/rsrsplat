"""One live simulation: the objects in it, and the poses that come back out.

A session owns a MuJoCo model and its data, and nothing else. It does not know about
sockets, asyncio, or timing policy -- the service drives it -- which is what lets the whole
of S5 be tested headlessly with a loop and a clock.

**Adding an object rebuilds the model.** MuJoCo models are immutable once compiled, so
physicalising a second object means compiling a new one. The state of everything already in
the scene is carried across by joint name, because the alternative is that physicalising a
crate teleports the dishwasher's door back to shut. Joint names are stable across rebuilds
precisely so this can work: they are ``{object_id}__{part}_j``.

**Stepping and broadcasting are decoupled.** The timestep stays at 0.002 because that is
what keeps contacts stable; the wire wants about 30 Hz. So the service asks for however many
steps a wall-clock interval is worth, and samples poses on its own cadence.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import mujoco
import numpy as np

from ..mjcf.scene import SceneObject, check_world, compile_scene, object_from_selection
from ..protocol import (
    Obstacle,
    PhysicsObject,
    PhysicsPart,
    PoseBatch,
    PoseUpdate,
    Selection,
    SimStatus,
    WorldFrame,
)
from ..schema import friction as schema_friction
from ..schema import mass_kg
from ..schema import mobility as schema_mobility
from ..schema import parts as schema_parts
from ..schema import region as schema_region

#: MuJoCo's integration step. Small enough that a 200 kg crate does not pass through a floor
#: between two steps. Not the rate anything is sent at.
TIMESTEP = 0.002

#: How far the simulation will catch up in one call, in seconds of simulated time. Without a
#: cap, a service that was paused or descheduled for a minute would try to run a minute of
#: physics in one blocking burst and stall the socket it is meant to be feeding.
MAX_CATCHUP = 0.1


@dataclass
class Session:
    """One scene, its objects, and its clock."""

    world: WorldFrame
    session_id: str = field(default_factory=lambda: f"ses_{uuid.uuid4().hex[:8]}")

    #: The room's solid geometry. Fixed for the life of a session: it comes from the
    #: capture, and the capture does not change while it is loaded.
    obstacles: tuple[Obstacle, ...] = ()

    objects: dict[str, SceneObject] = field(default_factory=dict)

    #: Joints the user is driving, by body name, in wire units. Survives a rebuild, so
    #: physicalising a second object does not let a held door swing shut.
    held: dict[str, float] = field(default_factory=dict)
    running: bool = True
    step_count: int = 0

    _model: Any = field(default=None, repr=False)
    _data: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        check_world(self.world)
        self._rebuild()

    # -- the model ----------------------------------------------------------------------

    @property
    def model(self):
        return self._model

    @property
    def data(self):
        return self._data

    def _rebuild(self) -> None:
        """Recompile, carrying every existing joint's position across by name."""
        kept = self._joint_state() if self._model is not None else {}

        self._model = compile_scene(self.world, list(self.objects.values()), self.obstacles)
        self._model.opt.timestep = TIMESTEP
        self._data = mujoco.MjData(self._model)

        for name, (qpos, qvel) in kept.items():
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if jid < 0:
                continue  # the object that owned it was removed
            qadr, dadr = self._model.jnt_qposadr[jid], self._model.jnt_dofadr[jid]
            self._data.qpos[qadr : qadr + len(qpos)] = qpos
            self._data.qvel[dadr : dadr + len(qvel)] = qvel

        self._apply_held()
        mujoco.mj_forward(self._model, self._data)

    def _joint_state(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Every joint's position and velocity, keyed by name.

        Free joints carry seven values in qpos and six in qvel; hinges and slides carry one
        of each. Reading the widths off the model rather than assuming keeps a rebuild from
        quietly truncating a free body's orientation.
        """
        out: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for jid in range(self._model.njnt):
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_JOINT, jid)
            if name is None:
                continue
            nq, nv = _joint_widths(self._model.jnt_type[jid])
            qadr, dadr = self._model.jnt_qposadr[jid], self._model.jnt_dofadr[jid]
            out[name] = (
                self._data.qpos[qadr : qadr + nq].copy(),
                self._data.qvel[dadr : dadr + nv].copy(),
            )
        return out

    # -- objects ------------------------------------------------------------------------

    def physicalize(self, object_id: str, schema: dict[str, Any], selection: Selection):
        """Add an object, and report it in the shape the wire expects.

        Raises ``SchemaError`` or ``SceneError`` rather than adding something unusable, so a
        failed physicalise leaves the session exactly as it was.
        """
        obj = object_from_selection(object_id, schema, selection)
        self.objects[object_id] = obj
        try:
            self._rebuild()
        except Exception:
            del self.objects[object_id]
            self._rebuild()
            raise
        return self._describe(obj, selection.id)

    def remove(self, object_id: str) -> bool:
        if object_id not in self.objects:
            return False
        del self.objects[object_id]
        self._rebuild()
        return True

    def _describe(self, obj: SceneObject, selection_id: str) -> PhysicsObject:
        """The object as ``object.created`` describes it.

        Part order matters and is shell first: the client assigns each splat to the LAST
        part whose region contains it, so the shell's ``all`` is the fallback and a door's
        ``front`` overrides it. See ``app/protocol.py``.
        """
        schema = obj.schema
        shell_name = f"{obj.prefix}{schema['object']}"
        listed: list[PhysicsPart] = [
            PhysicsPart(
                bodyName=shell_name,
                jointType=schema_mobility(schema),  # "free" or "fixed"
                range=None,
                splatSubset="all",
                initialPose=self._pose(shell_name),
            )
        ]

        for part in schema_parts(schema):
            name = f"{obj.prefix}{part['name']}"
            joint = part["joint"]
            rng = part.get("range")
            reported = None if joint == "fixed" or rng is None else (float(rng[0]), float(rng[1]))
            listed.append(
                PhysicsPart(
                    bodyName=name,
                    jointType=joint,
                    # Degrees for a hinge, metres for a slide, and the schema already speaks
                    # both. MuJoCo's radians never reach this side.
                    range=reported,
                    splatSubset=schema_region(part),
                    initialPose=self._pose(name),
                )
            )

        return PhysicsObject(
            id=obj.object_id,
            label=str(schema["object"]).replace("_", " "),
            selectionId=selection_id,
            parts=tuple(listed),
            massKg=mass_kg(schema),
            friction=schema_friction(schema),
        )

    # -- poses --------------------------------------------------------------------------

    def _pose(self, body_name: str) -> PoseUpdate:
        bid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if bid < 0:
            raise KeyError(f"no body named {body_name!r} in the compiled model")
        return PoseUpdate(
            bodyName=body_name,
            position=tuple(float(v) for v in self._data.xpos[bid]),
            # xquat is already (w, x, y, z). The wire wants the same, so nothing reorders.
            orientation=tuple(float(v) for v in self._data.xquat[bid]),
        )

    def body_names(self) -> list[str]:
        """Every body this session streams, shell first within each object."""
        names: list[str] = []
        for obj in self.objects.values():
            names.append(f"{obj.prefix}{obj.schema['object']}")
            names.extend(f"{obj.prefix}{p['name']}" for p in schema_parts(obj.schema))
        return names

    def poses(self) -> PoseBatch:
        return PoseBatch(
            t=float(self._data.time),
            poses=tuple(self._pose(name) for name in self.body_names()),
        )

    def status(self) -> SimStatus:
        return SimStatus(running=self.running, stepCount=self.step_count)

    # -- the clock ----------------------------------------------------------------------

    def advance(self, seconds: float) -> int:
        """Step far enough to cover ``seconds`` of wall time. Returns the steps taken.

        Capped at ``MAX_CATCHUP``: a session that was paused, or a process that was
        descheduled, must not then try to simulate the whole gap in one blocking burst.
        """
        if not self.running or seconds <= 0:
            return 0
        steps = int(min(seconds, MAX_CATCHUP) / TIMESTEP)
        for _ in range(steps):
            mujoco.mj_step(self._model, self._data)
            # After the step, or gravity walks a held door shut between frames.
            if self.held:
                self._apply_held()
        self.step_count += steps
        return steps

    def set_joint(self, body_name: str, value: float) -> bool:
        """Drive a part's joint to ``value``, in the units the wire speaks, and HOLD it there.

        Degrees for a hinge, metres for a slide. ``qpos`` is SI, so a hinge converts; that
        conversion happens here and nowhere else.

        **Held kinematically, not servoed.** A door set to 60 degrees and left alone falls
        shut: it has weight, and a generated appliance has none of the damper a real one
        does. The obvious fix is a position actuator, and it was tried -- measured, its
        steady state came out at roughly six times its target and saturated by 15, so it was
        not behaving as a position servo and shipping it on a guess would have meant a
        slider whose number meant nothing.

        So a driven joint is pinned: its position is reasserted after every step and its
        velocity held at zero. The cost is honest and worth stating -- a pinned joint is
        kinematic, so a door held open cannot be pushed shut by anything else in the scene.
        The shell around it is still a full dynamic body. For an interface whose verb is a
        slider, a joint that goes exactly where it is put beats one that approximately does.
        """
        jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, f"{body_name}_j")
        if jid < 0:
            return False

        radians = self._model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE
        lo, hi = self._model.jnt_range[jid]
        # jnt_range is SI, so express the limits in wire units to clamp against them.
        lo_wire, hi_wire = (math.degrees(lo), math.degrees(hi)) if radians else (lo, hi)

        self.held[body_name] = min(max(value, lo_wire), hi_wire)
        self._apply_held()
        mujoco.mj_forward(self._model, self._data)
        return True

    def release_joint(self, body_name: str) -> None:
        """Let a joint move under physics again."""
        self.held.pop(body_name, None)

    def _apply_held(self) -> None:
        """Pin every held joint. Called after each step, or gravity undoes the slider."""
        for body_name, wire in self.held.items():
            jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, f"{body_name}_j")
            if jid < 0:
                continue
            radians = self._model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE
            self._data.qpos[self._model.jnt_qposadr[jid]] = (
                math.radians(wire) if radians else wire
            )
            self._data.qvel[self._model.jnt_dofadr[jid]] = 0.0

    def joint_value(self, body_name: str) -> float | None:
        """Where a joint is now, in wire units. The inverse of :meth:`set_joint`."""
        jid = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, f"{body_name}_j")
        if jid < 0:
            return None
        raw = float(self._data.qpos[self._model.jnt_qposadr[jid]])
        if self._model.jnt_type[jid] == mujoco.mjtJoint.mjJNT_HINGE:
            return math.degrees(raw)
        return raw

    def control(self, action: str) -> None:
        if action == "play":
            self.running = True
        elif action == "pause":
            self.running = False
        elif action == "reset":
            self.step_count = 0
            self.held.clear()
            mujoco.mj_resetData(self._model, self._data)
            mujoco.mj_forward(self._model, self._data)
        else:
            raise ValueError(f"unknown sim action {action!r}")


def _joint_widths(joint_type: int) -> tuple[int, int]:
    """(qpos, qvel) widths for a MuJoCo joint type."""
    if joint_type == mujoco.mjtJoint.mjJNT_FREE:
        return 7, 6
    if joint_type == mujoco.mjtJoint.mjJNT_BALL:
        return 4, 3
    return 1, 1


def real_time_factor(session: Session, seconds: float = 1.0) -> float:
    """Simulated seconds per wall-clock second, measured rather than assumed.

    Below 1.0 the physics cannot keep up with the clock and the scene will visibly lag.
    """
    steps = int(seconds / TIMESTEP)
    started = time.perf_counter()
    for _ in range(steps):
        mujoco.mj_step(session.model, session.data)
    elapsed = time.perf_counter() - started
    return (steps * TIMESTEP) / elapsed if elapsed > 0 else float("inf")


__all__ = ["MAX_CATCHUP", "TIMESTEP", "Session", "real_time_factor"]
