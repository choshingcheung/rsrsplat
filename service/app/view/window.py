"""The window.

MuJoCo's own viewer cannot draw splats -- it renders through OpenGL and knows nothing about
them, and splats exist only inside MJWarp's raytraced framebuffer. So this opens a GLFW
window, steps physics, calls ``mw.render``, and blits the resulting pixels as a texture.
Everything on screen is one composited frame.

The blit is immediate-mode OpenGL, which is ancient and exactly right here: the entire job is
to put one full-screen image on the screen, and a shader pipeline for that would be more code
doing the same thing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..splat.ply import Splats
from ..splat.sample import drop_faint, subsample
from .camera import Orbit
from .scene import SplatScene, scene_xml

#: Splat budgets the viewer can switch between, on keys 1-4.
#:
#: Measured at 960x720 on an RTX 4060: 80k is 42 ms, 200k is 98 ms, 400k is 285 ms. A raytraced
#: splat frame costs roughly in proportion to how many Gaussians a ray wades through, so this
#: is the single control that decides whether the thing is usable.
BUDGETS = (60_000, 120_000, 250_000, 500_000)


@dataclass
class Viewer:
    """A window onto one splat scene."""

    source: Splats
    width: int = 960
    height: int = 720
    budget_index: int = 1
    show_splats: bool = True

    scene: SplatScene | None = field(default=None, repr=False)
    camera: Orbit = field(default_factory=Orbit)

    # -- scene ----------------------------------------------------------------------------

    def rebuild(self) -> None:
        """Compile the scene at the current splat budget.

        A budget change means a new render context, which is the one expensive operation
        here. Everything else is done in place.
        """
        budget = BUDGETS[self.budget_index]
        splats = subsample(drop_faint(self.source), budget)
        print(f"  budget {budget:,} -> {len(splats):,} splats", flush=True)
        self.scene = SplatScene.build(
            scene_xml(), splats, width=self.width, height=self.height
        )
        self.camera.frame(self.source.position)

    # -- the loop -------------------------------------------------------------------------

    def run(self) -> int:
        import glfw
        import mujoco
        import OpenGL.GL as gl

        if not glfw.init():
            print("glfw failed to initialise")
            return 1

        glfw.window_hint(glfw.RESIZABLE, glfw.FALSE)
        window = glfw.create_window(self.width, self.height, "rsrsplat", None, None)
        if not window:
            glfw.terminate()
            print("could not open a window")
            return 1

        glfw.make_context_current(window)
        glfw.swap_interval(0)  # never wait for vsync; we want the true frame cost

        texture = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)

        state = {"button": None, "x": 0.0, "y": 0.0}

        def on_button(_win, button, action, _mods):
            down = action == glfw.PRESS
            if button == glfw.MOUSE_BUTTON_LEFT:
                state["button"] = "orbit" if down else None
            elif button == glfw.MOUSE_BUTTON_RIGHT:
                state["button"] = "pan" if down else None

        def on_move(_win, x, y):
            dx, dy = x - state["x"], y - state["y"]
            state["x"], state["y"] = x, y
            if state["button"] == "orbit":
                self.camera.orbit(dx, dy)
            elif state["button"] == "pan":
                self.camera.pan(dx, dy)

        def on_scroll(_win, _dx, dy):
            self.camera.zoom(dy)

        def on_key(_win, key, _code, action, _mods):
            if action != glfw.PRESS:
                return
            if key == glfw.KEY_ESCAPE:
                glfw.set_window_should_close(window, True)
            elif key == glfw.KEY_S:
                self.show_splats = not self.show_splats
            elif key in (glfw.KEY_1, glfw.KEY_2, glfw.KEY_3, glfw.KEY_4):
                self.budget_index = key - glfw.KEY_1
                self.rebuild()

        glfw.set_mouse_button_callback(window, on_button)
        glfw.set_cursor_pos_callback(window, on_move)
        glfw.set_scroll_callback(window, on_scroll)
        glfw.set_key_callback(window, on_key)

        # First render compiles warp kernels, about 4 seconds. Say so, or it reads as a hang.
        print("  compiling kernels (about 4s on a cold cache)...", flush=True)
        self.rebuild()

        frames = 0
        since = time.perf_counter()

        while not glfw.window_should_close(window):
            glfw.poll_events()
            scene = self.scene
            assert scene is not None

            self.camera.write(scene.model, scene.camera_id)
            # The camera lives on the CPU model, so derived poses must be recomputed before
            # they are pushed. mj_forward is cheap next to the raytrace.
            mujoco.mj_forward(scene.model, scene.data)
            scene.push_pose()

            image = scene.render() if self.show_splats else self.blank()

            gl.glBindTexture(gl.GL_TEXTURE_2D, texture)
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D, 0, gl.GL_RGB, self.width, self.height, 0,
                gl.GL_RGB, gl.GL_UNSIGNED_BYTE, np.flipud(image).copy(),
            )
            gl.glEnable(gl.GL_TEXTURE_2D)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT)
            gl.glBegin(gl.GL_QUADS)
            for u, v, x, y in ((0, 0, -1, -1), (1, 0, 1, -1), (1, 1, 1, 1), (0, 1, -1, 1)):
                gl.glTexCoord2f(u, v)
                gl.glVertex2f(x, y)
            gl.glEnd()
            glfw.swap_buffers(window)

            frames += 1
            elapsed = time.perf_counter() - since
            if elapsed >= 0.5:
                fps = frames / elapsed
                glfw.set_window_title(
                    window,
                    f"rsrsplat — {len(scene.splats):,} splats — "
                    f"{fps:5.1f} fps — {1000 / fps:5.1f} ms",
                )
                frames, since = 0, time.perf_counter()

        glfw.terminate()
        return 0

    def blank(self) -> np.ndarray:
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)


__all__ = ["BUDGETS", "Viewer"]
