# Fixtures

One golden response per API call, replayed through `httpx.MockTransport`. They are what lets
the whole client be proven with no key, no credits and no network — the same discipline that
lets `web/`'s mock server prove the app with no Python process running.

## Provenance, stated plainly

These began as hand transcriptions of the published API documentation. **Some have since been
corrected against the live API, and some have not.** The difference matters: a fixture from
documentation proves only that the client is self-consistent, while a recorded one proves it
agrees with the server.

**Corrected from observed responses** (M5, 2026-09-05):

| file | what the documentation got wrong |
|---|---|
| `worlds_list.json` | the call is `POST /worlds:list`; `GET /worlds` is a 404 |
| `world.json` | returned bare, not wrapped; keyed `world_id`, not `id`; `semantics_metadata` sits under `assets.splats` |
| `world_without_semantics.json` | a real draft world: `semantics_metadata` is `null` |
| `credits.json` | confirmed correct as documented |
| `prepare_upload.json` | the asset id is `media_asset_id`, not `id`; the upload limit is 100 MB, not 1 GB; there is an undocumented `curl_example` field |

`prepare_upload.json` holds a real signed URL with **the signature replaced by a placeholder**.
A signed URL is a capability; it does not belong in a committed fixture, and it would expire
within the hour anyway.

**Still only transcribed, never observed:**

`generate.json`, `operation_running.json`, `operation_done.json`, `operation_failed.json`,
`export_pending.json`, `export_done.json`, and every `error_*.json`.

The error bodies are the exposure worth naming. Every one of them is invented, so the client's
handling of 400, 402, 422 and 429 is coherent rather than confirmed -- and 402 in particular
matters, because it is what a run hits when the credits run out mid-session. The operation
shapes are better supported: two real generations polled to completion through them, so their
happy path is observed even though the fixture text was written by hand.

When any of these is seen for real, capture it verbatim, write it over the file, and record the
difference in `capture/NOTES.md`.

## The files

| file | call |
|---|---|
| `credits.json` | `GET /credits` |
| `prepare_upload.json` | `POST /media-assets:prepare_upload` |
| `generate.json` | `POST /worlds:generate` — the operation, before any progress |
| `operation_running.json` | `GET /operations/{id}` mid-flight, carrying `metadata.world_id` |
| `operation_done.json` | the same, finished, with the World in `response` |
| `operation_failed.json` | a content policy refusal — a real outcome, not a server fault |
| `world.json` | `GET /worlds/{id}` — bare, keyed `world_id`, semantics under `assets.splats` |
| `world_without_semantics.json` | the same, as a real draft world returns it: semantics `null` |
| `worlds_list.json` | `POST /worlds:list` — envelope `worlds`, plus `next_page_token` |
| `export_pending.json` | `POST /worlds/{id}:export` when the conversion is not cached |
| `export_done.json` | the cached case, with the signed URL in `response.url` |
| `error_400/402/422/429.json` | the error bodies, each of which needs its own handling |

The numbers in `world.json` — `metric_scale_factor` and `ground_plane_offset` — are invented
placeholders, and deliberately kept that way: no real world has yet reported either, so this
fixture exists to prove the client *would* read them from the right place if one did.
`world_without_semantics.json` is the case that actually occurs.
