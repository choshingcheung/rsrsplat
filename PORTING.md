# Porting Inventory

Source: `c:\vscode workspace\akitech\splat` @ `3e17ee4` ("Correct a stale test count and record Phase 6")
Audited: 2026-09-05

No code moved in this pass. Every source file appears below exactly once.

The prototype is unusually good: heavily documented, ~180 passing tests, and most of its hard
parts were measured on the machine rather than assumed. The bulk of the value is in `src/`. The
liability is concentrated in one place — it is an **object-centric desktop pipeline**, and
rsrsplat is a **scene-centric browser app**. Anything that renders, or that assumes it owns one
object in isolation, does not survive the move.

---

## Port

| Source | What it does | Destination | Notes |
|---|---|---|---|
| `src/splat_io.py` → `load_ply`, `sigmoid`, `normalize_quat`, `sh_degree` | Parses binary LE 3DGS PLY. `exp` on log-space scales, numerically stable `sigmoid` on pre-sigmoid opacity, band-0 SH to linear RGB via `0.5 + C0·f_dc`, quaternion normalise. Detects SH degree from the `f_rest_*` count. | `service/app/splat/ply.py` | **S1.** Highest-confidence port in the list; all four conventions were checked against MJWarp's source rather than guessed. `sh_degree()` re-reads the entire PLY a second time — fix while porting (see Rewrite). |
| `src/splat_io.py` → `Splats`, `bounds`, `extent`, `report` | Typed array container plus the bounding-box/extent report. | `service/app/splat/ply.py` | **S1.** `report()` is exactly the S1 acceptance output. |
| `src/splat_io.py` → `TRANSFORMS`, `apply_transform` | The four candidate coordinate conversions, including Marble's OpenCV→z-up. Correctly rotates per-splat quaternions too, not just positions. | `service/app/splat/transforms.py` | **S1.** The docstring's insistence that the transform is chosen by the caller and written down, never guessed in the loader, is right and should survive. |
| `src/splat_io.py` → `quat_mul`, `quat_to_mat`, `mat_to_quat`, `crop`, `synth_box` | Vectorised (w,x,y,z) quaternion maths; Shepperd's method for matrix→quat; an axis-aligned crop; a synthetic cloud of known dimensions. | `service/app/splat/quat.py`, `service/app/splat/synth.py` | **S1.** `synth_box` is the basis of the A5 acceptance test — a cloud whose every property we chose, so a wrong answer is unambiguous. |
| `src/anchors.py` (whole file) | Symbolic anchor and axis names resolved against half-extents. One table, read by both the validator and the prompt. | `service/app/schema/anchors.py` | **S2.** The load-bearing design idea of the whole system. Port intact — but see Hazards for the left/right mislabel. |
| `src/schema.py` (whole file) | The articulation vocabulary and hard validation: joint types, axes, anchors, affordance actions, plausibility bounds, inverted ranges, `requires` clauses naming parts that do not exist. Error strings written to be fed back to a model. | `service/app/schema/validate.py` | **S2.** Port intact. Needs one addition: `free` is missing from `VALID_JOINTS`, and rsrsplat requires it for a loose crate. |
| `src/schema.py` → `_check_satisfiable` | Rejects a precondition that can never hold, because a sprung button returns to rest the instant it is released. | `service/app/schema/validate.py` | **S2.** Subtle, hard-won, and found on a real generated schema. Would not be reinvented. |
| `src/gen_mjcf.py` → the non-negotiables | `<compiler angle="degree" autolimits="true"/>`; body origin placed **on** the joint anchor so every joint is `pos="0 0 0"`; box `size` as half-extents; `button` expanded into a sprung slide. | `service/app/mjcf/build.py` | **S3.** Port the skeleton and every one of these invariants. The geometry it hangs off them is a rewrite — see below. |
| `src/precond.py` (whole file) | Symbolic plan checking and live `check_requires`, with the degrees/radians conversion isolated to hinges. | `service/app/schema/precond.py` | **S2.** Not on the critical path for the demo, but it is small, tested, and it is the "type checker, not simulator" claim. Cheap to keep. |
| `src/binding.py` → the algorithm only | Freeze a splat subset into a body's local frame at that body's *current* pose, then each frame write `R·local + t` back, composing per-splat orientations with the body's. | `web/src/scene/binding.ts` | **A7.** The Python does not survive — the backend never sees splats. The maths and, more importantly, the reasoning in its docstring transfer directly. Capturing against the current pose rather than identity is the detail that stops a rotated body's splats swinging on the first step. |
| `src/ground.py` → `find_planes`, `layer_score`, `estimate_up`, `floor_is_denser_end`, `rotation_to_z` | Derives up and the floor from the cloud by RANSAC plus a layeredness score along each candidate normal. Measured 16 sharp layers along up vs 4 along a wall normal on the playroom capture. | `web/src/scene/ground.ts` (primary), `service/app/splat/ground.py` (reference) | **A3/S4.** This fills the contract's `WorldFrame`. It has to run in the browser now, because the browser owns the Gaussians — so the real destination is TypeScript, with the Python kept as the checkable reference. The docstring records two plausible rules that both *fail* here (largest plane is a wall; largest parallel family is also the wall family); that negative result is worth more than the code. |
| `src/ground.py` → `horizontal_surfaces`, `support_patch`, `Surface` | Finds flat patches big enough to stand something on, with occupied area rather than bounding-box area. | deferred | Not needed for the core interaction — the user points at the object. Revisit only if "drop it on the nearest surface" becomes a feature. |
| `src/describe.py` → `output_schema`, `build_prompt`, `request_kwargs`, retry-then-fallback control flow | Noun phrase → schema via a constrained-output API call, with the vocabulary generated from `anchors.py` rather than retyped, validation feeding errors back, two retries, then a stored schema. | `service/app/schema/describe.py` | **S7.** Port the structure wholesale. The prompt needs rewriting for rsrsplat's inputs (a free-text sentence plus measured half-extents, not a bare noun phrase). `MODEL = "claude-opus-5"` carries over. |
| `src/describe.py` → `fallback_path`, `FALLBACK_DIR` | Stored-schema lookup that keeps the pipeline alive with no network and no key. | `service/app/schema/fallback.py` | **S7, and built before the API path.** This is what survives venue wifi. |
| `assets/schemas/*.json` (5 files) | Hand-written schemas: dishwasher, microwave, drawers, bin, toaster oven. | `service/app/schema/fallbacks/` | **S7.** These *are* the fallback library. Small, no secrets, plain JSON. |
| `assets/mjcf/dishwasher_reference.xml` | Hand-written MJCF, checked joint by joint, that the generator must reproduce. | `service/tests/data/dishwasher_reference.xml` | **S3.** A generated file differing from this names exactly which resolver is wrong. That is the whole reason it exists. |
| `scripts/check_joints.py` | Drives every joint through its range and reports where the geometry actually ends up. | `service/tests/test_mjcf_sweep.py` | **S3.** This is the S3 acceptance criterion already written. Becomes a test rather than a script. |
| `scripts/check_fallbacks.py` | Asserts every stored schema still produces a working twin offline. | `service/tests/test_fallbacks.py` | **S7.** Same: becomes a test. |
| `tests/test_splat_io.py`, `test_anchors.py`, `test_schema.py`, `test_gen_mjcf.py`, `test_precond.py` | Cover exactly the modules being ported. | `service/tests/` | Port alongside their subjects, with imports rewritten. Free confidence. |
| `tests/test_binding.py` | Checks the binding rotation composition against MuJoCo's own `mju_mulQuat` rather than trusting the algebra. | `web/src/scene/binding.test.ts` | **A7.** Translate the *assertions*; there is no `mju_mulQuat` in the browser, so the reference values get baked in from a one-off Python run. |

