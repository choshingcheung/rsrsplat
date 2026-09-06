# Pinned meshes

A `.glb` here is used **as-is** for any prompt containing its file name, with no generation
and no network call. This is what a demo should run on.

    vest.glb            ->  any prompt containing "vest"
    high_vis_vest.glb   ->  wins over vest.glb when both match; longest name wins
    tool_box.glb        ->  underscores read as spaces, so "the tool box" matches
    default.glb         ->  matches anything, for when there is only one object

## Why not just rely on the cache

Generated meshes are cached under the hash of the image that produced them, which is correct
for avoiding duplicate work and useless for a demo: the second run photographs the object
from a slightly different camera, so the bytes differ, so the hash differs, so it generates
again — for a minute, on stage, spending credits.

## Pinning one you already generated

    cp assets/meshes/<hash>.glb assets/meshes/pinned/vest.glb

The `.glb` files themselves are gitignored; this README is not.
