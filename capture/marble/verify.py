"""Is this file actually a 3D Gaussian splat cloud?

A downloaded PLY is trusted by nobody until it has been read. Three failures are worth
catching here, at the point of download, rather than in a browser twenty minutes later:

1. **It is not a PLY at all.** An expired signed URL returns XML; a proxy returns HTML. Both
   land as a 200 with a body, and both write a perfectly good file.
2. **It is truncated.** A short file still opens. ``client.download`` already checks the byte
   count against ``Content-Length``, but that only helps when the server sent one -- the
   header's own vertex count and property list give an exact expected size that does not
   depend on the server being helpful.
3. **It is a PLY, but not a splat.** A mesh export saved under the wrong name parses fine and
   has none of the fields a splat renderer needs.

Deliberately header-only and dependency-free. Reading 1.5 million Gaussians to answer "is this
the right kind of file" would take a second and several hundred megabytes of memory, and the
answer is in the first kilobyte. The full parse belongs to whoever renders it.

**Two conventions this repo already records, which the numbers here reflect:** splat scales
are log-space and opacity is pre-sigmoid. Neither is applied -- nothing is decoded here -- but
they are why a raw ``scale_0`` of -5 is normal and not a broken file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: How far in to look for the end of the header before giving up. A 3DGS header with SH
#: degree 3 is about 2 KB; anything past this is not a header.
MAX_HEADER = 64 * 1024

#: Bytes per PLY scalar type, both the short and long spellings.
TYPE_SIZES = {
    "char": 1, "uchar": 1, "int8": 1, "uint8": 1,
    "short": 2, "ushort": 2, "int16": 2, "uint16": 2,
    "int": 4, "uint": 4, "int32": 4, "uint32": 4,
    "float": 4, "float32": 4,
    "double": 8, "float64": 8,
}  # fmt: skip

#: What a Gaussian must carry: a position, a colour, an opacity, a scale and an orientation.
REQUIRED = (
    "x", "y", "z",
    "f_dc_0", "f_dc_1", "f_dc_2",
    "opacity",
    "scale_0", "scale_1", "scale_2",
    "rot_0", "rot_1", "rot_2", "rot_3",
)  # fmt: skip

#: Number of ``f_rest_*`` properties -> spherical harmonic degree. Three coefficients per
#: band per colour channel: degree 1 adds 3, degree 2 adds 5, degree 3 adds 7.
SH_DEGREE_BY_REST = {0: 0, 9: 1, 24: 2, 45: 3}


class VerifyError(ValueError):
    """The file is not what it claims to be. The message is meant to be shown to a person."""


class NotAPly(VerifyError):
    pass


class NotGaussianSplats(VerifyError):
    pass


class Truncated(VerifyError):
    pass


@dataclass(frozen=True)
class PlyInfo:
    """What the header says, plus whether the body is the size it implies."""

    count: int
    sh_degree: int
    properties: tuple[str, ...]
    header_bytes: int
    vertex_bytes: int
    file_bytes: int
    format: str

    @property
    def expected_bytes(self) -> int:
        return self.header_bytes + self.count * self.vertex_bytes

    def summary(self) -> str:
        return (
            f"{self.count:,} splats, SH degree {self.sh_degree}, "
            f"{len(self.properties)} properties, {self.file_bytes / 1e6:.1f} MB"
        )


def parse_header(data: bytes) -> tuple[int, tuple[str, ...], int, str, int]:
    """Read a PLY header.

    Returns (vertex count, property names, header size, format, bytes per vertex).

    Only the ``vertex`` element's properties are collected. A 3DGS file has exactly one
    element, but reading it positionally would break on any file that does not.
    """
    end = data.find(b"end_header")
    if not data.startswith(b"ply") or end < 0:
        head = data[:40].decode("ascii", "replace").strip()
        raise NotAPly(
            f"not a PLY file: it begins {head!r}. An expired signed URL usually returns "
            f"XML or HTML here, which downloads perfectly happily."
        )

    # Past the token to the end of that line, so the body offset is exact under either
    # line ending. CRLF in a binary PLY is unusual but not illegal.
    line_end = data.find(b"\n", end)
    header_bytes = (line_end + 1) if line_end >= 0 else (end + len("end_header"))
    text = data[:end].decode("ascii", "replace")

    count = 0
    fmt = ""
    properties: list[str] = []
    sizes: list[int] = []
    element = ""

    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        keyword = parts[0]

        if keyword == "format" and len(parts) >= 2:
            fmt = parts[1]
        elif keyword == "element" and len(parts) >= 3:
            element = parts[1]
            if element == "vertex":
                count = int(parts[2])
        elif keyword == "property" and element == "vertex" and len(parts) >= 3:
            if parts[1] == "list":
                # A list property in a vertex element makes the stride variable, so the
                # size check below cannot apply. Not something 3DGS produces.
                raise NotGaussianSplats("this PLY has list properties; it is not a splat cloud")
            properties.append(parts[-1])
            sizes.append(TYPE_SIZES.get(parts[1], 4))

    return count, tuple(properties), header_bytes, fmt, sum(sizes)


def sh_degree(properties: tuple[str, ...]) -> int:
    """Spherical harmonic degree, from how many ``f_rest_*`` there are."""
    rest = sum(1 for name in properties if name.startswith("f_rest_"))
    if rest not in SH_DEGREE_BY_REST:
        raise NotGaussianSplats(
            f"{rest} f_rest_ properties is not a whole SH degree "
            f"(expected one of {sorted(SH_DEGREE_BY_REST)})"
        )
    return SH_DEGREE_BY_REST[rest]


def inspect(path: Path) -> PlyInfo:
    """Read the header and measure the file. Raises rather than returning a bad answer."""
    file_bytes = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(MAX_HEADER)

    count, properties, header_bytes, fmt, vertex_bytes = parse_header(head)

    if fmt != "binary_little_endian":
        raise NotGaussianSplats(
            f"PLY format is {fmt!r}; this project requires binary_little_endian. "
            f"An ASCII PLY of 1.5M Gaussians is both enormous and slow to parse."
        )

    missing = [name for name in REQUIRED if name not in properties]
    if missing:
        raise NotGaussianSplats(
            f"not a Gaussian splat PLY: missing {', '.join(missing[:6])}"
            f"{' and more' if len(missing) > 6 else ''}. A mesh export has vertices too."
        )

    if count <= 0:
        raise NotGaussianSplats("the header declares no vertices")

    return PlyInfo(
        count=count,
        sh_degree=sh_degree(properties),
        properties=properties,
        header_bytes=header_bytes,
        vertex_bytes=vertex_bytes,
        file_bytes=file_bytes,
        format=fmt,
    )


def verify(path: Path, expect_count: int | None = None) -> PlyInfo:
    """Inspect, and insist the body is the size the header implies.

    This is the check that catches a download which ended early without the server ever
    saying how long it should have been.
    """
    info = inspect(path)

    if info.file_bytes < info.expected_bytes:
        short = info.expected_bytes - info.file_bytes
        raise Truncated(
            f"{path.name} is {short:,} bytes short: the header declares {info.count:,} "
            f"vertices of {info.vertex_bytes} bytes, needing {info.expected_bytes:,} in all, "
            f"but the file is {info.file_bytes:,}. The download did not finish."
        )

    if expect_count is not None and expect_count != info.count:
        raise VerifyError(
            f"{path.name} holds {info.count:,} splats, but {expect_count:,} were expected."
        )

    return info


__all__ = [
    "MAX_HEADER",
    "REQUIRED",
    "SH_DEGREE_BY_REST",
    "TYPE_SIZES",
    "NotAPly",
    "NotGaussianSplats",
    "PlyInfo",
    "Truncated",
    "VerifyError",
    "inspect",
    "parse_header",
    "sh_degree",
    "verify",
]
