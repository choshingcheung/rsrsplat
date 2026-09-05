# Notes

Facts measured on this machine, not assumed. Newest at the bottom.

---

## The playroom capture, measured — 2026-09-05

`captures/playroom_7000.ply`, the only capture on hand, via `python -m app.splat`:

```
splats      1,495,461
sh degree   3
bbox        [-14.976 -24.465 -23.855] .. [12.537  9.379 10.401]
extent      [27.513 33.844 34.256]   (longest axis 34.256)
scale       0.00001 .. 0.91391
opacity     0.004 .. 1.000
```

**The capture is not metric.** No playroom is 34 units across if those units are metres. This
is not a parser fault — it is the expected state of a trained 3DGS scene, which arrives in
whatever frame and scale its reconstruction happened to pick. It is exactly why the contract
carries `WorldFrame.sceneScale`, and why the browser has to fit the ground rather than assume it.

**Floaters inflate the bounding box by about three times.**

| measure | extent |
|---|---|
| raw min/max | 27.51 × 33.84 × 34.26 |
| p1–p99 | 11.50 × 10.96 × 11.41 |
| p2–p98 | 11.18 × 10.69 × 10.90 |
| p5–p95 | 9.61 × 9.06 × 10.14 |

94.2% of splats sit inside the p1–p99 box. So the bulk of the scene is a roughly cubic
11-unit region and the remaining 5.8% — stray Gaussians scattered far outside the room — are
what stretch the raw extent to 34. **Never scale a scene by its raw longest axis.** Use a
percentile. This is the same lesson the prototype's `ground.py` recorded about floor height,
arriving from a different direction.

**Most of the cloud is nearly invisible.**

| alpha threshold | share of splats |
|---|---|
| ≥ 0.02 | 88.5% |
| ≥ 0.10 | 42.7% |
| ≥ 0.30 | 9.6% |

Fewer than half the Gaussians carry an alpha of 0.1 or more. That is a large lever for browser
frame rate, and it also matters for selection: a box-select that counts faint splats will report
a Gaussian count far larger than what the user can actually see inside their box. Worth an
opacity floor in the selection filter, not only in the renderer.

**Reading the header is free.** 0.2 s to report count and SH degree from a 370 MB file, against
0.7 s for a full parse. The header path is what makes the count usable as a cross-check against
the browser's own parse.
