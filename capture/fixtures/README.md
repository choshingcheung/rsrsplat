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

**Still only transcribed, never observed:**

`prepare_upload.json`, `generate.json`, `operation_running.json`, `operation_done.json`,
`operation_failed.json`, `export_pending.json`, `export_done.json`, and every `error_*.json`.

The upload pair is the exposure worth naming: `prepare_upload.json` drives the whole signed-PUT
path, which has never run against the live API. A green suite says that path is coherent, not
that it works. When an image run happens, capture the responses verbatim, write them over these
files, and record any difference in `capture/NOTES.md`.

## The files

| file | call |
|---|---|
| `credits.json` | `GET /credits` |
| `prepare_upload.json` | `POST /media-assets:prepare_upload` |
| `generate.json` | `POST /worlds:generate` — the operation, before any progress |
| `operation_running.json` | `GET /operations/{id}` mid-flight, carrying `metadata.world_id` |
| `operation_done.json` | the same, finished, with the World in `response` |
| `operation_failed.json` | a content policy refusal — a real outcome, not a server fault |
| `world.json` | `GET /worlds/{id}`, including `semantics_metadata` |
| `export_pending.json` | `POST /worlds/{id}:export` when the conversion is not cached |
| `export_done.json` | the cached case, with the signed URL in `response.url` |
| `error_400/402/422/429.json` | the error bodies, each of which needs its own handling |

The numbers in `world.json` — `metric_scale_factor` and `ground_plane_offset` — are invented
placeholders. Their **shape** is what the tests assert on. Their values will only mean
something once a real world has been generated.
