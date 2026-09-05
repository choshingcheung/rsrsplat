# rsrsplat — project rules

Read `README.md` for what this is, `PLAN.md` for what is built and what is next, and
`DECISIONS.md` before changing anything architectural. `REPO_INIT.md` is the original spec and
is history, not a live document — where it and `DECISIONS.md` disagree, `DECISIONS.md` wins.

## The three conventions that cost an hour each when broken

- **Quaternions are `(w, x, y, z)`** on the wire, MuJoCo convention. Three.js `Quaternion.set`
  takes `(x, y, z, w)`. Convert in exactly one place, in `web/src/net/`, and comment it there.
- **Angles on the wire are degrees.** MuJoCo `qpos` is radians. Convert at the socket boundary
  and nowhere else.
- **Positions are metres, in scene coordinates.** Always.

## Other things that will silently cost you

- Splat PLY scales are **log-space** — apply `exp`. Opacity is **pre-sigmoid** — apply `sigmoid`.
- Marble uses OpenCV convention (+x left, +y down, +z forward). Negate y and z for OpenGL-style.
- PCA eigenvectors come back with arbitrary sign. **Check the determinant** and flip a column if
  negative — a left-handed frame is a reflection, not a rotation, and mirrors everything after it.
- MJCF box `size` is **half-extents**.
- Without `<compiler autolimits="true"/>`, joint ranges are silently ignored.
- MuJoCo takes the elementwise **maximum** of friction across a contact pair. Setting one geom
  does nothing.
- A body with no joint does not fall. It is welded to its parent.
- MuJoCo collides a mesh against its **convex hull**, which fills in every cavity.

## The seam

`web/src/types/protocol.ts` and `service/app/protocol.py` are a hand-mirrored pair. Change one,
change the other, and update `contract/fixtures/` — both sides have tests over those fixtures
and both will go red if you do not.

## Working rules

- The splat data never crosses the socket. If you find yourself sending Gaussians to Python,
  the design has gone wrong.
- Every ported function gets a test, even a trivial one proving it runs on a known input.
- No secrets, ever. No `.env` contents in code, in commits, or in output.
- No database, no auth, no accounts, no settings page. See `PLAN.md` cut line.
- Large binaries (`.ply`, `.glb`) stay out of git. Reference them by path.

## Running things

```bash
# service
cd service && python -m pytest tests/ -q
uvicorn app.main:app --reload

# web
cd web && npm run dev
```
