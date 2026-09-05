"""The desktop application: one window, one world.

MuJoCo-Warp renders the splats and the physics geometry into the same framebuffer, so they
occlude each other by construction rather than by agreement between two processes.

**MuJoCo's own viewer cannot draw splats.** It renders through OpenGL and knows nothing about
them; splats exist only inside MJWarp's raytraced framebuffer. So this opens its own GLFW
window, steps physics, calls ``mw.render``, and blits the result as a texture.
"""
