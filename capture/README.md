# capture — a Marble world, as a `.ply`

**Read this first if you are working in the other half of this repo.** It says what is being
built here, and — more importantly — exactly what this track will and will not touch.

Everything in this directory is being developed in parallel with Tracks S and A, in the same
working tree, by a second session. The point of the contract below is that neither side can
break the other's build.

---

## What this is

A standalone command-line tool that turns an image or a text prompt into a Gaussian splat
capture, using World Labs' Marble (World) API. Image in, `.ply` out, plus a small JSON
sidecar recording what Marble knows about the world it made.

It is a **supply line, not a feature**. It does not run during the app. It has no socket, no
UI, and nothing imports it. You run it, wait about half a minute on the draft model, and get a
file you can drag into the viewport.

## Why it exists

`README.md` names World Labs Marble as the ideal input, and the primary input is a `.ply`.
But the only capture on hand is `playroom_7000.ply` — a 7,000-iteration checkpoint, as
`DECISIONS.md` records, where standard training runs to 30,000. `PLAN.md`'s **Open** section
asks for a 30k capture, "preferably a scene containing appliances, since the articulation
story is about hinged doors."

Nothing in the repo can produce one. This is that.

---

## The isolation contract

**This track owns exactly one path: `capture/`.** Everything else in the repo is read-only to
it, for as long as both sessions are running.

### Files this track will never edit

