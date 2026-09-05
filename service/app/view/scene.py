"""One world: a MuJoCo model and the splat cloud drawn in the same frame.

The whole point of the desktop architecture is that these are not two things kept in step.
The splats live in MJWarp's render context, the bodies live in the MuJoCo model, and one
raytrace pass produces a frame in which a solid body correctly occludes the splats behind it.
The prototype measured that exactly: 135,086 of 135,086 pixels inside a blocker's silhouette
matched a box-only render.

Two costs that decide how this is written, both measured on this machine:

* ``put_model`` is 10.5 ms and ``put_data`` 5.1 ms, against **0.1 ms** to assign
  ``geom_xpos``/``cam_xpos`` in place. So the warp model and data are built ONCE and only
  their transforms are pushed per frame. Rebuilding them each frame costs half a 30 Hz budget.
* First-call kernel compilation is about **4.1 s**, cached afterwards. Budget for it on the
  first render of a session and do not mistake it for a hang.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..splat.ply import Splats

#: A camera the orbit control writes into. MJCF needs one declared to render through.
CAMERA = "eye"


def scene_xml(
    *,
    floor: bool = True,
    floor_size: float = 8.0,
    extra: str = "",
) -> str:
    """The minimal world: a floor, a light, and a camera to look through.

    Kept as a string rather than built with ElementTree because at this stage it is a
    constant, and the generated part -- objects and room collision -- is appended as ``extra``
    by whatever assembled it.
    """
    ground = (
        f'<geom name="env_floor" type="plane" size="{floor_size} {floor_size} 0.1"'
        f' pos="0 0 0" rgba="0.35 0.38 0.45 0.25"/>'
        if floor
        else ""
    )
    return f"""<mujoco model="rsrsplat">
  <compiler angle="degree" autolimits="true"/>
  <option timestep="0.002" gravity="0 0 -9.81"/>
  <visual>
    <headlight ambient="0.5 0.5 0.5" diffuse="0.5 0.5 0.5"/>
  </visual>
  <default>
    <geom friction="0.8 0.005 0.0001" rgba="0.72 0.74 0.78 1"/>
    <joint damping="0.5"/>
  </default>
  <worldbody>
    <light pos="0 0 3" dir="0 0 -1" directional="true"/>
    {ground}
    <camera name="{CAMERA}" pos="0 -3 1.5" xyaxes="1 0 0  0 0 1"/>
{extra}
  </worldbody>
</mujoco>
"""


@dataclass
class SplatScene:
    """A compiled MuJoCo model, its warp mirror, and the splats drawn with it."""

    model: Any
    data: Any
    splats: Splats
    context: Any
    warp_model: Any
    warp_data: Any
    width: int
    height: int
    camera_id: int = field(default=-1)

    # -- construction ---------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        xml: str,
        splats: Splats,
        *,
        width: int = 960,
        height: int = 720,
    ) -> SplatScene:
        import mujoco
        import mujoco_warp as mw

        model = mujoco.MjModel.from_xml_string(xml)
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)

        context = mw.create_render_context(
            model,
            nworld=1,
            cam_res=(width, height),
            render_rgb=True,
            splat_position=np.ascontiguousarray(splats.position, dtype=np.float32),
            splat_rotation=np.ascontiguousarray(splats.rotation, dtype=np.float32),
            splat_scale=np.ascontiguousarray(splats.scale, dtype=np.float32),
            splat_rgba=np.ascontiguousarray(splats.rgba, dtype=np.float32),
        )

        scene = cls(
            model=model,
            data=data,
            splats=splats,
            context=context,
            warp_model=mw.put_model(model),
            warp_data=mw.put_data(model, data),
            width=width,
            height=height,
            camera_id=mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, CAMERA),
        )
        return scene

    # -- per frame ------------------------------------------------------------------------

    def push_pose(self) -> None:
        """Send the current geom and camera transforms to the GPU, in place.

        In place, and only these four arrays. Rebuilding the warp model and data instead
        costs 15.6 ms a frame against 0.1 ms for this.
        """
        d, wd = self.data, self.warp_data
        wd.geom_xpos.assign(d.geom_xpos.reshape(1, -1, 3).astype(np.float32))
        wd.geom_xmat.assign(d.geom_xmat.reshape(1, -1, 3, 3).astype(np.float32))
        wd.cam_xpos.assign(d.cam_xpos.reshape(1, -1, 3).astype(np.float32))
        wd.cam_xmat.assign(d.cam_xmat.reshape(1, -1, 3, 3).astype(np.float32))

    def render(self) -> np.ndarray:
        """One composited RGB frame, as (h, w, 3) uint8."""
        import mujoco_warp as mw

        mw.render(self.warp_model, self.warp_data, self.context)
        packed = self.context.rgb_data.numpy()[0]
        return unpack_rgb(packed).reshape(self.height, self.width, 3)


def unpack_rgb(packed: np.ndarray) -> np.ndarray:
    """MJWarp packs each pixel into one uint32 as 0x00RRGGBB."""
    r = ((packed >> 16) & 0xFF).astype(np.uint8)
    g = ((packed >> 8) & 0xFF).astype(np.uint8)
    b = (packed & 0xFF).astype(np.uint8)
    return np.stack([r, g, b], axis=-1)


__all__ = ["CAMERA", "SplatScene", "scene_xml", "unpack_rgb"]
