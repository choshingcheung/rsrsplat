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
UI, and nothing imports it. You run it, wait about five minutes, and get a file you can drag
into the viewport.

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

The sidecar is the interesting half. Alongside the world id, prompt, model and splat count, it
carries Marble's own `semantics_metadata`:

| field | meaning |
|---|---|
| `metric_scale_factor` | multiply coordinates by this to get **metres** |
| `ground_plane_offset` | subtract from the vertical axis to put the **floor at zero** |

Which is, to be precise about it, an independent answer to exactly what
`web/src/scene/ground.ts` derives by sequential RANSAC and layer scoring. `NOTES.md` opens
with "the capture is not metric"; a Marble world states its own scale and floor height as
facts.

**This is offered as a cross-check, not as a replacement.** No code here touches the ground
fitting, and none is proposed. Once there is a real Marble capture, the fitted `sceneScale` and
`groundHeight` can be compared against two numbers that came from somewhere else entirely —
worth having, since a wrong ground plane looks completely plausible in a render.

---

## The API, in brief

Base `https://api.worldlabs.ai/marble/v1/`, authenticated with a `WLT-Api-Key` header.
Generation is asynchronous and takes about five minutes.

| # | Call |
|---|---|
| 1 | `POST /media-assets:prepare_upload` → signed `upload_url` + required headers |
| 2 | `PUT` the image to that URL |
| 3 | `POST /worlds:generate` → `operation_id` |
| 4 | `GET /operations/{id}` until `done` (`metadata.world_id` appears early) |
| 5 | `POST /worlds/{world_id}:export` with `{asset_type: "splats", format: "ply"}` |
| 6 | download the signed URL from `response.url` |

Two facts worth knowing: **rate limits apply to generation starts, not to polling** (3/min on
the default tier), and **PLY export costs nothing** beyond the generation itself.

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

- **Python, synchronous `httpx`.** A batch job that waits five minutes gains nothing from async
  and loses testability. Two dependencies: `httpx` and `pytest`.
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

## Two things not yet known

Both get recorded as measured facts in `capture/NOTES.md` at the first real run, rather than
assumed now:

1. **The exported PLY's axis convention.** The docs put Marble's ground plane at `y = 0`,
   implying y-up; `CLAUDE.md` says Marble is OpenCV, `+y` down. Those want reconciling against
   an actual file, not against each other.
2. **The real splat count of a draft world**, which decides whether a draft capture is good
   enough to develop selection against, or only good enough to prove the pipeline.

## Status

**M0-M5 done: 123 tests, ruff clean, and a real capture on disk.**

    capture/out/20260905-225447-kitchen.ply    127.5 MB, 2,276,736 splats, SH degree 0

A kitchen with a dishwasher and a wall oven, generated from a text prompt on
`marble-1.0-draft` for 230 credits in 21 seconds. The repo's own `python -m app.splat` reads it
as a subprocess and agrees exactly on count and SH degree.

**The live API disagreed with its own documentation in four places**, every one of them silent
rather than loud — `POST /worlds:list` rather than `GET /worlds`, a bare world keyed by
`world_id`, `semantics_metadata` nested under `assets.splats` and null on draft worlds, and a
sidecar filename bug. All four are fixed and pinned by tests. `NOTES.md` has the detail.

**The one that matters to Track A:** Marble reported **no** `metric_scale_factor` and **no**
`ground_plane_offset` for this world, so the sidecar cannot supply a metric scale and the
RANSAC ground fitting in `web/src/scene/ground.ts` is **not** redundant. What this track can
offer instead is a measured fact: in the exported PLY the vertical axis is **Y with +Y up and
the floor at minimum y**, which contradicts `CLAUDE.md`'s "Marble is OpenCV, +y down".
`opencv_to_zup` is the transform that lands it Z-up the right way up.

    cd capture
    .venv/Scripts/python -m pytest tests/ -q
    .venv/Scripts/python -m marble --help
    .venv/Scripts/python -m marble generate --text "a utility room" --dry-run

**Still unexercised:** the image upload path has never run against the live API, and whether a
standard-model world reports `semantics_metadata` is unknown. See `PLAN.md`'s Open section.
