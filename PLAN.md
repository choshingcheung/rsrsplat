# Working plan

> **`STATUS.md` describes what actually exists.** This file is the plan as drawn, and parts of
> it are stale — the Integration steps below were completed in practice without being ticked.
> Where the two disagree, `STATUS.md` is right.

Two tracks against a frozen seam. Each step ends green and is provable on its own; nothing is
"done" because it looks done. Mark steps here as they pass.

Legend: `[ ]` not started · `[~]` in progress · `[x]` passing

---

## Phase 0 — the agreement

Sequential. Nothing can run in parallel until the seam exists.

- [x] **0.1** Repo skeleton, `.gitignore`, `.env.example`.
- [x] **0.2** `README.md`, `DECISIONS.md`, this plan.
- [x] **0.3** The contract: `service/app/protocol.py` and `web/src/types/protocol.ts`, with the
      four additions recorded in `DECISIONS.md`. Units commented at every field that has one.
- [x] **0.4** Golden fixtures in `contract/fixtures/`, one per message type, with a test on
      **both** sides. *Proof:* `pytest` parses every fixture; `tsc` accepts every fixture.
- [x] **0.5** Prototype audit → `PORTING.md`. Every source file lands in Port, Rewrite or Leave.
      No code moves in this step.

Tag: `phase-0-green`

---

## Track S — the spine

Python, headless, provable with `pytest`. No browser involved at any point.

- [x] **S1 · Splat reading.** Port PLY parsing and the Marble coordinate transform.
      *Proof:* CLI prints Gaussian count, SH degree and bounding box for the real capture.
      Transform tests on known points. 60 tests green.
      *Caveat:* the extent is NOT a plausible number of metres — the capture is not metric
      (see `NOTES.md`). That is a fact about the capture, not the parser, and it is what
      `WorldFrame.sceneScale` exists for. This half of the criterion is met at A3/S4.
- [x] **S2 · Schema and validator.** Port the articulation schema and its checker.
      *Proof:* the dishwasher validates; seven malformed schemas rejected with model-readable
      errors; all seven stored fallbacks validate. 112 tests green.
      *Added for rsrsplat:* `mobility` (free/fixed), `shape` (box/shell), and objects with no
      parts — a crate is the commonest case and the prototype could not express it.
      *Deferred:* `precond.py` (symbolic plan checking). Nothing consumes `requires` at
      runtime until there is a planner, and rsrsplat has none. The validation of `requires`
      clauses — including the sprung-button check — is ported and live.
- [x] **S3 · MJCF generation.** Port schema → XML.
      *Proof:* all seven stored schemas load in MuJoCo. Every hinge is swept through its full
      range asserting the panel never enters its own shell; every slide travels its exact range
      along one axis; every button springs back. All four silent killers have explicit tests,
      and `autolimits` is checked by asserting the solver *enforces* the limit rather than that
      the attribute is present. 165 tests green, ruff clean.
      *Three real bugs the sweep found:* a top-hinged lid swung backwards into its own bin
      (the same axis sign opens a bottom-hinged door and closes a top-hinged lid — the
      generator now derives the sign that opens, so `range: [0, N]` always means "opens");
      a door panel centred on the front face stood permanently 1.75 mm inside the side walls;
      and `floor=False` moved the object to the origin while leaving the plane there, so a
      "free-floating" body started half-buried and was ejected upward.
- [x] **S4 · Ground and scene assembly.** Floor, up vector, scale.
      *Proof:* a 20 kg crate dropped 1 m above a floor at z = -1.42 settles in under 1500 steps
      and rests on it; a 200 kg one never dips below the plane on the way down. Two objects
      stack. 183 tests green, ruff clean.
      *Decided:* scene coordinates are metric and z-up **by construction** — the browser
      establishes that at load, since it owns the Gaussians and must pick a render frame
      anyway. `WorldFrame` still carries `up` and `sceneScale`, and the service validates them
      with a message naming the problem rather than simulating a scene where gravity points at
      a wall. See `DECISIONS.md`.
      *Two cross-checks worth having:* the placement derived from a selection's axis columns
      independently reproduces the orientation quaternion hand-computed in
      `contract/fixtures/`, and the door body resolved from the symbolic anchor
      `bottom_front_edge` lands exactly where the fixture says the hinge is. Two routes to the
      same numbers means the wire's frame and the MJCF's frame really are one frame — the
      mismatch `PORTING.md` hazard 2 was about, and one that is invisible in a viewer.
- [x] **S5 · Simulation loop.** Stepping at 0.002, pose broadcast at ~30 Hz, decoupled.
      *Proof:* **227x real time** with six objects and 19 bodies — two orders of magnitude of
      headroom over a 30 Hz broadcast. A free crate falls and settles across a stream of
      batches. 206 tests green, ruff clean.
      *The one that bites:* MuJoCo models are immutable, so physicalising a second object
      recompiles the first. The naive version drops every existing joint to zero, so the
      dishwasher door the user just opened snaps shut when they physicalise a crate across the
      room — a physics glitch to look at, a data loss in fact. State is carried across by joint
      name, reading qpos/qvel widths off the model so a freejoint's seven values are not
      truncated to one.
      *Also:* a long gap is capped at 0.1 s of catch-up, so a descheduled service does not
      block its own socket simulating the whole gap before sending anything.
