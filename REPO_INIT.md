# Repo Initialization Spec

**Read this file top to bottom before writing any code.**

This document is both the product vision and an executable plan. It is written to be handed to a coding agent. Stages are ordered, each ends in a commit, and each has acceptance criteria that must actually pass before moving on.

Replace `PRODUCT_NAME` throughout with the real name once chosen.

---

# Part 1: The Vision

## 1.1 What this is

A web application that turns a Gaussian splat scan into a physically simulatable scene, through direct manipulation and natural language.

You import a splat of a real place. It appears in the browser, photoreal, and completely inert. You click or drag to select an object in it. You describe how that object should behave physically. The object becomes real: it has mass, collision, and where appropriate, joints that move. Everything you did not select stays exactly as it was, static scenery.

The end state is a scene where some things are alive and the rest is backdrop, and the user decided which is which by pointing and talking.

## 1.2 The core interaction

This is the thing to get right. Everything else is support.

1. A splat scene renders in the browser. The user orbits it freely.
2. The user drags a selection box, or clicks, over an object.
3. Selected Gaussians highlight. The rest dim.
4. A prompt field appears. The user types what the thing is and how it should behave. "A wooden crate, heavy, sits flat." "A dishwasher, the door hinges at the bottom and opens ninety degrees."
5. The system produces a physics body: collision geometry, mass, friction, and any joints the description implies.
6. The object visibly becomes physical. It settles under gravity. Its parts move. Its splats move with it.
7. Everything unselected remains static and untouched.

Steps 4 through 6 are the product. Steps 1 through 3 are what makes it feel like a tool rather than a script.

## 1.3 What goes in

Three import paths, in priority order.

**A splat scene**, as a `.ply` file. Ideally exported from World Labs Marble, but any standard binary little-endian 3DGS PLY must work. This is the environment and it is the primary input.

**A generated object mesh**, GLB from Tripo. Two uses: closing the shell on an object that was scanned only from one side, and dropping in objects that were never in the scene at all.

**A prompt with no geometry**, for adding a generated object directly.

## 1.4 What comes out

A scene description that can be simulated: MJCF plus the splat, with a mapping from splat subsets to physics bodies. Exportable, so the scene can leave the app and be used elsewhere.

## 1.5 Design direction

Modern, dark, and the splat is the hero. The 3D viewport is full-bleed and everything else floats over it.

- Full-viewport canvas. No page chrome, no header bar eating vertical space.
- Dark background, near-black rather than pure black, so the splat's own darks read.
- Floating glass panels with backdrop blur for controls. They overlay the scene, never push it around.
- One accent colour used sparingly, for selection state and for "this object is physical now".
- Sans-serif for interface text, monospace for anything numeric or technical: joint angles, mass, coordinates.
- Selection should feel immediate. Highlight on hover, commit on release, no modal dialogs.
- Motion matters. When an object becomes physical it should visibly settle rather than snap. That moment is the demo.

Avoid: sidebars that squeeze the viewport, multi-step wizards, anything that looks like a CAD package.

---

# Part 2: Architecture

## 2.1 The split, and why

Physics runs in Python because MuJoCo is a Python library and reimplementing it is not an option. Rendering runs in the browser because that is where the product lives. So there are two processes and a socket between them.

```
┌─────────────────────────────────────────────────┐
│  BROWSER                                        │
│                                                 │
│  Three.js scene                                 │
│   ├── splat renderer (the environment)          │
│   ├── per-object splat groups (movable)         │
│   └── selection overlay                         │
│                                                 │
│  UI: prompt bar, object list, physics readout   │
└──────────────┬──────────────────────────────────┘
               │  WebSocket
               │  ── up:   selections, prompts, commands
               │  ── down: body poses @ ~30Hz, scene events
┌──────────────┴──────────────────────────────────┐
│  PYTHON SERVICE                                 │
│                                                 │
│  FastAPI + WebSocket                            │
│  Schema generation (LLM → articulation JSON)    │
│  MJCF generation (JSON → XML)                   │
│  MuJoCo simulation loop                         │
│  Plan checking                                  │
└─────────────────────────────────────────────────┘
```

**The splat data never crosses the socket.** The browser loads the PLY directly and holds all the Gaussians. The backend never sees them. What crosses is a compact description of what was selected, and a stream of body poses coming back.

