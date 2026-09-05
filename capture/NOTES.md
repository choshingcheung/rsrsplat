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
y: -1.047 .. 2.189   (two sharp peaks; x and z have none)

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

## The image path, and a second world — 2026-09-05

A real photograph of a desk, `marble-1.0-draft`, 230 credits, 26 seconds.

**The upload path failed the first time it ever ran**, which is exactly where it was expected
to. The media asset comes back as **`media_asset_id`**, not `id` — the same divergence as
`world_id`, from the same documentation. Reading only `id` aborted the run.

It aborted *safely*: the client's own validation threw before the generate call, so nothing
was charged. The ledger recorded a `created` run with no operation id, and `marble status`
correctly refused to resume it — nothing had been paid for.

Two further things the live response carried that the documentation did not mention: a
`curl_example` field, and `x-goog-content-length-range: 0,104857600` — a **100 MB** upload
limit, not the 1 GB the sample header in the docs implied.

`fixtures/prepare_upload.json` is now recorded from the live response, with the signature
replaced by a placeholder: a signed URL is a capability and does not belong in a committed
fixture. The old error message printed the whole response body, signed URL included; it now
prints key names only.

### `semantics_metadata` is null for an image world too

Same as the text world. So this is not a property of text generation — draft worlds simply do
not carry a metric scale. **The sidecar cannot supply one**, and `ground.ts` must fit it.

### y-up confirmed on a second, independent world

The desk capture is much larger and messier than the kitchen — a p1–p99 extent of 7.2 x 4.7 x
12.6 against raw extents of 27 x 38 x 43, so **floaters inflate this one about fourfold**,
just as they do the playroom capture. Any axis test on raw extents is worthless here.

Counting *sharp* peaks — local maxima well above their neighbourhood, which is what
`ground.ts` means by a layer — on the trimmed range:

| axis | sharp peaks | what they are |
|---|---|---|
| x | 1 | a wall |
| **y** | **2**, at -1.75 (20.0%) and +0.50 (12.1%) | **floor and ceiling** |
| z | 3, all within 1.5 units of each other | the desk surface and the window reveal |

Two well-separated peaks with the denser one low: the same signature the kitchen showed. So
**+Y is up and the floor is at minimum y** on both a text-derived and an image-derived world.

**A heuristic that failed, recorded so it is not tried again:** taking the vertical axis to be
the one with the *smallest extent* works on a clean capture and is badly wrong on this one —
floaters made x look smallest, and the answer came out as x with the floor overhead. Counting
bins above a threshold fails too, because a broad hump trips it; it has to be local maxima.
`ground.ts` reaching for RANSAC planes and layer scores rather than extents is right, and this
is a second capture demonstrating why.

---

## Multi-image, and a caveat on the axis sign — 2026-09-05

All three desk photographs into one world: `marble-1.0-draft`, **250 credits** (multi-image
buys a 100-credit panorama against 80 for a single image), 26 seconds.

Marble accepted all three views at the azimuths given and echoed them back on the world, along
with an undocumented `reconstruct_images` field -- so the extra views are being used for
reconstruction rather than the first being taken and the rest ignored.

**Azimuths had to be estimated by eye.** The photographs carry no `GPSImgDirection`: EXIF has
orientation and nothing else, so location services were off or the metadata was stripped. The
three frames cover one arc of roughly 45 degrees, given as 0/20/45. Spreading them evenly to
0/120/240 -- which is what the CLI assumes when no azimuth is passed, and it says so -- would
claim a baseline the photographs do not have.

### The output is structurally the same

2,276,736 splats, SH degree 0, 127.5 MB, exactly as both single-prompt worlds. **A draft world
appears to have a fixed splat budget**, so more input views buy better geometry within that
budget, not more of it. `semantics_metadata` is null here too, on a third world.

### The floor-detection proxies disagree on this one

Worth recording, because the axis claim above rests on them:

| capture | denser band | warmer band |
|---|---|---|
| kitchen | low | peaks sit at the trimmed range edge; not sampled |
| desk, one image | low (20.0% vs 12.1%) | low (+0.155 vs +0.085) |
| desk, three images | **high** (18.5% vs 16.1%) | **low** (+0.167 vs +0.237) |

The **axis** is not in doubt: all three worlds put two well-separated dense bands on y and
nothing comparable on x or z, and the desk peaks land within 0.1 units of each other whether
built from one photograph or three.

The **sign** is confirmed on two captures and unresolved by proxy on the third, where density
and colour point opposite ways. There is no reason to think the export convention changed
between two worlds made minutes apart on the same model -- but "no reason to think" is not a
measurement, and this is the honest state of it. A single look at a rendered capture settles
it in seconds and is worth more than another proxy.

---

## Still to measure

- Whether a **standard-model** world (`marble-1.1`, ~$1.26) populates `semantics_metadata`.
  That is the single open question that decides whether the sidecar can ever carry a metric
  scale, and it cannot be answered without spending.
- Whether a **panorama** input behaves the same, and whether `is_pano` detection works. A
  pano costs 0 credits to convert, so this is the cheapest untested path left.
- Whether **multi-image** improves geometry. It needs azimuths, and photographs taken from a
  narrow arc cannot supply honest ones.