- [x] **S6 · The service.** FastAPI, WebSocket, session state, `sim.control`, and
      `object.physicalize` end to end.
      *Proof:* a test client connects over a real socket, commits a selection, physicalizes,
      receives `object.created`, and watches ~40 `pose.batch` messages take a crate from 1 m
      above the floor down to rest on it. Pause freezes the stream, play resumes it, reset
      returns both position and simulated time to zero. 263 tests green, ruff clean.
      *Better than the stub REPO_INIT asked for:* rather than a hardcoded rigid body, this is
      the **real offline path** — `schema/fallback.py` matches a noun in the prompt against the
      stored library and nudges density from adjectives. "a wooden crate, heavy" and "an empty
      cardboard box" already differ by 6x in mass, and a dishwasher already comes back with a
      90-degree hinge, with no network and no key. S7 puts the model in front of this, which is
      the order REPO_INIT asks for: a fallback written afterwards is one nobody has run.
- [ ] **S7 · Language.** Model → schema, validated, two retries, then fallback.
      *Proof:* the fallback is built and passing **first**. Then "a wooden crate, heavy" gives a
      rigid body and the dishwasher sentence gives a hinge. Then a network-kill test still
      yields a working object.

---

## Track A — the app

Browser, design-led, provable with no Python process running.

- [x] **A1 · Scaffold and design system.** Vite + React + TS + Tailwind + Three.js.
      *The direction: an instrument pointed at a room, not a dashboard.* A theodolite, a
      rangefinder, a laser scanner. That one decision produces every rule in
      `src/styles/tokens.css` and rules out what a web app reaches for by default — cards,
      sidebars, drop shadows, a header bar.
      *Concretely:* viewfinder corner brackets and an edge scale instead of panels; Instrument
      Sans for interface text and Martian Mono for every number, with tabular figures so a
      frame counter does not shiver as digits change width; a cool near-black ground rather
      than pure black, so the scan's own shadows survive; motion that decelerates and never
      overshoots, because the subject is physics.
      *One accent, survey-laser lime, with exactly two meanings:* this is selected, this is
      alive now. Chosen for legibility over an arbitrary photoreal interior — a scan of a real
      room is full of warm neutrals, so a warm accent disappears into it, and this colour does
      not occur domestically, which is why it reads as a marker laid over the world.
- [x] **A2 · Renderer spike — first, before anything else.** Prove the chosen splat library
      exposes per-splat centroids **and** allows a subset to be split into an independently
      transformable group.
      *Chosen: Spark (`@sparkjsdev/spark`).* Both capabilities confirmed against the real
      371 MB capture: `forEachSplat` gives per-splat centres, and `SplatMesh` is a
      `THREE.Object3D` taking a `packedSplats` directly, so a subset is transformable on its
      own. 22 web tests green, `tsc` clean.
      *The cross-check that matters:* Spark parses the capture identically to the Python
      reference — 1,495,461 splats, same bounding box, scales and opacity to three decimals —
      in 611 ms. `service/app/splat/` is a real reference implementation, not a claim.
      *Cost:* Three.js 0.169 → 0.185, which Spark requires.
      *Two things learned:* the packed format is quantised to ~4e-4, so half-extents must be
      measured from the parsed centres and not from re-read packed data; and a box at the
      centroid of a room scan comes back empty, because splats live on surfaces and the middle
      of a room is air.
- [~] **A3 · The viewport.** Full-bleed canvas, orbit, drag-and-drop `.ply`, loading state,
      count and frame rate readout in monospace.
      *Built:* Three.js and Spark outside React entirely — the scene graph is mutable 60 Hz
      state and reconciling it through a component tree would be slower and harder to reason
      about. React owns the chrome, the viewport owns the pixels, the store carries the four
      numbers that cross. Typecheck and build both clean; dev server serves.
      *Still to verify by eye:* that the capture actually renders and orbits smoothly. There is
      no browser automation in this environment, so this one needs a human at the screen.
      *Note:* the camera frames on a 2nd-to-98th-percentile extent, not the bounding box —
      floaters inflate the raw box threefold and framing on it puts the room at a speck.
- [x] **A4 · Mock server.** Every server message implemented in-browser: invents plausible
      bodies, streams a fake settle curve.
      *Proof:* 15 tests under fake timers. A crate falls, lands on its base rather than its
      centre, and stops dead rather than jittering; a dishwasher comes back with a hinge in
      degrees whose body sits on the bottom front edge, matching where the MJCF generator puts
      it; pause, play, reset and remove all behave. 33 web tests green.
      *Two real bugs it caught in itself:* reset restored orientation but never position, so a
      crate stayed where it had fallen; and the door's spring was soft enough to still be
      visibly creeping after three seconds. It is now an exponential approach, which cannot
      oscillate and is what a real damped appliance door does.
