"""The WebSocket physics service.

One connection is one session, holding one MuJoCo model. Two things happen concurrently:
a receive loop handling client messages, and a stream task broadcasting poses at roughly
30 Hz. They are separate because they run at different rates and neither should wait on the
other -- a physicalise that takes 40 ms to compile must not drop a frame of the stream.

**The splat data never crosses this socket.** Selections arrive as a centroid, three axes and
three half-extents; poses go back as a position and a quaternion per body. A million-Gaussian
scene stays in the browser, where it was loaded.

Schema generation is the fallback path only. The model path arrives in S7 and sits in front
of it; this is deliberately the order REPO_INIT.md asks for, because a fallback written
afterwards is a fallback nobody has run.

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import TypeAdapter, ValidationError

from .mesh import tripo as mesh
from .mjcf.build import SchemaError
from .mjcf.scene import SceneError
from .protocol import (
    BodyDrag,
    ClientMessage,
    JointSet,
    ObjectCreated,
    ObjectFailed,
    ObjectPhysicalize,
    ObjectRemove,
    SceneLoad,
    SceneReady,
    Selection,
    SelectionCommit,
    SimControl,
)
from .schema import fallback
from .sim.session import Session

log = logging.getLogger("rsrsplat")

#: Pose broadcast rate. Deliberately not the physics rate: the timestep stays at 0.002 for
#: stable contacts, and the client interpolates between these to reach 60 fps.
BROADCAST_HZ = 30.0

CLIENT = TypeAdapter(ClientMessage)

app = FastAPI(title="rsrsplat", version="0.1.0")

# The browser is served by vite on another port, so every request from it is cross-origin.
# Local tool, local service: the permissive policy is the honest one rather than a pretence
# of a boundary that does not exist.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Mesh-Cached"],
)

mesh.MESH_DIR.mkdir(parents=True, exist_ok=True)
# Generated meshes are served straight off disk. They are large binaries and deliberately
# outside the repo; see assets/ in .gitignore.
app.mount("/meshes", StaticFiles(directory=str(mesh.MESH_DIR)), name="meshes")


@app.post("/mesh")
async def make_mesh(request: Request) -> dict:
    """A PNG of one object, in; a generated GLB, out.

    The image is the whole prompt, so it is also the cache key -- the same selection
    photographed the same way returns the same mesh with no network call. That is what makes
    this demoable: generation takes the better part of a minute and costs credits, neither of
    which is acceptable in front of an audience.

    Run in a worker thread. Polling an external job from inside the event loop would stall
    the pose stream for a minute, which looks exactly like the simulation having crashed.
    """
    image = await request.body()
    if len(image) < 128:
        raise HTTPException(status_code=400, detail="empty or truncated image")

    hit = mesh.cached(image)
    if hit is not None:
        return {"url": hit.url, "cached": True}

    try:
        made = await asyncio.to_thread(mesh.generate, image)
    except mesh.TripoError as exc:
        # 502 rather than 500: the failure is upstream, and the message is written to be
        # shown to the user unchanged -- "not enough credit" is something only they can fix.
        log.warning("mesh generation failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from None

    log.info("generated mesh %s", made.path.name)
    return {"url": made.url, "cached": False}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


class Connection:
    """One browser, one session, and the two tasks that serve it."""

    def __init__(self, websocket: WebSocket):
        self.ws = websocket
        self.session: Session | None = None
        self.selections: dict[str, Selection] = {}
        self.stream: asyncio.Task | None = None
        # Both tasks send, and interleaved frames on one socket are corrupt frames.
        self._sending = asyncio.Lock()

    async def send(self, message: Any) -> None:
        async with self._sending:
            await self.ws.send_text(message.model_dump_json(by_alias=True))

    # -- the stream ---------------------------------------------------------------------

    async def run_stream(self) -> None:
        """Advance the simulation on a wall clock and broadcast what came of it.

        Sleeping for the period rather than stepping a fixed count keeps simulated time
        tracking real time even when a frame runs late, which is what stops a scene drifting
        into slow motion under load.
        """
        period = 1.0 / BROADCAST_HZ
        last = time.perf_counter()
        try:
            while True:
                await asyncio.sleep(period)
                now = time.perf_counter()
                elapsed, last = now - last, now

                session = self.session
                if session is None:
                    continue
                session.advance(elapsed)
                if session.objects:
                    await self.send(session.poses())
        except asyncio.CancelledError:
            raise
        except (WebSocketDisconnect, RuntimeError):
            pass  # the socket went away; the receive loop handles the teardown

    # -- messages -----------------------------------------------------------------------

    async def handle(self, message: Any) -> None:
        if isinstance(message, SceneLoad):
            await self.on_scene_load(message)
        elif isinstance(message, SelectionCommit):
            self.selections[message.selection.id] = message.selection
        elif isinstance(message, ObjectPhysicalize):
            await self.on_physicalize(message)
        elif isinstance(message, ObjectRemove):
            await self.on_remove(message)
        elif isinstance(message, BodyDrag):
            if self.session is not None:
                self.session.drag(message.body_name, message.target)
        elif isinstance(message, JointSet):
            if self.session is not None:
                self.session.set_joint(message.body_name, message.value)
        elif isinstance(message, SimControl):
            await self.on_control(message)
        else:
            # mesh.attach is Phase 9. Acknowledge nothing rather than pretend.
            log.info("ignoring %s: not implemented yet", message.type)

    async def on_scene_load(self, message: SceneLoad) -> None:
        try:
            self.session = Session(world=message.world, obstacles=message.obstacles)
        except SceneError as exc:
            # The browser sent a frame we cannot simulate in. Say so against the scene
            # rather than silently running a session that will never behave.
            await self.send(ObjectFailed(selectionId=message.splat_id, reason=str(exc)))
            return

        self.selections.clear()
        log.info(
            "scene %s loaded: %d splats, ground at %.3f, %d obstacles",
            message.splat_id,
            message.splat_count,
            message.world.ground_height,
            len(message.obstacles),
        )
        await self.send(SceneReady(sessionId=self.session.session_id))
        await self.send(self.session.status())

        if self.stream is None:
            self.stream = asyncio.create_task(self.run_stream())

    async def on_physicalize(self, message: ObjectPhysicalize) -> None:
        if self.session is None:
            await self.send(
                ObjectFailed(
                    selectionId=message.selection_id,
                    reason="no scene loaded yet - send scene.load first",
                )
            )
            return

        selection = self.selections.get(message.selection_id)
        if selection is None:
            await self.send(
                ObjectFailed(
                    selectionId=message.selection_id,
                    reason=f"unknown selection {message.selection_id!r}; commit it first",
                )
            )
            return

        # The patch that closes the hole this object is about to leave. It has to be in the
        # model BEFORE the object exists, or the first frame is simulated against a floor
        # with a hole in it -- which is one step, but one step is all it takes for something
        # resting there to start falling.
        if message.obstacles:
            self.session.add_obstacles(message.obstacles)

        schema, source = fallback.describe(message.prompt, tuple(selection.half_extents))
        object_id = f"obj_{uuid.uuid4().hex[:8]}"

        try:
            created = self.session.physicalize(object_id, schema, selection)
        except (SchemaError, SceneError, ValueError) as exc:
            reason = "; ".join(getattr(exc, "errors", None) or [str(exc)])
            log.warning("physicalize failed for %s: %s", message.selection_id, reason)
            await self.send(ObjectFailed(selectionId=message.selection_id, reason=reason))
            return

        log.info("physicalized %s from %r (%s)", object_id, message.prompt, source)
        await self.send(ObjectCreated(object=created))

    async def on_remove(self, message: ObjectRemove) -> None:
        if self.session is not None and self.session.remove(message.object_id):
            await self.send(self.session.status())

    async def on_control(self, message: SimControl) -> None:
        if self.session is None:
            return
        self.session.control(message.action)
        await self.send(self.session.status())

    async def close(self) -> None:
        if self.stream is not None:
            self.stream.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.stream


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    connection = Connection(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = CLIENT.validate_json(raw)
            except ValidationError as exc:
                # A malformed message is a bug on the other side of a hand-mirrored
                # contract -- most often one side restarted and the other did not. Do not
                # take the session down over one frame, but do not swallow it either: a
                # dropped scene.load surfaces three steps later as "no scene loaded yet",
                # which sends you looking in entirely the wrong place.
                detail = "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
                )
                log.warning("rejected client message: %s", detail)
                await connection.send(
                    ObjectFailed(
                        selectionId="protocol",
                        reason=(
                            f"the service rejected a message it could not parse ({detail}). "
                            f"This usually means the service and the page are running "
                            f"different versions of the contract - restart uvicorn."
                        ),
                    )
                )
                continue
            await connection.handle(message)
    except WebSocketDisconnect:
        log.info("client disconnected")
    finally:
        await connection.close()


__all__ = ["BROADCAST_HZ", "Connection", "app"]