Not one of these is touched, and none appears in any commit from this side:

    .env.example      .gitignore        CLAUDE.md         README.md
    PLAN.md           DECISIONS.md      NOTES.md          PORTING.md
    REPO_INIT.md      contract/**       service/**        web/**       assets/**

That includes the two it would be most natural to want — `.env.example`, for the new API key,
and the root `.gitignore`, for the new artefacts. Both are avoided deliberately:

- **The API key** is read from the environment, or from the repo-root `.env`, which is
  gitignored and therefore cannot ever produce a merge conflict. The variable is documented
  here instead of in `.env.example`.
- **Ignore rules** live in `capture/.gitignore`. Git reads nested ignore files, so the root
  one never has to learn about `runs/` or downloaded captures.

If this track ever genuinely needs a shared file changed, it will ask first, do it as a single
one-line commit of its own, and never bundle it with anything else.

### No code crosses either

Nothing under `capture/` imports `service.app.*` or anything from `web/`. Where the repo's PLY
reader is wanted as an independent cross-check, it is invoked as a **subprocess** —
`python -m app.splat <file>` — which is read-only, adds no coupling, and is the same reference
`NOTES.md` used to measure the playroom capture.

`capture/` has its own `pyproject.toml` and its own `.venv`. It adds no dependency to
`service/pyproject.toml` or `web/package.json`.

### Git discipline

We share one index, so:

- Only ever `git add capture/...`, with explicit paths. Never `git add -A`, `git add -u` or
  `git commit -a` — any of those would sweep your half-finished work into a commit from this
  side.
- If `.git/index.lock` is held, wait and retry. It is never cleared by force.
- No rebases, no history rewriting, no force pushes, no branch switching.

### If you need something from this side

Say so in your session. This track can produce a capture on demand, or report what Marble says
about a world, but it will not reach into `web/` or `service/` to wire anything up.

---

## What actually crosses between the tracks

**Nothing at runtime.** No socket, no import, no shared module. The entire interface is a file
on disk that a human drags into the viewport.

Two files come out of a run:

    <name>.ply           the capture, standard binary little-endian 3DGS
    <name>.marble.json   what Marble knows about it

### The scale cross-check this track was going to offer does not exist

Worth stating plainly, because the earlier version of this file promised it. The plan was that
the sidecar would carry Marble's own `semantics_metadata` — `metric_scale_factor` and
`ground_plane_offset` — giving an independent answer to what `web/src/scene/ground.ts` derives
by RANSAC and layer scoring.

**Measured on two real worlds, one text-derived and one from a photograph: it is `null` in
both.** Draft worlds do not carry it. The field is real, and nested under
`assets.splats.semantics_metadata` rather than at the top level as documented, but it is empty.
Whether a standard-model world populates it is untested and costs $1.26 to find out.

So the sidecar records `"reported": false` rather than implying the numbers were never asked
for, and **the ground fitting in `ground.ts` is not redundant** — nothing here can replace it.

### What this track can offer instead is the axis

A measured fact rather than a promise, confirmed on both worlds:

> In Marble's exported PLY the vertical axis is **Y**, with **+Y up** and the **floor at
> minimum y**. `opencv_to_zup` in `service/app/splat/transforms.py` lands it Z-up the right way
> up; `yup_to_zup` inverts it and puts the floor overhead.

Note this **contradicts the repo-root `CLAUDE.md`**, which says Marble is OpenCV with `+y`
down — right about the axis, wrong about the sign. `CLAUDE.md` is on this track's never-touch
list, so it has not been corrected there; this is the only record.

Every sidecar carries the same statement in its `axes` field, so a capture found later is
self-describing.

---

## The API, in brief

Base `https://api.worldlabs.ai/marble/v1/`, authenticated with a `WLT-Api-Key` header.
Generation is asynchronous. The documentation says about five minutes; a draft world measured
21 and 26 seconds. The larger models are untested.

| # | Call |
|---|---|
| 1 | `POST /media-assets:prepare_upload` → signed `upload_url` + required headers |
| 2 | `PUT` the image to that URL |
| 3 | `POST /worlds:generate` → `operation_id` |
| 4 | `GET /operations/{id}` until `done` (`metadata.world_id` appears early) |
| 5 | `POST /worlds/{world_id}:export` with `{asset_type: "splats", format: "ply"}` |
| 6 | download the signed URL from `response.url` |

Three facts worth knowing: **rate limits apply to generation starts, not to polling** (3/min on
the default tier), **PLY export costs nothing** beyond the generation itself, and the signed
upload accepts at most **100 MB** — the limit the live API returns, not the 1 GB implied by the
sample header in the documentation.

**Two of these paths do not match the published reference**, and both fail silently rather than
loudly: listing worlds is `POST /worlds:list` (not `GET /worlds`), and identifiers come back as
`world_id` and `media_asset_id` (not `id`). See `NOTES.md`.

### Money

Real money, so it is guarded rather than trusted. Credits are $1.00 per 1,250, minimum
purchase $5.00.

| model | credits | ≈ |
|---|---|---|
| `marble-1.0-draft` + image pano | 150 + 80 | **$0.18** |
| `marble-1.1` + image pano | 1,500 + 80 | $1.26 |
| mesh export (not used here) | 3,500 | $2.80 |

Draft is the default. Anything dearer needs an explicit flag, the balance is checked before a
run starts, and `--dry-run` prices a run without spending. The estimated cost is written to the
run ledger *before* the generate call is made, so a crash cannot lose a paid job.

---

## Decisions, and why

- **Python, synchronous `httpx`.** A batch job that spends its time waiting on one remote job
  gains nothing from async and loses testability. Two dependencies: `httpx` and `pytest`.
- **A hand-rolled client**, rather than the `worldlabs-api-python` library the docs mention.
  Six endpoints is about 150 lines that can be tested exhaustively offline; an unofficial
  dependency for that is a poor trade.
- **PLY, not SPZ.** SPZ is Marble's native and default format, but rsrsplat requires standard
  binary little-endian 3DGS, and the PLY conversion is free.
- **Draft by default.** The expensive mistake available here is an accidental
  `--model marble-1.1-plus` inside a loop.
- **The tests never touch the network.** Recorded fixtures through a fake transport, so the
  whole client is provable with no key and no credits — the same discipline that lets the mock
  server prove the app with no Python running.

## What a draft world actually is

Measured on two of them, one from text and one from a photograph. They were identical on every
count below, which suggests a fixed budget rather than a coincidence:

| | |
|---|---|
| splats | **2,276,736** (the playroom capture has 1,495,461) |
| SH degree | **0** — no `f_rest_*` at all, so base colour only, no view-dependent shading |
| properties | **14**, and no normals: 56 bytes a splat rather than 248 |
| file size | **127.5 MB** |
| generation | **21 and 26 seconds**, not the five minutes the documentation quotes |

The absent spherical harmonics are the thing to know before looking at one: a Marble draft
capture renders flat. That is the export, not the renderer.

## Status

**M0-M5 done: 125 tests, ruff clean, and two real captures on disk.**

    capture/out/20260905-225447-kitchen.ply    a kitchen, from a text prompt
    capture/out/20260905-232058-desk.ply       a desk, from a photograph

Both 127.5 MB, 2,276,736 splats, SH degree 0; 230 credits and under half a minute each. The
repo's own `python -m app.splat` reads them as a subprocess and agrees exactly on count and SH
degree.

**The live API disagreed with its own documentation in five places**, every one silent rather
than loud: `POST /worlds:list` rather than `GET /worlds`; a bare world keyed `world_id` rather
than wrapped with `id`; `media_asset_id` rather than `id` on an upload target;
`semantics_metadata` nested under `assets.splats` and null on draft worlds; and a 100 MB upload
limit rather than 1 GB. Plus one bug of this track's own — a sidecar named `x.marble.ply`. All
fixed and pinned by tests. `NOTES.md` has the detail.

**What Track A should take from this** is set out under *What actually crosses between the
tracks* above: the metric-scale cross-check does not exist, and the axis fact does.

    cd capture
    .venv/Scripts/python -m pytest tests/ -q
    .venv/Scripts/python -m marble --help
    .venv/Scripts/python -m marble generate --text "a utility room" --dry-run

**Exercised for real:** two worlds generated, one from text and one from a photograph, so the
upload path and the generate-poll-export-download-verify spine have both run end to end.

**Still unexercised:** panorama input and `is_pano` detection, multi-image, and every model
above draft — including whether a standard-model world reports `semantics_metadata`. See
`PLAN.md`'s Open section.
