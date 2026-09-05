# Notes

Facts measured against the real API, not assumed. Newest at the bottom. Same convention as the
repo-root `NOTES.md`, kept separate so the two sessions never edit the same file.

---

## The API, as it actually answers — 2026-09-05

First contact with the live API, through three calls that cost nothing, made before a single
credit was spent. Worth doing: **two of the three shapes were wrong**, and neither would have
failed loudly.

### `GET /credits` — correct as documented

`{"remaining_credits": 37000}`. The fixture was right and the money guard reads a real number.

### Listing worlds is `POST /worlds:list`, not `GET /worlds`

The published reference names a "List worlds" endpoint but does not spell out its route. The
guess was wrong. Probed against the live API:

| request | result |
|---|---|
| `GET /worlds` | 404 `{"detail":"Not Found"}` |
| `GET /worlds?limit=5` | 404 |
| `GET /worlds:list` | 405 Method Not Allowed |
| **`POST /worlds:list`** | **200 `{"worlds":[],"next_page_token":null}`** |

So the envelope is `worlds`, and pagination is `next_page_token` — not `items`, `data`, or any
of the three spellings the client had been written to tolerate. The body is permissive:
`{}`, `{"page_size": 5}` and `{"limit": 5}` all return 200, so unknown fields are ignored
rather than rejected. `page_size` is used, matching the `next_page_token` naming.

**Why this one matters more than it looks.** `marble worlds` is the recovery path for the one
irreducible failure in this tool: a crash between the API accepting a generation and the
response arriving, which leaves a paid world with no local record. That path was broken, and
it would only have been discovered while trying to recover a world someone had already paid
for.

### `GET /worlds/{id}` returns the world bare, keyed by `world_id`

The documentation describes the world nested under a `world` field, with an `id`. **Both are
wrong against the live API**, which returns:

```
top-level keys: world_id, display_name, tags, assets, created_at,
                updated_at, permission, world_prompt, world_marble_url, model
```

So there is no envelope, and the identifier is `world_id`, not `id`. Reading `id` printed a
blank column in `marble worlds` and would have written a null id into every sidecar.

`world_id_of()` accepts either spelling and the client tolerates either shape, because being
wrong in this direction is silent: it does not fail, it just quietly produces a less useful
file. That is the failure mode the whole sidecar idea is most exposed to.

---

## The capture itself — 2026-09-05

One `marble-1.0-draft` world from a text prompt: *"a domestic kitchen with a dishwasher, a wall
oven and fitted cupboards, photorealistic interior"*.

| measure | expected | **actual** |
|---|---|---|
| wall time, generate | about 5 minutes | **21 seconds** |
| splats | unknown | **2,276,736** (the playroom capture has 1,495,461) |
| SH degree | 3, if it matched the playroom | **0** |
| properties per splat | 62 at degree 3 | **14** |
| file size | | **127.5 MB** |
| credits charged | 230 estimated | **230 exactly** (37,000 -> 36,770) |
| `metric_scale_factor` | | **null** |
| `ground_plane_offset` | | **null** |

**Draft generation is 21 seconds, not 5 minutes.** The documented figure is presumably for the
standard model. Worth knowing: the poll timeout budget of 30 minutes is enormously generous,
and iterating on prompts is far cheaper in time than planned.

**There are no spherical harmonics.** 14 properties: `x y z f_dc_0..2 opacity scale_0..2
rot_0..3`. No `f_rest_*`, and no `nx ny nz` either. So Marble's PLY carries base colour only —
no view-dependent shading — and a splat costs 56 bytes rather than 248. The verifier's SH
degree table already handled 0; it was written expecting 3.

**The estimate was exact, not an upper bound.** 230 credits quoted, 230 charged. The pricing
model in `client.py` is right for a draft text run.

### `semantics_metadata` is null, and it is nested

Two things wrong with what was assumed. It lives at **`assets.splats.semantics_metadata`**,
not at the top level of the world — and for this draft world its value is **`null`**.

So the sidecar cannot promise a metric scale. `semantics_of()` now reads the right place and
returns `{}` when there is nothing, and the sidecar records `"reported": false` rather than
implying the numbers were never asked for.

**What this means for Track A:** the RANSAC ground fitting in `web/src/scene/ground.ts` is
**not redundant**. The hoped-for cross-check does not exist for draft worlds. Whether a
standard-model world populates the field is untested — it would cost $1.26 to find out.

### The axis convention, settled by measurement

The question `CLAUDE.md` and the World Labs docs disagreed about. Density along each axis of
the real file, which is the test `ground.ts` itself uses — a floor is the denser extreme,
because objects collect on it and a ceiling is flat and featureless:

```
y: -1.047 .. 2.189   (the smallest extent, so the vertical axis)

density along y, low -> high:
  -0.723  35.12%  ############################################################
  -0.561   4.35%  #######
   ...      ~4.4% each
   0.895  24.83%  ##########################################
```

Nothing at all outside `[-0.72, 1.06]`: two dense slabs with sparse room between them.
**35.1% at the low end against 24.8% at the high end, so the floor is at MINIMUM y, and +Y is
UP.**

That **contradicts `CLAUDE.md`**, which says Marble is OpenCV with +y down. It is right about
the axis and wrong about the sign, at least for a text-generated draft world exported as PLY.

The transform that lands this Z-up with the floor underfoot is **`opencv_to_zup`** from
`service/app/splat/transforms.py`, whose matrix gives `new_z = old_y`:

| transform | resulting z range | floor ends up |
|---|---|---|
| **`opencv_to_zup`** | `-1.047 .. 2.189` | **at minimum z — correct** |
| `yup_to_zup` | `-2.189 .. 1.047` | at maximum z — upside down |

An irony worth recording: the transform *named* for OpenCV is the correct one, but not for the
reason its name implies — the file is plain y-up. The name is misleading; the matrix is right.

### The room is about 1.6 units tall

Floor at y = -0.72, ceiling at y = +0.90. If a domestic ceiling is the 2.6 m that
`ground.ts` assumes, the implied scale factor is about **1.6 metres per unit**, and the room's
footprint is roughly 7.3 x 9.9 m. That is a large kitchen, which is consistent with a
generated 360 world including geometry beyond the room itself. Since Marble reported no
`metric_scale_factor`, this fitted number is the only one available.

---

## Still to measure

- Whether a **standard-model** world (`marble-1.1`, ~$1.26) populates `semantics_metadata`.
  That is the single open question that decides whether the sidecar can ever carry a metric
  scale, and it cannot be answered without spending.
- Whether an **image** prompt behaves the same. The upload path -- prepare, signed PUT,
  required headers -- has still never run against the live API. It is the code most likely to
  be wrong and the least exercised.
- Whether the axis finding holds for image- and panorama-derived worlds, or is particular to
  text generation.
