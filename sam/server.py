"""
SAM 2 as a local sidecar, so a drag rectangle can become an object mask.

Why this exists
---------------

Selection was a screen-space rectangle plus a depth filter, and that takes a slab of the
frustum: the vest, the carpet around it, and whatever is behind. Measured on the real
capture, growing that geometrically returned 801% of what was selected, and no threshold
separates a legitimate recovery from a runaway -- a room scan is one connected mass, so
"connected to" means "in the same room as". Geometry cannot say where an object ends.

A segmentation model can, because it has an image prior. The browser already projects every
splat to the screen, so a MASK is a drop-in replacement for the rectangle test: a splat is
the object if it lands inside the silhouette and at the right depth.

Why a sidecar and not the service
---------------------------------

The simulation service is FastAPI + MuJoCo + numpy and starts instantly. Putting torch in it
would make every test run and every reload pay for CUDA. This is a separate process with its
own interpreter, and the app degrades to the plain rectangle when it is not running -- which
is also what happens if it falls over mid-demo.

Note that no splat data crosses this boundary. What goes over is a rendered PNG of the
viewport, which is a picture, not Gaussians.

Running it
----------

Any interpreter with torch + transformers + fastapi. On this machine::

    "c:/vscode workspace/akitech/app/sidecar/.venv-win/Scripts/python.exe" sam/server.py

Loads in about three seconds on a 4060 and answers in about 90 ms.
"""

from __future__ import annotations

import io
import os
import time

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image

MODEL_ID = os.environ.get("RSRSPLAT_SAM_MODEL", "facebook/sam2.1-hiera-tiny")
PORT = int(os.environ.get("RSRSPLAT_SAM_PORT", "8008"))

app = FastAPI(title="rsrsplat SAM sidecar")

# The browser talks to this directly rather than through the simulation service: one fewer
# hop for an image that the service has no use for.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_model = None
_processor = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def _load():
    """Load once, on first use, so the port is listening before CUDA warms up."""
    global _model, _processor
    if _model is None:
        from transformers import Sam2Model, Sam2Processor

        started = time.time()
        _processor = Sam2Processor.from_pretrained(MODEL_ID)
        _model = Sam2Model.from_pretrained(MODEL_ID).to(_device).eval()
        print(f"[sam] {MODEL_ID} on {_device} in {time.time() - started:.1f}s", flush=True)
    return _model, _processor


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_ID, "device": _device, "loaded": _model is not None}


@app.post("/segment")
async def segment(request: Request, x0: float, y0: float, x1: float, y1: float) -> Response:
    """A viewport PNG as the raw body and a box in PIXELS as the query; back comes a mask.

    The box is a PROMPT, not a crop. SAM is being asked "what object is in here", and its
    answer routinely reaches outside the rectangle -- which is the entire point, because the
    part of the object the user's drag clipped is exactly what a rectangle cannot express.

    Raw body rather than multipart so this runs on any interpreter that already has torch,
    without adding python-multipart to an environment that belongs to another project.
    """
    model, processor = _load()

    pil = Image.open(io.BytesIO(await request.body())).convert("RGB")
    box = [[[float(x0), float(y0), float(x1), float(y1)]]]

    started = time.time()
    inputs = processor(images=pil, input_boxes=box, return_tensors="pt").to(_device)
    with torch.inference_mode():
        out = model(**inputs, multimask_output=False)
    masks = processor.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"])

    mask = (np.asarray(masks[0][0][0]) > 0).astype(np.uint8) * 255
    buffer = io.BytesIO()
    Image.fromarray(mask, mode="L").save(buffer, format="PNG", optimize=False)

    print(
        f"[sam] {pil.width}x{pil.height} box=({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}) "
        f"-> {int(mask.sum() // 255)} px in {time.time() - started:.2f}s",
        flush=True,
    )
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={"X-Mask-Pixels": str(int(mask.sum() // 255))},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