## Rewrite

The idea is right; the implementation should not survive.

- **`src/gen_mjcf.py`'s geometry.** Every body it emits is dishwasher-shaped: a five-walled
  hollow shell, a door panel with a capsule handle, a tray. rsrsplat has to physicalise a wooden
  crate, and a crate is a solid box with a free joint, not a hollow appliance with a handle. The
  generator needs a body-shape decision — solid box, hollow shell, or panel — driven by the
  schema rather than assumed. Keep every invariant listed in the Port table; replace the
  geometry that hangs off them.
- **`src/gen_mjcf.py`'s world.** It emits its own floor plane, light and headlight, and places
  the object at `frame.origin`. In rsrsplat the floor comes from `WorldFrame` on `scene.load`,
  and the object is placed at the selection's centroid with the selection's orientation. The
  scene assembly is a different problem to the body assembly and should be a separate module.
- **`VALID_JOINTS` has no `free`.** The prototype only ever articulated objects bolted in place.
  rsrsplat's most common case — "a wooden crate, heavy" — is a free body that falls. Add it to
  the vocabulary, the validator, the prompt and the generator together.
- **`src/splat_io.sh_degree`.** Re-reads the whole PLY to count `f_rest_*` properties, so
  reporting SH degree doubles the cost of loading a 370 MB file. Read the header once and return
  the degree alongside the arrays.
- **`src/render.py` → `fit_to_room`.** The right idea — a trained splat has no idea how big it
  is or which way is up — but it is the crude version, scaling by an assumed longest axis and
  dropping the floor by percentile. `ground.py` supersedes it and is honest about doing so.
- **`src/binding.py`.** Port the algorithm, not the file. See the Port table.
- **`src/handoff.py`.** A well-designed workaround for having no API credits: a duck-typed stand-in
  for the Anthropic client, so the prompt, parse, validation and retry are all exercised for real.
  rsrsplat should keep the *injectable-client* shape that makes it possible — but the handoff
  itself is a bridge for a constraint we do not currently have.

## Leave

