# twin — the scanned room as physics, with an arm in it

A robot arm standing inside a Gaussian splat capture, picking up a red block.

```bash
cd twin
.venv/Scripts/python -m twin inspect ../capture/out/<capture>.ply
.venv/Scripts/python -m twin view    ../capture/out/<capture>.ply   # interactive
.venv/Scripts/python -m twin film    ../capture/out/<capture>.ply   # out/pick.mp4
```

`view` opens the MuJoCo viewer and runs the pick in it; drag to orbit. `film` writes the
same run to `out/pick.mp4`, plus `out/orbit.mp4` -- a slow turn with nothing moving, which
is the quickest way to see whether the room came out the right way up.

**Read this if you are working in the other half of this repo.** Like `capture/`, this track
is developed in the same working tree by a second session, and the contract below is what
keeps the two apart.

---

## What it does

    a .ply  ->  up, scale, floor, surfaces  ->  MJCF  ->  SO-101 + red block  ->  a pick

Nothing here is new science. It composes three things that already exist:

| from | what |
|---|---|
| `akitech/splat/src/ground.py` | RANSAC planes, up by layer score, metric scale, the flat surfaces of a capture |
| `akitech/app/sdk/akitech/cell` | the SO-101's kinematics, joint conventions and IK |
| `robot_descriptions` | the SO-101 MJCF, from `mujoco_menagerie` |

The narrative it buys is this project's own thesis, stated in `ground.py`'s docstring:

> *None of this makes the splats solid. It reads geometry out of them so that ordinary
> MuJoCo geoms can be created to match.*

You show the capture in the app; then you show the same room as physics, with an arm reaching
into it. Same scan, twice.

## The isolation contract

**This track owns exactly one path: `twin/`.** Everything else in rsrsplat is read-only to it.

- **Nothing under `twin/` imports `service.app.*` or anything from `web/`**, and nothing
  there imports this. There is no socket, no shared module and no shared build.
- The two `akitech` repositories are read **read-only, over `sys.path`** (`twin/deps.py`).
  Nothing here writes to them. A missing one fails with the path it looked for.
- Own `pyproject.toml`, own `.venv`, own `.gitignore`. No dependency is added to
  `service/pyproject.toml` or `web/package.json`.
- Only ever `git add twin/...` with explicit paths. Never `git add -A`, `-u`, or
  `git commit -a`.

## The two assumptions everything rides on

Both are derived, not measured, and both are wrong in ways that still look plausible.

**Scale** comes from assuming the ceiling is 2.6 m above the floor — `ground.py`'s own
default, and the same figure `web/src/scene/ground.ts` uses. A splat capture has no intrinsic
scale, and Marble worlds report no `metric_scale_factor` either (see `capture/NOTES.md`). Get
it wrong and a 30 cm arm is a doll or a crane. `--room-height` overrides it.

**Which way is up** comes from `ground.py`'s rule that the floor is the denser extreme. It is
usually right and is not always right: on the three-image desk capture a colour test
disagreed with it. So `--flip` is a first-class flag, and **the viewer is the honest check** —
a room built upside down puts the arm on the ceiling, which takes one second to see and no
amount of arithmetic to prove.

Run `twin inspect` before anything else.

## What the captures gave

| capture | surfaces | verdict |
|---|---|---|
| `playroom_7000.ply` | floor + two ceiling patches, nothing between | **no table.** The 7k checkpoint gives mid-height furniture too few clean splats to form a band. The arm would stand on the floor. |
| desk, three images | floor, **0.90 m worktop (331k splats)**, ceiling | the desk. This is the one to demo. |

The playroom also proves why the indirection is worth it: its true up is
`(-0.002, -0.392, +0.920)`, oblique to every axis. No named transform finds it and no
histogram along x, y or z does either — only plane fitting.

## The pick

Scripted, not learned: **above → down → close → lift**, each an IK solve through akitech's
`Arm`. A trained policy that succeeds eight times in ten is a better robot and a worse
demonstration, because the two failures happen in front of an audience.

Two things this file is built around:

- The arm's IK works **in its base frame**, and the base is not at the world origin here. Every
  target is translated before it is solved. On a 0.9 m worktop, getting this wrong still
  produces a plausible-looking motion that misses.
- The actuators are **position servos**, so each beat is eased over a number of control ticks.
  Commanded as a step, the arm snaps and flings the block across the room.

Measured on the desk capture: **the block rises 110 mm.**

## Status

Working end to end. 14 tests, ruff clean. The tests build a synthetic room — a floor and one
worktop — so they run in two seconds and need no capture on disk; the one test that reads a
real `.ply` skips when there is not one.

**Not done, and visible in the film:** the worktop is drawn as the full extent of the band
`ground.py` found -- roughly 9 x 14 m -- so the arm reads as standing on a plain rather than
on a desk. `support_patch` would cut it down to the patch actually around the arm. Cosmetic,
but it is the first thing anyone will ask about.