This matters. A million-Gaussian scene is around 250 MB. Sending it to Python and back would kill the app. Instead:

**Up:** when the user selects, the frontend computes the selection's centroid, principal axes and half-extents from the selected Gaussians and sends *that*, a few dozen bytes, not the indices.

**Down:** for each physics body, a position and a quaternion. For ten bodies at thirty hertz that is trivial bandwidth.

The frontend keeps the index arrays locally and applies incoming poses to its own splat groups.

## 2.2 The contract

Write this first. It is the seam and everything else is built against it.

```typescript
// ---------- Frontend → Backend ----------

type ClientMessage =
  | { type: "scene.load"; splatId: string; splatCount: number }
  | { type: "selection.commit"; selection: Selection }
  | { type: "object.physicalize"; selectionId: string; prompt: string }
  | { type: "object.remove"; objectId: string }
  | { type: "sim.control"; action: "play" | "pause" | "reset" }
  | { type: "mesh.attach"; objectId: string; glbUrl: string };

interface Selection {
  id: string;
  splatCount: number;
  // ALL geometry below is in SCENE coordinates, metres.
  centroid: [number, number, number];
  // 3x3 principal axes, column-major, right-handed. From PCA.
  axes: [number, number, number, number, number, number, number, number, number];
  halfExtents: [number, number, number];
}

// ---------- Backend → Frontend ----------

type ServerMessage =
  | { type: "object.created"; object: PhysicsObject }
  | { type: "object.failed"; selectionId: string; reason: string }
  | { type: "pose.batch"; t: number; poses: PoseUpdate[] }
  | { type: "sim.status"; running: boolean; stepCount: number };

interface PhysicsObject {
  id: string;
  label: string;                 // from the prompt
  selectionId: string;
  parts: PhysicsPart[];
  massKg: number;
  friction: number;
}

interface PhysicsPart {
  bodyName: string;              // matches PoseUpdate.bodyName
  jointType: "hinge" | "slide" | "button" | "free" | "fixed";
  // DEGREES for hinge, METRES for slide. Never radians on the wire.
  range: [number, number] | null;
  // which of the selection's splats belong to this part;
  // empty means "all of them"
  splatSubsetHint: string | null;
}

interface PoseUpdate {
  bodyName: string;
  position: [number, number, number];
  // quaternion, (w, x, y, z), MuJoCo convention
  orientation: [number, number, number, number];
}
```

**Three conventions that must be written into the code as comments, not remembered:**

- All positions are **metres**, in **scene coordinates**.
- All quaternions are **(w, x, y, z)**, MuJoCo convention.
- All angles crossing the socket are **degrees**. MuJoCo's `qpos` is radians internally; convert at the boundary and nowhere else.

## 2.3 Stack

**Frontend:** Vite + React + TypeScript. Three.js for the scene. A Gaussian splat renderer library rather than writing one; pick whichever is currently maintained and loads standard PLY. Tailwind for styling. Zustand or equivalent for state, because prop-drilling a scene graph is misery.

**Backend:** Python 3.11+, FastAPI, `uvicorn`, `mujoco`, `numpy`. `websockets` via FastAPI's native support.

**Do not add Convex, a database, or auth in the initial build.** In-memory state is correct for a single-session tool. Add persistence only if there is time left over, which there will not be.

## 2.4 Layout

```
PRODUCT_NAME/
├── README.md                 # Part 1 of this doc, edited
├── DECISIONS.md              # append-only log, see Stage 0
├── PORTING.md                # written in Stage 1
├── .gitignore
├── web/
│   ├── src/
│   │   ├── scene/            # Three.js: splat loading, camera, groups
│   │   ├── selection/        # picking, box select, PCA
│   │   ├── net/              # websocket client, typed messages
│   │   ├── ui/               # panels, prompt bar, readouts
│   │   ├── store/
│   │   └── types/            # generated from the contract above
│   └── package.json
├── service/
│   ├── app/
│   │   ├── main.py           # FastAPI, websocket endpoint
│   │   ├── protocol.py       # message types, mirrors web/src/types
│   │   ├── splat/            # PLY parsing (ported)
│   │   ├── mjcf/             # schema → MJCF generation (ported)
│   │   ├── schema/           # LLM → articulation schema
│   │   └── sim/              # MuJoCo loop, pose streaming
│   ├── tests/
│   └── pyproject.toml
└── assets/
    └── samples/              # one small sample splat, gitignored if large
```

