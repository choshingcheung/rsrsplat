# capture — working plan

One track, sequential, built entirely offline until the last two steps. Each step ends green
and is provable on its own; nothing is "done" because it looks done. Same legend as the root
plan, deliberately.

Legend: `[ ]` not started · `[~]` in progress · `[x]` passing

See `README.md` in this directory for what is being built, and for the isolation contract that
keeps this out of Tracks S and A. **Nothing in this plan edits a file outside `capture/`.**

---

## M0 · The shell  [x]

- [x] `capture/` with its own `pyproject.toml`, its own `.venv`, `capture/.gitignore`, and the
      two dependencies: `httpx` and `pytest`.

*Proof:* `pytest` and `ruff` both run clean on an empty package, and `git status` shows changes
under `capture/` and nowhere else.

## M1 · The client, entirely offline  [x]

- [x] All six calls in `marble/client.py`: prepare upload, upload, generate, poll, export,
      download. Typed, with the units and shapes of the API written at each one.
- [x] Recorded fixtures in `capture/fixtures/`, replayed through a fake `httpx` transport.

*Proof:* the happy path runs end to end with no network and no key. Each of 400, 402, 404, 422,
429 and 500 produces its own readable error rather than a stack trace — 402 in particular must
say "out of credits" and not "request failed". `Retry-After` is honoured, and backoff is proven
against a fake clock rather than by sleeping.

*The one that will bite:* the upload is a `PUT` to a signed URL with required headers that come
back from the prepare call. Sending it with the API key attached, or without those headers, is
a 403 that reads like an auth problem and is not one.

## M2 · The ledger  [x]

- [x] Every run writes `capture/runs/<stamp>-<slug>.json` — operation id, world id, model,
      prompt, estimated cost, status — **before** the generate call is made.
- [x] `marble resume <run>` picks up polling. `marble status` lists what is outstanding.

*Proof:* killed mid-poll, a resume completes the same run. A crash between the generate call
and the first poll still leaves a recoverable operation id on disk.

*Why it is this early:* a generation is someone else's compute, already charged the moment it
is accepted. Losing one to a dropped process is the only irreversible failure in this tool --
and it turns out to take about 25 seconds, so the window is small and easy to underestimate.

## M3 · The CLI and the money guard  [x]

- [x] `marble generate --image room.jpg --text "..."`, and `--text` alone.
- [x] `--dry-run` prices the run and exits. Balance is checked before anything is spent, and
      the run refuses to start if it will not cover the estimate.
- [x] Draft is the default model; anything dearer needs an explicit flag.

*Proof:* a dry run costs nothing and prints the same estimate the real run records. An
insufficient balance fails before any charge. The key never reaches stdout, a log or an error
message — length and prefix only, which is `PORTING.md` hazard 5 observed rather than
rediscovered.

## M4 · Download, verify, sidecar  [x]

- [x] Streamed download with progress, into `SPLAT_CAPTURE_DIR` when it is set.
- [x] `<name>.marble.json` carrying world id, prompt, model, splat count and the whole of
      `semantics_metadata`.
- [x] `marble/verify.py`: the PLY header read in pure stdlib — binary little-endian, vertex
      count, the 3DGS properties present, SH degree from the `f_rest_*` count.

*Proof:* a truncated download is rejected rather than reported as a success — the byte length is
checked against `Content-Length`, because a half-written 200 MB file is still a file and still
opens. A valid header passes. The sidecar's splat count matches the header's.

## M5 · One real run  [x]

- [x] A single live `marble-1.0-draft` generation, from text. 230 credits, 21 seconds.
- [x] And a second from a real photograph, which is what found the upload bug. 230 credits,
      26 seconds.
- [x] The result read by the repo's own `python -m app.splat`, **as a subprocess**: both
      parsers agree exactly on 2,276,736 splats and SH degree 0.

*Proof:* a real `.ply` on disk that rsrsplat's own reference reader parses — the same
cross-check `NOTES.md` ran against the playroom capture, from the other direction.

*Recorded in `capture/NOTES.md`, measured rather than assumed:* the axis convention the file
actually uses, its real splat count, what `metric_scale_factor` and `ground_plane_offset` come
back as, and how long the whole round trip took.

## M6 · Handoff

- [ ] `capture/README.md` documents dragging the result into the viewport.
- [ ] The sidecar's scale and ground height reported next to whatever `ground.ts` derived, as
      a cross-check for Track A.

*Proof:* a capture generated here loads in the app with no change to any file under `web/` or
`service/`. If it does not, that is a finding to hand over — not a licence to edit their code.

---

## Cut line

If this has to be cut short, the order of sacrifice:

1. Cut M6. A file on disk is already the whole interface.
2. Cut text-only prompts; keep image.
3. **Never cut M2.** Everything else here can be re-run for pennies. A lost operation id is a
   generation that was paid for and cannot be recovered.

## Open

- Whether a standard-model world reports `semantics_metadata`. ~$1.26 to find out, and it
  decides whether the sidecar can ever carry a metric scale. See `NOTES.md`.
- A **panorama** input, and whether `is_pano` detection works. A pano costs 0 credits to
  convert, so it is the cheapest untested path left.
- **Multi-image**, which needs azimuths. Photographs from a narrow arc cannot supply honest
  ones, so this wants a deliberate set shot around a scene.
