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

## One frame for the whole system: +front, +left, +up — 2026-09-05

The audit found `anchors.py` declaring *"+x is front, +z is up, y is left-right"*, with every
stored schema, every generated MJCF and the hand-checked reference XML depending on it. The
contract had been drafted with a canonical selection frame of (right, front, up).

Those had to become one frame. Otherwise the splats `front_lower` names are not the splats the
MJCF hangs a door on, and the door swings carrying the wrong quarter of the object — which
looks *correct* in a viewer, because a door swinging is a door swinging.

Changed the contract rather than the prototype: the contract had no dependents yet, the
prototype has many. The canonical selection frame is now

    column 0 = +front, column 1 = +left, column 2 = +up

right-handed, since +left is `up × front`. `SubsetRegion` coordinates follow: u = front,
v = left, w = up. Pinned on both sides by a test asserting the golden dishwasher's hinge anchor
lands on the bottom front edge, computed from the frame rather than hardcoded.

Note that the prototype's anchor table has its own left/right labels backwards — in a
right-handed frame with +x front and +z up, +y is left, not right. Harmless on a symmetric box,
wrong for a side-hinged door. Recorded in `PORTING.md`; fix during the S2 port.

## Scene coordinates are metric and z-up by construction — 2026-09-05

The capture on hand is neither: `playroom_7000.ply` measures 27 x 34 x 34 in arbitrary units
with an arbitrary up direction, which is normal for trained 3DGS. Something has to fix that,
and there were two places to do it.

**The browser does it, once, at load.** It owns the Gaussians, it has to choose a frame to
render them in anyway, and the prototype's `ground.py` already derives up and scale from the
cloud. So the scene the rest of the system sees is metric and z-up.

The alternative — carrying an arbitrary `up` and `sceneScale` into the service — spreads one
transform across gravity, the floor plane's orientation, object placement, and the pose
return path. Four conversion points instead of one, and conversion points are where sign
errors live.

`WorldFrame` still carries `up` and `sceneScale` on the wire, and the service **validates**
them rather than assuming. A capture that arrives tilted or unscaled fails immediately with a
message saying so, instead of producing a scene where gravity points at a wall.

`groundHeight` stays genuinely variable: the floor is wherever the fitted plane put it, and
that is rarely z = 0.