---

# Part 3: The Port From `akitech/splat`

## 3.1 Rules

`akitech/splat` is prototype code. Prototypes have value in the parts that were hard to get right and liability in the parts that were expedient. **Do not copy the directory.**

The rules for every file considered:

1. **Audit before you touch anything.** Stage 1 produces an inventory and moves no code.
2. **Port by function, not by file.** If a file has one useful function and three dead ones, port the one.
3. **Never port secrets.** No `.env`, no API keys, no tokens. If a key appears in the source, note it in `PORTING.md` and leave it behind.
4. **Never port large binaries.** Sample splats, meshes, captures stay out of git. Reference them by path.
5. **Every ported function gets a test**, even a trivial one that just proves it runs on a known input.
6. **Rewrite imports and paths.** No absolute paths from the prototype machine.
7. **One commit per stage.** Stages are the unit of review.

## 3.2 What is likely worth porting

Judge against the actual contents, but expect value in:

- **PLY parsing and the field decoding.** The log-space scales, the pre-sigmoid opacity, the quaternion order, the SH degree detection from the `f_rest` count. This is fiddly and already debugged. High value.
- **Any coordinate transform** already solved, especially a Marble-to-scene conversion. Marble uses OpenCV convention with +x left, +y down, +z forward, so y and z need negating for OpenGL-style engines. If that conversion is already written and tested, it is worth its weight.
- **MJCF generation**, if a schema-to-XML path exists.
- **Anchor resolution** from symbolic names to coordinates.
- **PCA / oriented bounding box** fitting.
- **Any MuJoCo scene assembly** that works.

Expect **not** to port: throwaway scripts, notebook cells, anything that hardcodes a path, anything reading from a specific capture, and anything MuJoCo-Warp-specific for splat rendering, since rendering moved to the browser.

---

# Part 4: Stages

Each stage is a commit. Acceptance criteria must actually be checked, not assumed.

---

## Stage 0 — Skeleton

Create the repo structure from 2.4. Empty directories with `.gitkeep`.

Write `README.md` from Part 1 of this document, edited for tone and with the real product name.

Create `DECISIONS.md` with the initial entries:

```markdown
# Decisions

Append-only. Newest at the bottom. Every entry: what, why, when.

## Units and conventions
- Positions: metres, scene coordinates.
- Quaternions: (w, x, y, z), MuJoCo convention.
- Angles on the wire: DEGREES. MuJoCo qpos is radians; convert only at the boundary.
- Splat PLY: binary little-endian. Scales are log-space (apply exp).
  Opacity is pre-sigmoid (apply sigmoid).

## Architecture
- Physics in Python, rendering in the browser, WebSocket between them.
- Splat data never crosses the socket. The browser owns the Gaussians.
- Selections cross as centroid + axes + half-extents, not index arrays.
- No database in v1. In-memory session state.
```

Write `.gitignore` covering `node_modules`, `__pycache__`, `.venv`, `.env`, `dist`, `*.ply`, `*.glb`, `*.splat`, `.DS_Store`.

**Acceptance:** `git log` shows one commit. The tree matches 2.4.

**Commit:** `chore: initialize repo structure and conventions`

---

## Stage 1 — Audit the prototype

**Move no code in this stage.**

Read `akitech/splat` in full. Produce `PORTING.md`:

```markdown
# Porting Inventory

Source: akitech/splat @ <commit sha if available>
Audited: <date>

## Port

| Source | What it does | Destination | Notes |
|---|---|---|---|
| ... | ... | ... | ... |

## Rewrite

Things whose idea is right but whose implementation should not survive.

## Leave

| Source | Why |
|---|---|

## Hazards found

- Secrets, hardcoded paths, large binaries, anything surprising.
```

Be specific. "Utility functions" is not an inventory entry. "`load_ply()` parses binary LE PLY, handles SH degree detection, does not apply the exp to scales" is.

**Acceptance:** `PORTING.md` accounts for every source file in one of the three tables.

**Commit:** `docs: inventory akitech/splat for porting`

---

## Stage 2 — Splat I/O

Port PLY parsing into `service/app/splat/`.

It must: parse the header and respect property order; detect SH degree from the `f_rest` count; apply `exp` to scales and `sigmoid` to opacity; normalize quaternions; return typed numpy arrays; and report the scene's bounding box and extent.