| Source | Why |
|---|---|
| `src/render.py` (except `fit_to_room`, above) | MJWarp splat raytracing, render contexts, RGB unpacking. Rendering moved to the browser; this is the half being deliberately discarded. |
| `src/meshprep.py` | Solidify, normalise, convex-decompose a generated mesh. Genuinely valuable and the measurements behind it are real (0/9 bearings land inside a teapot before preparation, 9/9 after). But it belongs to mesh import, which is Phase 9 and first on the cut line. Revisit only if 0–8 are green. |
| `src/handoff.py` and `handoff/**` | See Rewrite. The stored request/response JSON is prototype session data. |
| `scripts/splat_viewer.py`, `scripts/playground.py`, `scripts/splat_occlusion.py` | Interactive desktop MuJoCo windows and an occlusion measurement. All three answer questions that the browser now answers differently. |
| `scripts/mesh_to_physics.py`, `scripts/build_page.py`, `scripts/bench_refit.py` | Mesh pipeline demo, an explainer page with renders inlined as data URIs, and a refit benchmark. Presentation and Phase-9 territory. |
| `scripts/generalize.py` | Runs the pipeline over several objects and prints a table. Useful as a prototype harness; superseded by the S7 tests. |
| `scripts/fetch_capture.py` | Downloads the 370 MB sample. rsrsplat takes a capture by drag-and-drop; a fetch script is not the workflow. Worth revisiting only as a developer convenience. |
| `tests/test_render.py`, `test_meshprep.py`, `test_handoff.py`, `test_ground.py` | Cover code that is being left or deferred. `test_ground.py` gets rewritten in TypeScript alongside its subject. |
| `assets/meshes/**`, `assets/mjcf/generated/**` | A test STL, a CoACD cache, and generated output. Regenerable or Phase 9. |
| `captures/playroom_7000.ply` | 370 MB. Stays out of git, referenced by path. See `DECISIONS.md` on capture quality. |
| `NOTES.md`, `TONIGHT.md`, `HACKATHON_PLAN.md`, `docs/action-items.md`, `CLAUDE.md`, `README.md` | Prototype narrative and planning. Facts worth keeping have been lifted into `DECISIONS.md` and `CLAUDE.md` here. |
| `.env` | **Secrets.** See Hazards. |
| `.venv/`, `.pytest_cache/`, `out/`, `pytest.ini`, `.gitignore` | Environment and build artefacts. |

---

## Hazards found

**1. Real credentials in `.env`.** `TRIPO_API_KEY`, `MARBLE_API_KEY`, `CONVEX_URL`,
`CONVEX_DEPLOY_KEY`. Not read, not copied, and nothing derived from them. rsrsplat's
`.env.example` was written from scratch. Rule 3 of §3.1, observed.

**2. Frame conflict, and it is the important one.** `anchors.py` declares
*"+x is front, +z is up, y is left-right"*, and every stored schema, every generated MJCF and
the reference XML depend on it. The rsrsplat contract had been drafted with a canonical
selection frame of (right, front, up). Those must be the same frame — otherwise the splats
`front_lower` selects are not the splats the MJCF hangs a door on, and the door would swing
carrying the wrong quarter of the object. **Resolved by changing rsrsplat's contract**, not the
prototype's convention: the canonical frame is now column 0 = +front, column 1 = +left,
column 2 = +up. The contract had no dependents yet; the prototype has many.

**3. `anchors.py` labels its left and right backwards.** In a right-handed frame with +x front
and +z up, +y is **left** — `y = z × x`. The table has `"left_front_edge"` at `-hy` and
`"right_front_edge"` at `+hy`, so both are on the opposite side from their names. Harmless on a
symmetric box, which is why it survived, but it puts a side-hinged door's hinge on the wrong
edge. Fix during the S2 port and add a test that pins the sign.

**4. `_add_shell` hardcodes appliance geometry.** Noted under Rewrite; repeated here because a
straight file copy would silently make every physicalised object a hollow five-walled box, and
it would look plausible in the viewer.

**5. `describe.py`'s `credential_source()` can print a key.** The function itself is benign and
worth porting — it *reports* which credential the SDK will use rather than gating on it, and its
docstring records a real bug where gating on the Linux profile path silently routed every
request to the stored fallback on Windows. The one problem is this line:

```python
return f"ANTHROPIC_API_KEY, but it is a placeholder ({key!r}) — fill it in or unset it"
```

It interpolates the key itself whenever the value is under 20 characters. That is only reached
for short or placeholder values, but a truncated or mistyped real key would land in a log.
Port the function; report the length and prefix, never the value.

**6. `FALLBACK_DIR` and `CACHE` are computed as `Path(__file__).parents[1] / ...`.** Fragile
against the new layout, and `meshprep`'s cache path escapes the package entirely. Rewrite both
as explicit configuration when porting.

**7. No absolute machine paths found in `src/`.** Checked. The scripts reference `captures/` and
`out/` relatively. Cleaner than expected.
