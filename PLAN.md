# Working plan

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
- [ ] **S2 · Schema and validator.** Port the articulation schema and its checker.
      *Proof:* a hand-written dishwasher schema validates; at least five malformed schemas are
      rejected with errors readable enough to feed back to a model.
- [ ] **S3 · MJCF generation.** Port schema → XML.
      *Proof:* loads in MuJoCo without error. A test drives every joint through its full range
      and asserts no part passes through the shell. Explicit tests for the four silent killers:
      `autolimits="true"`, box `size` as half-extents, friction on **both** geoms of a contact
      pair, body origin on the joint anchor.
- [ ] **S4 · Ground and scene assembly.** Floor, up vector, scale.
      *Proof:* a body dropped above the floor settles rather than tunnelling, within a bounded
      number of steps.
- [ ] **S5 · Simulation loop.** Stepping at 0.002, pose broadcast at ~30 Hz, decoupled.
      *Proof:* real-time factor ≥ 1 headless; broadcast rate holds under load.
- [ ] **S6 · The service.** FastAPI, WebSocket, session state, `sim.control`, and
      `object.physicalize` end to end with a **stubbed** schema generator.
      *Proof:* a Python test client connects, physicalizes, and watches a body fall and come to
      rest. No browser, no model.
- [ ] **S7 · Language.** Model → schema, validated, two retries, then fallback.
      *Proof:* the fallback is built and passing **first**. Then "a wooden crate, heavy" gives a
      rigid body and the dishwasher sentence gives a hinge. Then a network-kill test still
      yields a working object.

---

## Track A — the app

Browser, design-led, provable with no Python process running.

- [ ] **A1 · Scaffold and design system.** Vite + React + TS + Tailwind + Three.js. Committed
      tokens before any component exists: one near-black ground, one accent, a type pairing, a
      spacing scale, motion curves. Retrofitting a visual language never happens.
- [ ] **A2 · Renderer spike — first, before anything else.** Prove the chosen splat library
      exposes per-splat centroids **and** allows a subset to be split into an independently
      transformable group.
      *Proof:* load the real capture, read the centroid array, pull a thousand splats into a
      group and translate them visibly at frame rate. If it cannot, swap library now — this
      choice silently decides whether A5 and A7 are possible.
- [ ] **A3 · The viewport.** Full-bleed canvas, orbit, drag-and-drop `.ply`, loading state,
      count and frame rate readout in monospace.
      *Proof:* the real capture renders and orbits smoothly, and the Gaussian count matches
      S1's number exactly — which makes the Python parser a free reference implementation.
- [ ] **A4 · Mock server.** Every server message implemented in-browser: invents plausible
      bodies, streams a fake settle curve.
      *Proof:* the full interface works with Python not running.
- [ ] **A5 · Selection.** Box drag, screen projection, depth filtering, live count, PCA with the
      determinant flip, highlight and dim.
      *Proof:* a synthetic cloud of known dimensions gives half-extents within a few percent and
      a positive determinant. A real scan is judged by eye — there is no honest alternative.
- [ ] **A6 · The interface proper.** Prompt bar on commit, pending state, object list with parts
      and joint types, physics readout.
- [ ] **A7 · Live binding.** Splats move out of the static scene into per-body groups; poses
      applied; quaternion conversion in exactly one place in the network layer; interpolation so
      30 Hz reads as 60 fps.
      *Proof:* against the mock first. An object visibly settles, an articulated part swings, no
      drift over sixty seconds.

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

- A 30k-iteration capture, ideally a scene with appliances. See `DECISIONS.md`.
- `ANTHROPIC_API_KEY`, needed only at S7. The fallback is built before it.
