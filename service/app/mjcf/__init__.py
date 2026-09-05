"""Articulation schema to MJCF, and a schema placed into a scanned scene.

``build`` makes one object as a standalone model, which is what tests and the MuJoCo viewer
want. ``scene`` puts objects into the room the browser measured, which is what the running
session wants.
"""

from . import scene
from .build import (
    BUTTON_STANDOFF,
    PANEL,
    PART_DENSITY,
    WALL,
    SchemaError,
    build,
    build_object,
    compile_model,
    exclude_internal_contacts,
    to_xml,
)

__all__ = [
    "BUTTON_STANDOFF",
    "PANEL",
    "PART_DENSITY",
    "WALL",
    "SchemaError",
    "build",
    "build_object",
    "compile_model",
    "exclude_internal_contacts",
    "scene",
    "to_xml",
]
