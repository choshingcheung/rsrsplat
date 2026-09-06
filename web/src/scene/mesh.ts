/**
 * Swapping a scanned object's splats for a generated mesh.
 *
 * A splat cloud lifted out of a scan looks right from where the scanner stood and wrong from
 * anywhere else, because the far side of the object was never observed. That is invisible
 * while the object sits still and unmissable the moment it is thrown — it tumbles and shows a
 * hollow. Segmentation cannot fix this: the data is not there to segment.
 *
 * So the object's APPEARANCE comes from a generated mesh while its PHYSICS stays on the voxel
 * boxes measured from the splats. Keeping those separate is what makes the mesh optional. The
 * solver never sees it, there is no convex decomposition anywhere, and if generation fails —
 * or is not paid for — the object is still a working body wearing its original splats.
 *
 * The mesh is parented to the part's own group, so it inherits the pose for free. Nothing in
 * the pose path changes; there is no second thing to keep in sync.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

const SERVICE = import.meta.env.VITE_SERVICE_URL ?? `http://${location.hostname}:8000`;

/**
 * Photograph one object on its own.
 *
 * Takes what to HIDE rather than what to keep, and that is not a stylistic choice. The scene
 * also holds a `SparkRenderer`, which is the object that actually draws Gaussians; hiding
 * everything except the target would hide it too and produce a blank grey frame. Tripo would
 * then dutifully reconstruct a grey rectangle, and the fault would look like the model's.
 *
 * Isolation matters because image-to-3D reconstructs what it is shown: a picture containing
 * a slice of carpet produces a mesh with carpet fused to it.
 */
export function captureObject(
  renderer: THREE.WebGLRenderer,
  scene: THREE.Scene,
  camera: THREE.Camera,
  hide: (THREE.Object3D | null | undefined)[],
): Blob | null {
  const hidden: THREE.Object3D[] = [];
  for (const item of hide) {
    if (item && item.visible) {
      item.visible = false;
      hidden.push(item);
    }
  }

  // A flat mid-grey ground rather than the scene's near-black: a subject floating in a void
  // gives the model no sense of scale, and pure black eats the object's own shadows.
  const background = scene.background;
  scene.background = new THREE.Color(0.42, 0.42, 0.44);

  try {
    renderer.render(scene, camera);
    return dataUrlToBlob(renderer.domElement.toDataURL("image/png"));
  } catch {
    return null;
  } finally {
    scene.background = background;
    for (const item of hidden) item.visible = true;
    renderer.render(scene, camera);
  }
}

export interface MeshResult {
  url: string;
  cached: boolean;
  /** True when a hand-chosen mesh was used rather than anything generated. */
  pinned?: boolean;
}

/**
 * Ask the service for a mesh of this image.
 *
 * The prompt goes too, and not as a hint to the generator: it is how a PINNED mesh is
 * chosen. The content cache keys on the image, which misses whenever the camera has moved
 * even slightly, so a demo that depends on it generates for a minute on stage. A mesh
 * pinned by name is the same mesh every run.
 *
 * The service holds the API key and the cache; the browser only ever sees a URL. A failure
 * returns null with the reason logged, because a missing mesh is a cosmetic loss and must
 * never take the object down with it.
 */
export async function requestMesh(image: Blob, prompt = ""): Promise<MeshResult | null> {
  try {
    const query = prompt.trim() ? `?prompt=${encodeURIComponent(prompt.trim())}` : "";
    const response = await fetch(`${SERVICE}/mesh${query}`, {
      method: "POST",
      body: image,
      headers: { "Content-Type": "image/png" },
    });
    if (!response.ok) {
      const detail = await response.text();
      console.warn(`rsrsplat: mesh generation failed — ${detail}`);
      return null;
    }
    const body = (await response.json()) as MeshResult;
    return { url: `${SERVICE}${body.url}`, cached: Boolean(body.cached) };
  } catch (error) {
    console.warn("rsrsplat: could not reach the mesh service", error);
    return null;
  }
}

/**
 * Load a GLB and put it exactly where the object was.
 *
 * Three things have to be corrected, and each one alone is enough to make a correct mesh
 * look like a broken one:
 *
 * 1. **glTF is Y-up.** This scene is Z-up, because that is what the splats were aligned to
 *    at load. Dropped in unrotated, the vest lies on its side and reads as the physics
 *    having thrown it.
 * 2. **The mesh has its own origin**, which is wherever the generator put it and is not the
 *    centre of anything. Uncentred, the object orbits a point somewhere outside itself.
 * 3. **The mesh has its own units.** Generators normalise to roughly unit size, so a vest
 *    arrives about a metre across. It is fitted to the half-extents the SELECTION measured
 *    — the same numbers the physics body uses, which is what keeps what you see and what
 *    you collide with the same size.
 *
 * The result is a group whose origin is the object's centre, upright, at the right scale,
 * ready to be parented to the body and inherit its pose.
 */
export async function loadMesh(
  url: string,
  halfExtents: THREE.Vector3,
): Promise<THREE.Object3D | null> {
  let gltf;
  try {
    gltf = await new GLTFLoader().loadAsync(url);
  } catch (error) {
    console.warn("rsrsplat: could not load the generated mesh", error);
    return null;
  }

  const inner = gltf.scene;

  // Y-up to Z-up, before anything is measured: bounds taken first would describe the mesh
  // in the wrong frame and the fit would be wrong on two axes.
  inner.rotation.set(Math.PI / 2, 0, 0);
  inner.updateMatrixWorld(true);

  const bounds = new THREE.Box3().setFromObject(inner);
  const size = bounds.getSize(new THREE.Vector3());
  if (size.x < 1e-6 || size.y < 1e-6 || size.z < 1e-6) {
    console.warn("rsrsplat: the generated mesh has no extent");
    return null;
  }

  // Uniform, by the tightest axis. A non-uniform fit would stretch the mesh to fill a
  // measured box that is itself only an approximation of the object, so a slightly loose
  // selection would visibly distort it.
  const target = halfExtents.clone().multiplyScalar(2);
  const scale = Math.min(target.x / size.x, target.y / size.y, target.z / size.z);

  const centre = bounds.getCenter(new THREE.Vector3());
  const group = new THREE.Group();
  // Scale acts about the group origin, so the centring offset is scaled too. Applying the
  // raw centre here puts the mesh adrift by however much the scale differs from one.
  inner.position.copy(centre).multiplyScalar(-scale);
  inner.scale.setScalar(scale);
  group.add(inner);
  return group;
}

function dataUrlToBlob(dataUrl: string): Blob {
  const binary = atob(dataUrl.slice(dataUrl.indexOf(",") + 1));
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: "image/png" });
}
