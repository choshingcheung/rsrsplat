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
#: Open-vocabulary detector, so the sentence the user types can steer the mask.
GROUND_ID = os.environ.get("RSRSPLAT_GROUND_MODEL", "google/owlv2-base-patch16-ensemble")
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
_ground = None
_ground_processor = None
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


def _load_ground():
    """The text-to-box model, loaded only if a prompt is ever sent."""
    global _ground, _ground_processor
    if _ground is None:
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        started = time.time()
        _ground_processor = Owlv2Processor.from_pretrained(GROUND_ID)
        _ground = Owlv2ForObjectDetection.from_pretrained(GROUND_ID).to(_device).eval()
        print(f"[sam] {GROUND_ID} on {_device} in {time.time() - started:.1f}s", flush=True)
    return _ground, _ground_processor


def _iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / (area_a + area_b - inter)


def _ground_box(pil, text: str, drag, threshold: float = 0.06):
    """Tighten the user's drag box using what they called the thing.

    The drag box and the sentence answer different halves of the question and both are
    needed. The sentence says WHAT ("a vest") but not which one, and a room may hold several;
    the drag says WHICH but not where the object ends, because a rectangle drawn by hand is
    loose and clips whatever hangs outside it.

    So detections are scored by overlap with the drag rather than by confidence alone. The
    most confident vest in the room is not necessarily the vest being pointed at.
    """
    model, processor = _load_ground()
    inputs = processor(text=[[text]], images=pil, return_tensors="pt").to(_device)
    with torch.inference_mode():
        out = model(**inputs)
    sizes = torch.tensor([[pil.height, pil.width]])
    found = processor.post_process_grounded_object_detection(
        out, threshold=threshold, target_sizes=sizes
    )[0]

    best, best_score = None, 0.0
    for box, score in zip(found["boxes"].tolist(), found["scores"].tolist()):
        overlap = _iou(box, drag)
        if overlap < 0.1:
            continue  # a different instance of the same noun, somewhere else in the room
        combined = overlap * float(score)
        if combined > best_score:
            best, best_score = box, combined
    return best, best_score


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_ID, "device": _device, "loaded": _model is not None}


@app.post("/segment")
async def segment(
    request: Request,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    prompt: str = "",
) -> Response:
    """A viewport PNG as the raw body and a box in PIXELS as the query; back comes a mask.

    The box is a PROMPT, not a crop. SAM is being asked "what object is in here", and its
    answer routinely reaches outside the rectangle -- which is the entire point, because the
    part of the object the user's drag clipped is exactly what a rectangle cannot express.

    Raw body rather than multipart so this runs on any interpreter that already has torch,
    without adding python-multipart to an environment that belongs to another project.
    """
    model, processor = _load()

    pil = Image.open(io.BytesIO(await request.body())).convert("RGB")
    drag = [float(x0), float(y0), float(x1), float(y1)]

    started = time.time()
    source = "drag"
    prompt = prompt.strip()
    if prompt:
        try:
            grounded, score = _ground_box(pil, prompt, drag)
            if grounded is not None:
                drag = grounded
                source = f"prompt({score:.2f})"
        except Exception as exc:  # never fail the request over the optional half
            print(f"[sam] grounding failed, using the drag box: {exc}", flush=True)

    box = [[drag]]
    inputs = processor(images=pil, input_boxes=box, return_tensors="pt").to(_device)
    with torch.inference_mode():
        out = model(**inputs, multimask_output=False)
    masks = processor.post_process_masks(out.pred_masks.cpu(), inputs["original_sizes"])

    mask = (np.asarray(masks[0][0][0]) > 0).astype(np.uint8) * 255
    buffer = io.BytesIO()
    Image.fromarray(mask, mode="L").save(buffer, format="PNG", optimize=False)

    print(
        f"[sam] {pil.width}x{pil.height} {source} "
        f"box=({drag[0]:.0f},{drag[1]:.0f})-({drag[2]:.0f},{drag[3]:.0f}) "
        f"{prompt!r} -> {int(mask.sum() // 255)} px in {time.time() - started:.2f}s",
        flush=True,
    )
    return Response(
        content=buffer.getvalue(),
        media_type="image/png",
        headers={
            "X-Mask-Pixels": str(int(mask.sum() // 255)),
            "X-Box-Source": source,
        },
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