Add `service/app/splat/transforms.py` with the Marble OpenCV-to-scene conversion, and a test asserting a known point maps where expected.

Note: the backend does not need splats for physics. This exists for validation, for headless testing, and because the frontend parser should be checkable against a reference implementation.

**Acceptance:** `pytest` passes. A CLI command prints Gaussian count, SH degree, and bounding box for a sample file, and the extent is physically plausible.

**Commit:** `feat(splat): port PLY parsing and coordinate transforms`

---

## Stage 3 — Schema and MJCF generation

Port or write `service/app/schema/` and `service/app/mjcf/`.

The schema is the articulation description: symbolic anchors, symbolic axes, joint types, ranges, preconditions, affordance sites. Symbolic, never coordinates, because a language model can name a location reliably and cannot guess a coordinate in your frame.

The generator resolves symbols against half-extents and emits MJCF. It must handle at minimum: `fixed` (no joint, welded), `free` (freejoint, a loose object), `hinge`, `slide`, and `button` (a sprung slide).

Non-negotiables in the generated XML:

- `<compiler angle="degree" autolimits="true"/>`. Without `autolimits`, ranges are silently ignored.
- Body origin **on the joint anchor**, geometry offset from it. This makes every joint `pos="0 0 0"` and removes a class of arithmetic bugs.
- Box `size` is **half-extents**.
- Friction set on **both** contacting geoms, because MuJoCo takes the elementwise maximum and setting one does nothing.
- A `<site>` per affordance.

Include a validator that rejects unknown anchors, unknown axes, inverted ranges, implausible values (a hinge over 180 degrees, a slide over a metre) and `requires` clauses naming parts that do not exist. On failure, return the errors as text so they can be fed back to the model for a retry.

**Acceptance:** a hand-written dishwasher schema generates MJCF that loads in MuJoCo without error. A test drives each joint through its full range and asserts no part passes through the shell. The validator rejects at least five malformed schemas.

**Commit:** `feat(mjcf): articulation schema, validator, and MJCF generation`

---

## Stage 4 — Physics service

Build `service/app/main.py` and `service/app/sim/`.

A FastAPI app with a WebSocket endpoint. A session holds one MuJoCo model and data. A background task steps the simulation and broadcasts `pose.batch` at roughly thirty hertz, decoupled from the physics timestep, which stays at 0.002.

Implement `object.physicalize` end to end with a **stubbed** schema generator that returns a hardcoded rigid-body schema regardless of the prompt. The LLM comes in Stage 7.

Handle `sim.control` for play, pause and reset.

**Acceptance:** a Python test client connects, sends a selection and a physicalize message, receives `object.created`, then receives a stream of `pose.batch` messages showing a body falling under gravity and coming to rest.

**Commit:** `feat(service): websocket physics session with pose streaming`

---

## Stage 5 — Frontend shell and splat rendering

Scaffold `web/`. Vite, React, TypeScript, Tailwind, Three.js, a splat renderer.

Generate `web/src/types/protocol.ts` from the contract in 2.2. Keep it in sync with `service/app/protocol.py` by hand; note in `DECISIONS.md` that these two files are a matched pair.

Implement: full-viewport canvas, orbit controls, drag-and-drop a `.ply` to load it, a loading state, and a readout showing Gaussian count and frame rate in monospace.

Get the visual language right here, because retrofitting it later never happens. Dark background, floating blurred panel, one accent colour.

**Acceptance:** dropping a sample PLY renders it, orbiting is smooth, and the Gaussian count is displayed.

**Commit:** `feat(web): scene shell with splat rendering and orbit controls`

---

## Stage 6 — Selection

This is the interaction that makes it a tool. Spend real time here.

Implement drag-box selection: on drag, project Gaussian centroids to screen space, collect those inside the rectangle, and filter by depth to avoid grabbing the far wall through the object. Show a live count during the drag.

On release, compute the selection's centroid, principal axes by PCA, and half-extents. **Check the determinant of the axes matrix and flip a column if it is negative**, because eigenvectors come back with arbitrary sign and a left-handed frame is a reflection, not a rotation, and will mirror everything downstream.

Render selected Gaussians in the accent colour and dim the rest. Provide a clear-selection affordance.

Send `selection.commit` and keep the index array locally.

