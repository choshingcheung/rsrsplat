"""Write the golden protocol fixtures.

Content is hand-authored below; this script is only the pen. Run once from the repo root.

The running example is a dishwasher standing on the floor of the playroom capture, yawed
30 degrees about the scene up axis, with a bottom-hinged door. Chosen because it exercises
every interesting part of the contract at once: a non-identity right-handed frame, a
symbolic splat region, degrees on the wire, and a body origin sitting on its joint anchor.
"""

import json
import math
import pathlib


def qmul(a, b):
    """Hamilton product of two (w, x, y, z) quaternions, MuJoCo order."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]


def axis_angle(axis, degrees):
    """A (w, x, y, z) quaternion for a rotation about a unit axis."""
    half = math.radians(degrees) / 2.0
    s = math.sin(half)
    return [math.cos(half), axis[0] * s, axis[1] * s, axis[2] * s]

# 30 degrees of yaw about scene up (+z), so the canonical frame is non-trivial and a
# determinant check on it actually means something.
COS30, SIN30 = 0.8660254037844387, 0.5
# Half angle, for the equivalent quaternion.
COS15, SIN15 = 0.9659258262890683, 0.25881904510252074

# Column-major: column 0 = +front, column 1 = +left, column 2 = +up. Right-handed, because
# left is up x front. This matches the prototype's anchors frame (+x front, +z up), which
# every stored schema and the reference MJCF already depend on.
AXES = [COS30, SIN30, 0.0, -SIN30, COS30, 0.0, 0.0, 0.0, 1.0]

# A 600 x 600 x 850 mm dishwasher: half-extents in metres.
HALF_EXTENTS = [0.30, 0.30, 0.425]

# Ground sits at -1.42 m along up, so a centroid at -0.995 puts the base exactly on it.
CENTROID = [1.2, -0.4, -0.995]
SHELL_QUAT = [COS15, 0.0, 0.0, SIN15]

# The door caught mid-swing: 45 degrees about its own hinge axis, composed onto the shell's
# yaw. A bottom-hinged door pivots about the horizontal left-right axis, which is column 1
# of the canonical frame, so in the parent's local frame the rotation is about local y --
# the prototype's "left_right" axis.
DOOR_HALF_OPEN_QUAT = qmul(SHELL_QUAT, axis_angle((0.0, 1.0, 0.0), 45.0))

# The door's body origin sits ON its hinge anchor -- the bottom front edge -- which is
# what makes every generated joint pos="0 0 0" and removes a class of arithmetic bugs.
# centroid + 0.30 * column0 (front) + (-0.425) * column2 (up)
DOOR_ORIGIN = [
    CENTROID[0] + 0.30 * COS30,
    CENTROID[1] + 0.30 * SIN30,
    CENTROID[2] - 0.425,
]

SELECTION = {
    "id": "sel_01",
    "splatCount": 18422,
    "centroid": CENTROID,
    "axes": AXES,
    "halfExtents": HALF_EXTENTS,
}

CLIENT = {
    "scene.load": {
        "type": "scene.load",
        "splatId": "playroom_7000",
        "splatCount": 1543280,
        "world": {
            "up": [0.0, 0.0, 1.0],
            "groundHeight": -1.42,
            "sceneScale": 1.0,
        },
        # The room as boxes physics can hit. A splat stops nothing, so without these the
        # only solid thing in the scene is the ground plane.
        "obstacles": [
            {
                "id": "surface_2",
                "kind": "surface",
                "position": [1.05, -0.4, -0.51],
                "halfExtents": [0.62, 0.31, 0.02],
            },
            {
                "id": "wall_xhi",
                "kind": "wall",
                "position": [2.4, 0.0, -0.12],
                "halfExtents": [0.05, 1.9, 1.3],
            },
        ],
    },
    "selection.commit": {
        "type": "selection.commit",
        "selection": SELECTION,
    },
    "object.physicalize": {
        "type": "object.physicalize",
        "selectionId": "sel_01",
        "prompt": "a dishwasher, the door hinges at the bottom and opens ninety degrees",
    },
    "object.remove": {
        "type": "object.remove",
        "objectId": "obj_01",
    },
    "joint.set": {
        # Degrees, because the door is a hinge. The service converts to radians once.
        "type": "joint.set",
        "bodyName": "obj_01__door",
        "value": 62.5,
    },
    "body.drag": {
        # Where the body is being pulled toward. null releases it.
        "type": "body.drag",
        "bodyName": "obj_01__crate",
        "target": [1.24, -0.31, -0.86],
    },
    "sim.control": {
        "type": "sim.control",
        "action": "play",
    },
    "mesh.attach": {
        "type": "mesh.attach",
        "objectId": "obj_01",
        "glbUrl": "blob:http://localhost:5173/6f1b0c9a-dishwasher",
    },
}

SERVER = {
    "scene.ready": {
        "type": "scene.ready",
        "sessionId": "ses_7f3a",
    },
    "object.created": {
        "type": "object.created",
        "object": {
            "id": "obj_01",
            "label": "dishwasher",
            "selectionId": "sel_01",
            "massKg": 45.0,
            "friction": 0.8,
            "parts": [
                {
                    "bodyName": "dishwasher_shell",
                    "jointType": "fixed",
                    "range": None,
                    "splatSubset": "all",
                    "initialPose": {
                        "bodyName": "dishwasher_shell",
                        "position": CENTROID,
                        "orientation": SHELL_QUAT,
                    },
                },
                {
                    "bodyName": "dishwasher_door",
                    # Degrees on the wire. MuJoCo qpos is radians; the service converts.
                    "jointType": "hinge",
                    "range": [0.0, 90.0],
                    "splatSubset": "front_lower",
                    "initialPose": {
                        "bodyName": "dishwasher_door",
                        "position": DOOR_ORIGIN,
                        "orientation": SHELL_QUAT,
                    },
                },
            ],
        },
    },
    "object.failed": {
        "type": "object.failed",
        "selectionId": "sel_02",
        "reason": "hinge range of 270 degrees is implausible for a door; expected 0 to 180",
    },
    "pose.batch": {
        "type": "pose.batch",
        "t": 1.3333333333333333,
        "poses": [
            {
                "bodyName": "dishwasher_shell",
                "position": CENTROID,
                "orientation": SHELL_QUAT,
            },
            {
                "bodyName": "dishwasher_door",
                # The origin stays put: the body origin sits on the hinge anchor, so the
                # door rotates about it rather than translating.
                "position": DOOR_ORIGIN,
                "orientation": DOOR_HALF_OPEN_QUAT,
            },
        ],
    },
    "sim.status": {
        "type": "sim.status",
        "running": True,
        "stepCount": 4000,
    },
}


def main() -> None:
    root = pathlib.Path("contract/fixtures")
    for direction, messages in (("client", CLIENT), ("server", SERVER)):
        out = root / direction
        out.mkdir(parents=True, exist_ok=True)
        for name, payload in messages.items():
            path = out / f"{name}.json"
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
