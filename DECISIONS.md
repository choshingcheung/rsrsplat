# Decisions

Append-only. Newest at the bottom. Every entry: what, why, when.

---

## Units and conventions — 2026-09-05

- Positions: metres, scene coordinates.
- Quaternions: (w, x, y, z), MuJoCo convention.
- Angles on the wire: DEGREES. MuJoCo qpos is radians; convert only at the boundary.
- Splat PLY: binary little-endian. Scales are log-space (apply exp).
  Opacity is pre-sigmoid (apply sigmoid).

## Architecture — 2026-09-05

- Physics in Python, rendering in the browser, WebSocket between them.
- Splat data never crosses the socket. The browser owns the Gaussians.
- Selections cross as centroid + axes + half-extents, not index arrays.
- No database in v1. In-memory session state. No auth, no accounts, no persistence.

## Name — 2026-09-05

`rsrsplat`, kept from the repo directory rather than invented. Decided by the user over
three alternatives; no reason to spend a naming cycle on a single-session tool.

## The protocol is a hand-mirrored pair, guarded by fixtures — 2026-09-05

`web/src/types/protocol.ts` and `service/app/protocol.py` describe the same messages in two
languages and are maintained by hand. Hand-mirrored files drift.

So `contract/fixtures/*.json` holds one golden example per message type, and **both** sides
have a test that parses them. Change one side of the seam and a test goes red on the other
within seconds. This is what makes the two build tracks safe to run independently.

## Four additions to the contract as specified in REPO_INIT.md — 2026-09-05

The contract in REPO_INIT.md §2.2 is nearly complete. Four gaps would each have broken an
acceptance criterion, so they are closed before either track starts building against it.

**1. `scene.load` carries the world: `up`, `groundPlane`, `sceneScale`.**
Stage 4 requires a body to fall *and come to rest*, which requires a floor. Nothing in the
original contract ever told Python where the ground was or which way was up, and since the
browser owns the Gaussians, the backend can never derive it. The frontend fits the ground
plane from the cloud and sends it once at load.

**2. `splatSubsetHint` is a symbolic region, not a free string.**
When a dishwasher comes back as shell + door, the frontend must decide which of *its* splats
are the door — the backend never saw them and cannot say. The hint names a region of the
selection's own oriented box (`front_lower`, `top`, `left_third`, …) and the client resolves
it geometrically. This is the same discipline the articulation schema already uses for
anchors: name a place, never a coordinate.

**3. `object.created` carries each part's initial pose.**
Stage 8 stores each splat group relative to its body's initial pose, but that pose never
crossed the wire. The alternative — treating the first `pose.batch` as the reference frame —
is an implicit ordering dependency, and those become drift bugs.

**4. Units are commented at every field that has one.**
Not recorded in a document, written at the declaration in both files.

## Build order: two tracks against a frozen seam — 2026-09-05

REPO_INIT.md stages the work strictly sequentially. Instead, after the contract is frozen,
the spine (Python) and the app (browser) are built as independent tracks:

- The **app** develops against a mock server implemented in the browser. The entire
  interface — selection, prompt, an object falling and settling — works with no Python
  process running.
- The **spine** develops against a headless Python test client. Every step is provable with
  `pytest`, no browser.

Two reasons. It removes the blocking dependency between halves, and the mock is also the
demo's insurance policy: if the network or the Python process dies at the venue, one flag
still leaves something that moves.

## Capture: the available scan is a 7k checkpoint — 2026-09-05

The only capture on hand is `playroom_7000.ply` (Deep Blending playroom, 370 MB). The
`_7000` is the early 3DGS training checkpoint; standard training runs to 30,000 and looks
markedly sharper. Development proceeds against it because plumbing does not care, but
selection tuning and the demo need a 30k capture — preferably a scene containing appliances,
since the articulation story is about hinged doors.

## Not ported from the prototype — 2026-09-05

- **Secrets.** The prototype's `.env` holds Tripo, Marble and Convex keys. None cross over.
- **Convex.** Explicitly excluded by REPO_INIT.md §2.3; in-memory state is correct here.
- **Desktop rendering.** `render.py`, `splat_viewer.py`, `splat_occlusion.py` and the
  MuJoCo-Warp splat path solve a problem that moved to the browser.
