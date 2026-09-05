"""Reading a 3DGS PLY, and the four conventions that make it mean something.

Ported from ``akitech/splat`` ``src/splat_io.py``, where all four were checked against
MuJoCo-Warp's own source rather than assumed. Every one of them is silent when it goes
wrong: log scales left unexponentiated give negative sizes and a black frame, opacity left
pre-sigmoid gives values in the tens, the wrong quaternion order gives noise.

===================================  ==========================  =========================
stored in the PLY                    what it means               what to do
===================================  ==========================  =========================
``scale_*``, in **log space**        standard deviation per axis ``exp()``, nothing more
``opacity``, **pre-sigmoid**         alpha in [0, 1]             ``sigmoid()``
``f_dc_*``, band-0 harmonics         linear RGB                  ``0.5 + C0 * f_dc``
``rot_*`` as (w, x, y, z)            (w, x, y, z)                normalise; order matches
===================================  ==========================  =========================

``nx, ny, nz`` are written by every trainer and are zeros. Ignored.

**No coordinate transform happens here.** Marble writes OpenCV convention and MuJoCo is
z-up, so a capture needs one explicit conversion at load. Which one is an empirical question
about a given capture, so ``transforms.py`` holds the candidates and the caller picks and
records one. A loader that guesses silently is how a scene ends up subtly inside out.

The physics service does not strictly need any of this — the browser owns the Gaussians and
the backend never sees them. It exists so the browser's parser has a reference implementation
to be checked against, and so a capture can be inspected headlessly before anyone trusts it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import quat

#: Band-0 spherical harmonic coefficient. The DC term is the view-independent base colour.
SH_C0 = 0.28209479177387814

#: How many ``f_rest_*`` properties each spherical-harmonic degree implies.
SH_DEGREE_BY_REST = {0: 0, 9: 1, 24: 2, 45: 3}


@dataclass
class Splats:
    """A splat cloud, as typed arrays."""

    position: np.ndarray  # (n, 3) float32, scene coordinates
    rotation: np.ndarray  # (n, 4) float32, unit quaternions, (w, x, y, z)
    scale: np.ndarray  # (n, 3) float32, standard deviation per axis
    rgba: np.ndarray  # (n, 4) float32, linear colour and alpha
    sh_degree: int | None = None

    def __len__(self) -> int:
        return int(self.position.shape[0])

    def __getitem__(self, idx) -> Splats:
        return Splats(
            self.position[idx],
            self.rotation[idx],
            self.scale[idx],
            self.rgba[idx],
            self.sh_degree,
        )

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.position.min(0), self.position.max(0)

    def extent(self) -> np.ndarray:
        lo, hi = self.bounds()
        return hi - lo

    def report(self) -> str:
        """The numbers to eyeball before trusting a capture.

        An extent whose longest axis is not a plausible number of metres for a room means
        the capture is not metric, and everything downstream that assumes metres is wrong.
        """
        lo, hi = self.bounds()
        return "\n".join(
            [
                f"splats      {len(self):,}",
                f"sh degree   {self.sh_degree if self.sh_degree is not None else 'unknown'}",
                f"bbox        {np.array2string(lo, precision=3)} .. "
                f"{np.array2string(hi, precision=3)}",
                f"extent      {np.array2string(self.extent(), precision=3)} "
                f"(longest axis {self.extent().max():.3f})",
                f"scale       {self.scale.min():.5f} .. {self.scale.max():.5f}",
                f"opacity     {self.rgba[:, 3].min():.3f} .. {self.rgba[:, 3].max():.3f}",
                f"colour      {self.rgba[:, :3].min():.3f} .. {self.rgba[:, :3].max():.3f}",
            ]
        )


def sigmoid(x: np.ndarray) -> np.ndarray:
    """Stable in both directions, because real captures reach past +-30 in the logits."""
    out = np.empty_like(x, dtype=np.float32)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    e = np.exp(x[~pos])
    out[~pos] = e / (1.0 + e)
    return out


# ---------------------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------------------


def header_lines(path: str | Path) -> list[str]:
    """The PLY's ASCII header, read without touching the body.

    The prototype answered ``sh_degree`` by re-reading the entire file through plyfile,
    which doubled the cost of inspecting a 370 MB capture. The header is the first few
    hundred bytes; read those.
    """
    lines: list[str] = []
    with open(path, "rb") as fh:
        while True:
            raw = fh.readline()
            if not raw:
                raise ValueError(f"{path}: reached end of file with no 'end_header' - not a PLY?")
            line = raw.decode("ascii", errors="replace").strip()
            lines.append(line)
            if line == "end_header":
                return lines


def property_names(path: str | Path) -> list[str]:
    return [line.split()[-1] for line in header_lines(path) if line.startswith("property ")]


def splat_count(path: str | Path) -> int:
    """How many Gaussians, from the header alone.

    Cheap enough to call on a 370 MB file, which is what makes it usable as the cross-check
    against the browser's own parse.
    """
    for line in header_lines(path):
        if line.startswith("element vertex "):
            return int(line.split()[-1])
    raise ValueError(f"{path}: header declares no 'element vertex'")


def sh_degree(path: str | Path) -> int | None:
    """How many spherical-harmonic bands the file carries, from the ``f_rest_*`` count.

    Reporting only: rsrsplat renders from the DC term. ``None`` means the count matched no
    known degree, which is worth surfacing rather than rounding to the nearest.
    """
    rest = sum(1 for name in property_names(path) if name.startswith("f_rest_"))
    return SH_DEGREE_BY_REST.get(rest)


# ---------------------------------------------------------------------------------------
# Body
# ---------------------------------------------------------------------------------------


def load(path: str | Path) -> Splats:
    """Parse a 3DGS PLY into typed arrays. No coordinate transform is applied."""
    from plyfile import PlyData

    vertex = PlyData.read(str(path))["vertex"]

    position = np.stack([vertex["x"], vertex["y"], vertex["z"]], 1).astype(np.float32)
    # Log space. Left raw, a stored scale of 0 would mean a zero-size splat.
    scale = np.exp(np.stack([vertex[f"scale_{i}"] for i in range(3)], 1)).astype(np.float32)
    # Pre-sigmoid. Left raw, alpha comes out in the tens.
    alpha = sigmoid(np.asarray(vertex["opacity"], dtype=np.float32))
    # Band-0 harmonics, so an f_dc of zero is mid grey rather than black.
    rgb = 0.5 + SH_C0 * np.stack([vertex[f"f_dc_{i}"] for i in range(3)], 1).astype(np.float32)

    rotation = quat.normalize(
        np.stack([vertex[f"rot_{i}"] for i in range(4)], 1).astype(np.float32)
    )

    return Splats(
        position=position,
        rotation=rotation,
        scale=scale,
        rgba=np.concatenate([np.clip(rgb, 0.0, 1.0), alpha[:, None]], 1).astype(np.float32),
        sh_degree=sh_degree(path),
    )


def crop(splats: Splats, lo, hi) -> np.ndarray:
    """Indices inside an axis-aligned box."""
    lo = np.asarray(lo, dtype=np.float32)
    hi = np.asarray(hi, dtype=np.float32)
    return np.where(np.all((splats.position >= lo) & (splats.position <= hi), 1))[0]


def synth_box(
    n: int = 2000,
    *,
    centre=(0.0, 0.0, 0.0),
    size=(0.1, 0.1, 0.1),
    seed: int = 0,
) -> Splats:
    """A synthetic cloud filling a box, for testing against known-good data.

    Do not debug a parser against a file that might itself be malformed. This goes further:
    a cloud whose every property we chose, so a wrong answer is unambiguous rather than
    merely suspicious. It is also the basis of the selection test — half-extents recovered
    by PCA can be checked against the numbers that generated the cloud.
    """
    rng = np.random.default_rng(seed)
    centre = np.asarray(centre, dtype=np.float32)
    size = np.asarray(size, dtype=np.float32)
    return Splats(
        position=(centre + (rng.random((n, 3), dtype=np.float32) - 0.5) * size).astype(np.float32),
        rotation=quat.normalize(rng.normal(size=(n, 4)).astype(np.float32)),
        scale=np.full((n, 3), 0.004, dtype=np.float32),
        rgba=np.concatenate(
            [rng.random((n, 3), dtype=np.float32), np.full((n, 1), 0.9, dtype=np.float32)], 1
        ),
    )
