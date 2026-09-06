"""The twin, without needing a 127 MB capture.

Every test here builds a synthetic room -- a floor and one worktop -- so the suite runs in
seconds, is deterministic, and does not depend on which captures happen to be on this
machine. Reading a real PLY is covered by `test_reads_a_real_capture`, which skips when
there is not one to read.
"""

from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np
import pytest

from twin import pick, room, scene

CAPTURES = Path(__file__).resolve().parents[2] / "capture" / "out"


def synthetic(worktop_z: float = 0.90) -> room.Room:
    """A floor and one worktop, in metres, shaped like what `ground.py` returns."""
    return room.Room(
        path=Path("synthetic.ply"),
        positions=np.zeros((0, 3), dtype=float),
        scale=1.0,
        up_original=(0.0, 0.0, 1.0),
        floor_z=0.0,
        ceiling_z=2.6,
        surfaces=[
            room.Surface(0.0, (0.0, 0.0), (2.5, 3.0), 250_000, 15.0, is_floor=True),
            room.Surface(worktop_z, (0.0, 0.0), (0.8, 0.6), 300_000, 1.9, is_floor=False),
            room.Surface(2.6, (0.0, 0.0), (2.5, 3.0), 380_000, 15.0, is_floor=False),
        ],
        flipped=False,
    )


def built(scanned: room.Room | None = None):
    scanned = scanned or synthetic()
    placement = scene.place(scanned)
    model = scene.build(scanned, placement)
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    scene.settle(model, data, 0.5)
    return scanned, placement, model, data


# ---------------------------------------------------------------------------------------
# Choosing where to stand
# ---------------------------------------------------------------------------------------


def test_the_arm_stands_on_the_worktop_not_the_ceiling():
    """The 2.6m surface has the most splats of all. Height has to rule it out."""
    scanned = synthetic()
    assert [s.height for s in scanned.standable()] == [0.90]
    assert scene.place(scanned).surface_z == pytest.approx(0.90)


def test_a_room_with_no_worktop_falls_back_to_the_floor():
    """The playroom capture is exactly this: a floor and two ceiling patches, no table."""
    bare = synthetic()
    bare.surfaces = [s for s in bare.surfaces if s.is_floor or s.height > 2.0]

    assert bare.standable() == []
    placement = scene.place(bare)
    assert placement.on_floor
    assert placement.surface_z == pytest.approx(0.0)


def test_the_best_surface_is_the_best_supported_one_not_the_biggest():
    """A broad band of noise can span more square metres than a real worktop."""
    scanned = synthetic()
    scanned.surfaces.append(room.Surface(0.5, (0.0, 0.0), (4.0, 4.0), 9_000, 30.0, is_floor=False))

    assert scanned.standable()[0].height == pytest.approx(0.90)


def test_the_block_starts_in_front_of_the_arm_and_on_the_surface():
    placement = scene.place(synthetic())

    assert placement.block[1] < placement.arm[1], "the block should be in front, along -y"
    assert placement.block[2] > placement.surface_z, "and resting on top of it"
    reach = abs(placement.block[1] - placement.arm[1])
    assert 0.05 < reach <= scene.BLOCK_REACH_M


def test_a_narrow_surface_pulls_the_block_closer():
    """Otherwise the block is placed off the edge of the patch that was actually observed."""
    narrow = synthetic()
    narrow.surfaces[1] = room.Surface(0.9, (0.0, 0.0), (0.2, 0.09), 300_000, 0.1, is_floor=False)

    placement = scene.place(narrow)
    assert abs(placement.block[1] - placement.arm[1]) < scene.BLOCK_REACH_M


# ---------------------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------------------


def test_the_scene_compiles_with_the_arm_and_the_block():
    _, _, model, _ = built()

    assert model.nu == 6, "five arm joints and a jaw"
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "block") >= 0
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "room_floor") >= 0


def test_the_block_rests_on_the_surface_rather_than_falling_through():
    scanned, placement, model, data = built()
    at_rest = scene.block_position(model, data)

    assert at_rest[2] == pytest.approx(placement.surface_z + scene.BLOCK_HALF[2], abs=0.01)


def test_the_block_is_red():
    """It is 'the red block' in the script, so it had better be red on screen."""
    _, _, model, _ = built()
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "block_geom")
    assert model.geom_rgba[gid][0] > 0.8 and model.geom_rgba[gid][1] < 0.3


def test_the_room_geometry_is_static():
    """Walls and worktops are geoms in the worldbody, so they are welded by construction."""
    _, _, model, _ = built()
    assert model.nbody == 9, "the arm's links plus the block, and nothing else movable"


def test_walls_can_be_left_out():
    scanned = synthetic()
    placement = scene.place(scanned)
    with_walls = scene.build(scanned, placement, walls=True).ngeom
    without = scene.build(scanned, placement, walls=False).ngeom

    assert with_walls - without == 4


# ---------------------------------------------------------------------------------------
# The pick
# ---------------------------------------------------------------------------------------


def test_the_arm_picks_the_block_up():
    """The whole point. Slow -- it runs about five seconds of physics -- and worth it."""
    _, placement, model, data = built()
    result = pick.run(model, data, placement)

    assert result.ok, result.describe()
    assert result.lifted_m > scene.BLOCK_HALF[2]
    assert result.beats == ["above", "down", "close", "lift"]


def test_every_beat_is_reachable_on_a_worktop():
    scanned, placement, model, data = built()
    _, unreachable = pick.plan(placement, scene.block_position(model, data))

    assert unreachable == [], "the block is placed inside the arm's envelope by construction"


def test_targets_are_solved_in_the_arms_base_frame():
    """The arm's IK works in its base frame and the base is not at the origin here.

    Solving a world-frame target would put the wrist somewhere metres away, and on a
    worktop at z=0.9 it fails in a way that still looks like a plausible motion.
    """
    high = synthetic(worktop_z=0.90)
    low = synthetic(worktop_z=0.30)

    beats_high, _ = pick.plan(scene.place(high), np.array(scene.place(high).block))
    beats_low, _ = pick.plan(scene.place(low), np.array(scene.place(low).block))

    # Same geometry relative to the base, so the same solve, whatever the worktop height.
    assert np.allclose(beats_high[0].q, beats_low[0].q, atol=1e-6)


# ---------------------------------------------------------------------------------------
# A real capture, when there is one
# ---------------------------------------------------------------------------------------


def test_reads_a_real_capture():
    captures = sorted(CAPTURES.glob("*.ply")) if CAPTURES.is_dir() else []
    if not captures:
        pytest.skip(f"no capture in {CAPTURES}")

    scanned = room.read(captures[0])
    assert len(scanned.positions) > 1000
    assert scanned.scale > 0
    assert scanned.floor.is_floor
    assert scanned.report()