- [x] **A5 · Selection.** Box drag, screen projection, depth filtering, live count, PCA with the
      determinant flip, highlight and dim.
      *Maths done and tested* (21 tests): a box of known size comes back with the right
      half-extents; the frame is right-handed from every viewing angle; a rectangle over an
      object takes the object and leaves the wall behind it; near-invisible splats are ignored;
      a single floater in front does not drag the near plane forward. 54 web tests green.
      *Wired:* shift-drag to select, with a live count during the drag and the accent
      highlight plus a dimmed scene on release, both as GPU box edits rather than per-splat
      colour writes.
      *Two design errors the tests caught, both of which looked fine:*
      **(a)** `+front` taken from the camera's view *axis* is up to fifteen degrees off for an
      object at the edge of the frame — the face you are looking at is the one turned toward
      where you stand, not toward the centre of the screen.
      **(b)** Worse: taking `+front` as a *direction* at all makes half-extents a property of
      the viewpoint. A 600 mm box seen at 45° measures 850 mm, because the extent along a
      rotated axis is the box's projection onto it — so the physics body would be bigger than
      the object, by a factor that changed every time the user orbited. Fixed by measuring
      along the object's own PCA axes and letting the camera only choose *which of the four
      faces* is front. That is what the contract said all along: "the horizontal **principal
      axis** pointing back toward the camera".
- [x] **A6 · The interface proper.** Prompt bar on commit, pending state, object list with parts
      and joint types, physics readout.
      *Built:* the prompt bar appears at the selection, focuses itself, and teaches by example
      rather than by instruction. The object list shows each part's joint type and range with
      its unit. Space and R drive the transport, suppressed while the prompt has focus. The
      readout says which service is answering, so falling back to the in-browser one is never
      a silent substitution.
- [~] **A7 · Live binding.** Splats move out of the static scene into per-body groups; poses
      applied; quaternion conversion in exactly one place in the network layer; interpolation so
      30 Hz reads as 60 fps.
      *Built and unit-tested* (13 tests): each part's splats are baked into its body's frame
      once, so the mesh transform IS the pose and Three.js composes it — no per-Gaussian work
      per frame, no BVH refit. The round trip is asserted exact. Splats left behind are cut out
      of the static scene with a GPU box edit, because re-packing 1.5M splats mid-interaction
      would read as a hang.
      *Needs a screen:* that an object visibly settles and a door visibly swings.

---

## Integration

- [ ] **I1** Flip the mock flag; the real socket takes over.
- [ ] **I2** A real selection produces real physics and real poses.
- [ ] **I3** The rehearsal: load, select, describe, watch it land. Then the same run with the
      network pulled, confirming the fallback path is live rather than theoretical.

---

## Cut line

Mesh import, scene export and polish are Phase 9 and get cut first. The order of sacrifice, if
time runs out:

1. Cut Phase 9 entirely.
2. Reduce S7 to the fallback path only, no model.
3. **Never cut A7.** An object that becomes physical without visibly moving is not a demo, it
   is a claim.

## Open

### Segmentation needs a model, and does not have one

The largest open item, and the cause of "the removal is still not even close to good".

`web/src/selection/voxels.ts` grows the object out of the drag rectangle by connected-component
region growing on an occupancy grid. That is pure geometry. Measured on the real capture by
`voxels.capture.test.ts`, a 13,174-splat selection grew to **105,506 splats — 801%**: most of
the room.

It is not a tuning problem. A room scan is one connected mass, so "connected to" means "in the
same room as", and no density threshold or growth ratio separates a legitimate 3x recovery from
a 5x runaway — the ranges overlap. The `MAX_GROWTH` guard now makes the bad case fail visibly
and fall back to the rectangle, so removal is no longer made *worse*; it is not made right.

The fix is a segmentation model with an image prior, run over rendered views and lifted back to
splats — the prototype anticipated exactly this in `crop()`: *"the Tier-1 stand-in for a SAM 3
part mask"*. Sketch:

1. Render 8–12 views around the selection centroid from the existing Spark renderer.
2. Prompt SAM 2/3 with the drag rectangle projected into each view.
3. Vote per splat across views: a splat is owned if it projects inside the mask in a clear
   majority of the views it is visible in.
4. Keep the voxel grid — it is still what produces collision boxes, and that half works.

Only step 3 is new code; step 4 is already written and tested.

### Smaller

- A 30k-iteration capture, ideally a scene with appliances. See `DECISIONS.md`.
- `ANTHROPIC_API_KEY`, needed only at S7. The fallback is built before it.
- Dragging feels laggy: 33 ms interpolation delay stacked on the round trip. Needs client-side
  prediction for the grabbed body only.
