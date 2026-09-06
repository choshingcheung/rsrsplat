"""Generated visual geometry for scanned objects.

The mesh is what the user SEES; the physics body stays on the voxel boxes measured from the
splats. Keeping those separate is what lets a generated mesh be optional: if generation
fails, is not paid for, or is simply slow, the object is still a working physics body wearing
its original splats.
"""

from .tripo import MESH_DIR, Mesh, TripoError, cached, find, generate, key_for

__all__ = ["MESH_DIR", "Mesh", "TripoError", "cached", "find", "generate", "key_for"]
