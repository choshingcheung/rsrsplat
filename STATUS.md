# Where this actually is

Written at the point of stopping the rush to a deadline. `PLAN.md` describes the plan as it
was drawn; this describes what exists. Where the two disagree, this one is right.

Verified at the time of writing: **service 313 passed / 13 skipped**, **web 133 passed /
8 skipped**, **twin 14 passed**, ruff clean, `tsc` clean, `npm run build` clean.

---

## The demo path, end to end

Every step below runs today.

| step | how it works | state |
|---|---|---|
| Open a capture | Entry screen lists `capture/out/*.ply` from the service; drop a photo or click a row | works |
| Align the room | RANSAC planes; up from the largest parallel plane family; floor from the lowest large horizontal patch | works |
| Make the room solid | Scan voxelised at 8 cm into ~1200 static boxes, plus four walls and detected worktops | works |
| Select an object | Drag a rectangle, then the sentence goes to SAM 2 across five orbited viewpoints; splats kept when 60% of views agree | works, quality unverified |
| Make it physical | Voxel boxes → MJCF body; mass and joints from the sentence via the stored fallback library | works |
| Close the hole | Floor propagated inward from surrounding splats, with collision, applied before the body exists | works |
| Dress it with a mesh | A pinned `.glb` chosen by prompt, or Tripo image-to-3D, cached by image hash | works |
| Move it | Grab and throw; joint sliders; pose stream at 30 Hz interpolated to 60 fps | works |

## What is NOT done

- **An arm in the app.** `twin/` has a working SO-101 pick inside a splat capture (14 tests,
  block rises 110 mm), but it is a separate MuJoCo process by its own isolation contract and
  does not appear in the browser. Nothing spawns an arm in the live scene.
- **S7, the model path for language.** The stored fallback library carries every prompt. An
  `ANTHROPIC_API_KEY` is present but nothing uses it.
- **Client-side prediction while dragging.** 33 ms of interpolation delay sits on top of the
  round trip, and it is felt when grabbing rather than when watching.
- **Metric scale from measurement.** Still assumes a 2.6 m ceiling. `capture/NOTES.md` records
  that Marble worlds report no `metric_scale_factor`, so there may be nothing to read.

## What is unverified rather than unbuilt

These run without error and nobody has confirmed they look right:

- Segmentation quality on the desk capture after the multi-view change.
- Whether the healed floor patch is visible as a patch.
- Whether the pinned mesh now lands upright, coloured and on its base.

## Shelved

`service/app/arm/` — a second SO-101 implementation with damped-least-squares IK and a
scripted pick, written before `twin/` was noticed. Removed from the tree: it was unwired,
untested, and duplicated a track that already works. Preserved outside the repo. What it
measured is worth keeping:

- The model attaches cleanly via `MjSpec.attach`, which handles prefixes, assets and
  placement; hand-merging the XML is not necessary.
- A scripted pick reaches and closes, and **the grasp does not hold**: the block lifts about
  7 cm and is dropped within 4–5 cm of transport. Smaller blocks, higher friction, rolling
  friction and slower motion each changed it by under a centimetre.
- The cause is structural: the SO-101 has **one moving jaw**, which pushes a free object out
  rather than centring it. `twin/` reports a 110 mm lift using akitech's own IK, so the
  difference is worth understanding before either is trusted.

## Known-wrong things that are deliberate

- **Segmentation is not semantic beyond SAM's mask.** The voxel grow that supports the
  no-sidecar path is bounded to 18 cm past the selection so it cannot return the room. That
  is a guard rail, not a solution — see `web/src/selection/voxels.ts`.
- **The generated mesh is visual only.** Physics stays on the voxel boxes. The solver never
  sees a mesh and there is no convex decomposition anywhere.
- **Meshes are pinned by name for demos.** Content-hash caching misses whenever the camera
  moves, which would mean generating for a minute on stage.

## The three tracks in this tree

| track | owner | contract |
|---|---|---|
| `capture/` | second session | `capture/README.md` |
| `twin/` | second session | `twin/README.md` |
| everything else | this session | — |

Only ever `git add` with explicit paths. Never `git add -A`, `-u`, or `git commit -a`.