**Acceptance:** selecting an object in a real scan highlights approximately that object and nothing else. The computed half-extents match a caliper measurement of the real object within a few percent. The axes matrix has positive determinant.

**Commit:** `feat(web): box selection with PCA-derived object frame`

---

## Stage 7 — Prompt to physics

Wire the language model into `service/app/schema/`.

The prompt must enumerate the allowed joint types, axes, anchors and affordance actions, and demand JSON with no prose and no markdown fences. Validate the response with the Stage 3 validator. On failure, feed the errors back and retry twice, then fall back to a stored rigid-body schema.

**Build the fallback before the LLM path.** Venue wifi is unreliable and a flag that switches to canned schemas is what keeps a demo alive.

In the UI: a prompt bar appears on selection commit, with placeholder text that teaches by example. Show a pending state while generating. On success, show the object in a list with its parts and their joint types.

**Acceptance:** typing "a wooden crate, heavy" produces a rigid body that falls and settles. Typing a dishwasher description produces a hinge, and the door swings in the sim. Killing the network still produces a working object via the fallback.

**Commit:** `feat: natural language to physics object pipeline`

---

## Stage 8 — Live binding

Close the loop visually.

On `object.created`, move that selection's Gaussians out of the static scene into a per-body group. On each `pose.batch`, set each group's position and quaternion.

The transform is: store each group's Gaussians relative to the body's initial pose, then each frame set the group transform from the incoming pose. Three.js groups handle the composition, so you do not need to transform individual Gaussians, which is the main reason this architecture is easier than the desktop one.

Watch the quaternion order. The wire carries `(w, x, y, z)`; Three.js `Quaternion.set` takes `(x, y, z, w)`. Convert in exactly one place, in the network layer, and comment it.

Interpolate between pose updates so thirty hertz of physics reads as smooth motion at sixty frames per second.

**Acceptance:** a physicalized object visibly falls and settles, with its splats moving with it, while the rest of the scene stays fixed. An articulated part swings on its joint. No drift over sixty seconds.

**Commit:** `feat: bind splat groups to live physics poses`

---

## Stage 9 — Import paths and polish

Only if Stages 0 through 8 all pass.

Mesh import: accept a Tripo GLB, attach to an existing object as visual geometry with `contype="0" conaffinity="0"` so it renders but never collides, rescaled to the selection's measured half-extents. **Never trust the generated mesh's own scale**; generative reconstruction returns models of unknown size.

Scene export: MJCF plus the splat reference plus the object manifest.

Polish: the settle animation, an empty state that tells a first-time user what to do, keyboard shortcuts for play/pause/reset.

**Commit:** `feat: mesh import, scene export, and interaction polish`

---

# Part 5: Operating Notes

## 5.1 Order of work if time runs short

Stages 0 through 2 are cheap and must happen. Stage 3 is the core and is pure code with no dependencies on scan, camera or network, so it cannot be blocked. Stages 4 through 6 are the app skeleton. Stage 7 is the product. Stage 8 is the demo.

If you must cut, cut Stage 9 entirely, then reduce Stage 7 to the fallback path only with no LLM. **Do not cut Stage 8**, because an object that becomes physical without visibly moving is not a demo.

## 5.2 Things that will cost you an hour if you get them wrong

- Quaternion order at the browser boundary. `(w,x,y,z)` on the wire, `(x,y,z,w)` in Three.js.
- Degrees on the wire versus radians in `qpos`.
- Log-space scales and pre-sigmoid opacity in the PLY.
- Marble's OpenCV convention: negate y and z.
- PCA returning a left-handed axis set. Check the determinant.
- Box `size` being half-extents.
- `autolimits` unset, so joint ranges do nothing.
- Friction set on one geom of a contact pair.
- A body with no joint does not fall. It is welded to its parent.

## 5.3 Committing

Conventional commits, one per stage, imperative mood. The commit is the unit of review, so the message should describe what became possible, not which files changed.

Tag `git tag stage-N-green` after each stage's acceptance criteria pass. When something breaks late, you want a commit you are certain was good.

## 5.4 What not to build

No auth. No database. No user accounts. No multi-scene management. No settings page. No dark-mode toggle, since it is already dark. No onboarding tour.

Every one of those is a plausible feature and every one of them is time taken from the interaction in 1.2, which is the only thing anybody will remember.
