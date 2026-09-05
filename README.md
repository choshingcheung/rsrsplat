# rsrsplat

**Point at something in a scan. Say what it is. Watch it become real.**

rsrsplat turns a Gaussian splat capture of a real place into a scene you can simulate — through
direct manipulation and plain language.

You import a splat of somewhere real. It appears in the browser, photoreal and completely inert.
You drag a box over an object in it. You describe how that object should behave. It gains mass,
collision, and where the description implies them, joints that move. Everything you did not
select stays exactly as it was: static scenery.

The end state is a scene where some things are alive and the rest is backdrop, and you decided
which is which by pointing and talking.

---

## The interaction

This is the thing to get right. Everything else is support.

1. A splat scene renders in the browser. You orbit it freely.
2. You drag a selection box over an object.
3. The selected Gaussians highlight. The rest dim.
4. A prompt field appears. You say what the thing is and how it behaves —
   *"a wooden crate, heavy, sits flat"*, or
   *"a dishwasher, the door hinges at the bottom and opens ninety degrees."*
5. The system produces a physics body: collision geometry, mass, friction, and any joints the
   description implies.
6. The object visibly becomes physical. It settles under gravity. Its parts move. Its splats
   move with it.
7. Everything unselected remains static and untouched.

Steps 4 through 6 are the product. Steps 1 through 3 are what makes it a tool rather than a
script.

## Why it works

**Language supplies the mechanism. Perception supplies the pose.**

A language model already knows that a dishwasher door is a hinge along the bottom front edge
with about ninety degrees of travel. So the kinematic template comes from language, and the scan
only has to answer *where that edge is* — a far easier problem than discovering an unknown axis
from geometry, which is what most articulation-from-scan systems attempt.

## What goes in

- **A splat scene**, as a `.ply` — standard binary little-endian 3DGS. Ideally exported from
  World Labs Marble. This is the environment and the primary input.
- **A generated object mesh**, GLB from Tripo — for closing the shell on something scanned from
  one side, or for dropping in objects that were never in the scene.
- **A prompt with no geometry**, for adding a generated object outright.

## What comes out

A scene that can be simulated: MJCF plus the splat, with a mapping from splat subsets to physics
bodies. Exportable, so the scene can leave the app.

---

## Architecture

Physics runs in Python because MuJoCo is a Python library. Rendering runs in the browser because
that is where the product lives. Two processes, one socket.

```
┌─────────────────────────────────────────────────┐
│  BROWSER                                        │
│   Three.js scene                                │
│    ├── splat renderer (the environment)         │
│    ├── per-object splat groups (movable)        │
│    └── selection overlay                        │
│   UI: prompt bar, object list, physics readout   │
└──────────────┬──────────────────────────────────┘
               │  WebSocket
               │  ── up:   selections, prompts, commands
               │  ── down: body poses @ ~30Hz, scene events
┌──────────────┴──────────────────────────────────┐
│  PYTHON SERVICE                                 │
│   FastAPI + WebSocket                           │
│   Schema generation (LLM → articulation JSON)   │
│   MJCF generation (JSON → XML)                  │
│   MuJoCo simulation loop                        │
└─────────────────────────────────────────────────┘
```

**The splat data never crosses the socket.** The browser loads the PLY and owns every Gaussian.
The backend never sees them. A million-Gaussian scene is around 250 MB; sending it to Python and
back would kill the app.

What crosses instead:

- **Up:** a selection's centroid, principal axes and half-extents — a few dozen bytes, not an
  index array.
- **Down:** a position and a quaternion per body. Trivial bandwidth at thirty hertz.

The frontend keeps the index arrays locally and applies incoming poses to its own splat groups.

## Conventions

Non-negotiable, and written into the code rather than remembered:

- Positions are **metres**, in **scene coordinates**.
- Quaternions are **(w, x, y, z)**, MuJoCo convention. Three.js takes `(x, y, z, w)`; the
  conversion happens in exactly one place, in the network layer.
- Angles crossing the socket are **degrees**. MuJoCo's `qpos` is radians internally; convert at
  the boundary and nowhere else.

## Layout

```
rsrsplat/
├── REPO_INIT.md        # the original specification
├── PLAN.md             # the working plan: tracks, steps, proofs
├── DECISIONS.md        # append-only decision log
├── PORTING.md          # inventory of the akitech/splat prototype
├── NOTES.md            # facts measured on the machine, not assumed
├── contract/fixtures/  # golden protocol messages, tested by both sides
├── web/                # Vite + React + TypeScript + Three.js
└── service/            # FastAPI + MuJoCo
```

## Status

Under construction. See [`PLAN.md`](PLAN.md) for what is built and what is next.
