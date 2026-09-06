/**
 * The scene, outside React.
 *
 * Owns the renderer, the capture, the connection to the physics service, and everything that
 * changes sixty times a second. React owns the chrome and reads a handful of numbers out of
 * the store; nothing here reconciles through a component tree, because a scene graph is
 * mutable state and pretending otherwise is both slower and harder to follow.
 *
 * The whole product is four methods: `load`, `beginDrag`/`updateDrag`/`endDrag`, and
 * `physicalize`. Everything else is bookkeeping.
 */

import {
  PackedSplats,
  SparkRenderer,
  SplatEdit,
  SplatEditRgbaBlendMode,
  SplatEditSdf,
  SplatEditSdfType,
  SplatMesh,
} from "@sparkjsdev/spark";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { connect, type Service } from "../net/client";
import { PoseStream } from "../net/interpolate";
import { measureFrame, toSelection, type MeasuredFrame } from "../selection/frame";
import { pick, rectFromPointers, type ScreenRect } from "../selection/pick";
import { captureView, lastBoxSource, segmentView, type View } from "../selection/sam";
import { cellsOf, grow, toBoxes, voxelise } from "../selection/voxels";
import { useScene } from "../store/scene";
import type {
  ClientMessage,
  PhysicsObject,
  ServerMessage,
  ShapeBox,
} from "../types/protocol";
import { bind, orientationOf, unbind, type BoundObject } from "./binding";
import { alignScene, roomObstacles } from "./ground";
import { healSurface, mergeInto, type Patch } from "./heal";
import { sceneCollision } from "./solid";
import { applyAlignment, readPly, type SplatCloud } from "./splats";

/** How often the frame counter reaches React. Every frame would re-render the tree at 60 Hz. */
const PERF_INTERVAL_MS = 500;

/** Sampling during a drag. The commit always runs at stride 1. */
const DRAG_STRIDE = 12;

/** How far the unselected scene is dimmed while a selection is live. */
const DIM = 0.28;

/**
 * The live controller.
 *
 * There is exactly one canvas, so there is exactly one of these. Threading it through React
 * state instead added a failure mode for no benefit: if the state had not been set by the
 * time the prompt bar submitted, `controller?.physicalize(...)` was a silent no-op and
 * nothing anywhere said so.
 */
let current: SceneController | null = null;

export function activeController(): SceneController | null {
  return current;
}

export class SceneController {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera: THREE.PerspectiveCamera;
  private controls: OrbitControls;
  private observer: ResizeObserver;
  private raf = 0;

  private cloud: SplatCloud | null = null;
  private staticMesh: SplatMesh | null = null;
  private service: Service | null = null;

  /** The live selection: its splats, its measured frame, and the highlight showing it. */
  private selectedIndices: Uint32Array | null = null;
  private selectedFrame: MeasuredFrame | null = null;
  private selectionId: string | null = null;
  private highlight: SplatEdit | null = null;
  private dimmer: SplatEdit | null = null;

  private dragFrom: { x: number; y: number } | null = null;
  private dragRect: ScreenRect | null = null;

  private bound = new Map<string, BoundObject>();
  private meshFor = new Map<string, THREE.Object3D>();
  /** Buffered poses, interpolated by elapsed time rather than smoothed toward. */
  private stream = new PoseStream();

  /** Which bodies can be picked up. A fitted appliance is not one of them. */
  private draggable = new Set<string>();
  private grabbed: { bodyName: string; plane: THREE.Plane } | null = null;

  private readonly raycaster = new THREE.Raycaster();

  /** Heights of the detected horizontal surfaces, so a grow cannot leave through one. */
  private surfaces: number[] = [];

  /** The floor patch for the object currently being physicalised, applied when it binds. */
  private patch: Patch | null = null;

  /** The frozen view and camera the selection rectangle was drawn in, for the model. */
  private pending: {
    rect: ScreenRect;
    camera: THREE.PerspectiveCamera;
    view: View | null;
  } | null = null;

  private counter = 0;

  constructor(private container: HTMLDivElement) {
    this.renderer = new THREE.WebGLRenderer({
      // Spark's own guidance: WebGL anti-aliasing does nothing for Gaussian splatting and
      // costs a great deal of performance.
      antialias: false,
      powerPreference: "high-performance",
      // Needed to read the canvas back for SAM. A WebGL drawing buffer is not readable once
      // the frame has been composited, so without this the capture is a blank image and the
      // mask comes back empty -- which looks exactly like a segmentation failure.
      preserveDrawingBuffer: true,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(this.renderer.domElement);

    this.camera = new THREE.PerspectiveCamera(
      55,
      container.clientWidth / container.clientHeight,
      0.05,
      500,
    );
    this.camera.up.set(0, 0, 1); // the scene is z-up once aligned
    this.camera.position.set(0, -4, 1.6);

    this.scene.add(new SparkRenderer({ renderer: this.renderer }));

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    // Damping that decelerates rather than glides. The interface claims to be an instrument
    // with mass, and the camera is the largest moving thing in it.
    this.controls.dampingFactor = 0.08;
    this.controls.rotateSpeed = 0.65;

    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(container);
    current = this;
    this.tick();
  }

  // -- lifecycle ------------------------------------------------------------------------

  private resize(): void {
    const { clientWidth: w, clientHeight: h } = this.container;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  dispose(): void {
    // Only if we are still the live one. React's StrictMode mounts, unmounts and remounts in
    // development, and clearing unconditionally would blank a successor that already exists.
    if (current === this) current = null;
    cancelAnimationFrame(this.raf);
    this.observer.disconnect();
    this.service?.stop();
    this.controls.dispose();
    this.renderer.dispose();
    this.container.removeChild(this.renderer.domElement);
  }

  // -- loading --------------------------------------------------------------------------

  async load(file: File): Promise<void> {
    const store = useScene.getState();
    store.beginReading(file.name);

    try {
      const cloud = await readPly(new Uint8Array(await file.arrayBuffer()));

      // Make it metric and z-up before anything measures anything. Both the centres used
      // for selection and the mesh used for rendering take the same transform, or the
      // physics and the pixels disagree.
      const alignment = alignScene(cloud);

      if (this.staticMesh) {
        this.scene.remove(this.staticMesh);
        this.staticMesh.dispose();
      }
      this.clearBindings();

      // One frame throughout. The mesh carries no transform of its own: compensating on the
      // render mesh while the packed data stayed in the capture's frame is what made a
      // physicalised object disappear -- its splats were rebuilt in the wrong frame while
      // the hole was cut in the right one.
      const aligned = applyAlignment(cloud, alignment.matrix);
      const mesh = new SplatMesh({ packedSplats: aligned.packed, editable: true });
      await mesh.initialized;
      this.scene.add(mesh);

      this.cloud = aligned;
      this.staticMesh = mesh;
      this.frameCamera();
      store.ready(cloud.count);

      this.service?.stop();
      this.service = await connect({
        onMessage: (message) => this.receive(message),
        onTransport: (transport) => useScene.getState().setTransport(transport),
      });
      // The room, as boxes physics can hit. Without these the only solid thing in the
      // scene is the ground plane, and a bottle knocked off a worktop falls through the
      // worktop, through the floor, and out of the world.
      const room = roomObstacles(aligned.centers, aligned.count, alignment.groundHeight);

      // ...and the room ITSELF, voxelised. The line above gives four walls and whatever
      // horizontal patches were detected -- six geoms for a whole kitchen, which leaves the
      // units, the sink and the appliances as scenery you fall straight through. These are
      // the scan's own shape, so a cupboard is a cupboard.
      const solid = sceneCollision(
        aligned.centers,
        aligned.count,
        cloud.opacities,
        alignment.groundHeight,
      );
      const obstacles = [...room, ...solid];

      // The floor and every worktop. A segmentation flood that crosses one of these
      // leaves through the floor and comes back with the entire room.
      this.surfaces = [
        alignment.groundHeight,
        ...room
          .filter((o) => o.kind === "surface")
          .map((o) => o.position[2] + o.halfExtents[2]),
      ];

      if (import.meta.env.DEV) {
        console.debug(
          `rsrsplat: room is ${obstacles.length} static geoms ` +
            `(${room.length} wall/surface, ${solid.length} from the scan)`,
        );
      }

      this.emit({
        type: "scene.load",
        splatId: file.name,
        splatCount: cloud.count,
        obstacles,
        world: {
          up: [alignment.up.x, alignment.up.y, alignment.up.z],
          groundHeight: alignment.groundHeight,
          // Already applied to the cloud, so the wire carries the identity the service
          // validates for. See DECISIONS.md.
          sceneScale: 1,
        },
      });
    } catch (error) {
      useScene
        .getState()
        .fail(error instanceof Error ? error.message : "could not read that file");
    }
  }

  /**
   * Point the camera at what was just loaded, using a robust extent.
   *
   * Framing on the bounding box puts the room at a speck: floaters inflate it threefold.
   */
  private frameCamera(): void {
    if (!this.cloud) return;
    const axes: number[][] = [[], [], []];
    const stride = Math.max(1, Math.floor(this.cloud.count / 20_000));
    for (let i = 0; i < this.cloud.count; i += stride) {
      axes[0].push(this.cloud.centers[i * 3]);
      axes[1].push(this.cloud.centers[i * 3 + 1]);
      axes[2].push(this.cloud.centers[i * 3 + 2]);
    }

    const centre = new THREE.Vector3();
    const span = new THREE.Vector3();
    for (let a = 0; a < 3; a++) {
      const values = axes[a].sort((x, y) => x - y);
      const lo = values[Math.floor(values.length * 0.02)];
      const hi = values[Math.floor(values.length * 0.98)];
      centre.setComponent(a, (lo + hi) / 2);
      span.setComponent(a, hi - lo);
    }

    const radius = Math.max(span.x, span.y, span.z) * 0.5 || 2;
    this.controls.target.copy(centre);
    this.camera.position.set(centre.x, centre.y - radius * 1.6, centre.z + radius * 0.35);
    this.controls.update();
  }

  // -- grabbing -------------------------------------------------------------------------

  /**
   * Try to pick up whatever is under the cursor.
   *
   * Returns true if something was grabbed, in which case the caller must not orbit. A plain
   * drag over empty room still orbits: grabbing takes no modifier because it only triggers
   * when the ray actually lands on something that can be picked up, and needing a chord to
   * touch an object you can see would be worse than the ambiguity it avoids.
   */
  grab(x: number, y: number): boolean {
    if (!this.draggable.size) return false;

    const hit = this.pickBody(x, y);
    if (!hit || !this.draggable.has(hit.bodyName)) return false;

    // Drag in the plane through the grab point facing the camera, so the object tracks the
    // cursor without diving toward or away from the viewer.
    const normal = new THREE.Vector3();
    this.camera.getWorldDirection(normal);
    this.grabbed = {
      bodyName: hit.bodyName,
      plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, hit.point),
    };
    this.controls.enabled = false;
    useScene.getState().setGrabbed(hit.bodyName);
    return true;
  }

  moveGrab(x: number, y: number): void {
    if (!this.grabbed) return;
    const at = this.onPlane(x, y, this.grabbed.plane);
    if (at) this.emit({ type: "body.drag", bodyName: this.grabbed.bodyName, target: [at.x, at.y, at.z] });
  }

  releaseGrab(): void {
    if (!this.grabbed) return;
    // Null releases. The body keeps whatever momentum the drag gave it, which is what makes
    // letting go a throw rather than a drop.
    this.emit({ type: "body.drag", bodyName: this.grabbed.bodyName, target: null });
    this.grabbed = null;
    this.controls.enabled = true;
    useScene.getState().setGrabbed(null);
  }

  get isGrabbing(): boolean {
    return this.grabbed !== null;
  }

  /** Which bound body is under the cursor, if any. */
  private pickBody(x: number, y: number): { bodyName: string; point: THREE.Vector3 } | null {
    const { clientWidth: w, clientHeight: h } = this.container;
    this.raycaster.setFromCamera(new THREE.Vector2((x / w) * 2 - 1, -((y / h) * 2 - 1)), this.camera);

    const meshes = [...this.meshFor.entries()];
    const hits = this.raycaster.intersectObjects(
      meshes.map(([, mesh]) => mesh),
      false,
    );
    if (!hits.length) return null;

    const found = meshes.find(([, mesh]) => mesh === hits[0].object);
    return found ? { bodyName: found[0], point: hits[0].point.clone() } : null;
  }

  /** Where the cursor lands on a plane in the scene. */
  private onPlane(x: number, y: number, plane: THREE.Plane): THREE.Vector3 | null {
    const { clientWidth: w, clientHeight: h } = this.container;
    this.raycaster.setFromCamera(new THREE.Vector2((x / w) * 2 - 1, -((y / h) * 2 - 1)), this.camera);
    const at = new THREE.Vector3();
    return this.raycaster.ray.intersectPlane(plane, at) ? at : null;
  }

  // -- selection ------------------------------------------------------------------------

  beginDrag(x: number, y: number): void {
    if (!this.cloud) return;
    this.dragFrom = { x, y };
    this.controls.enabled = false; // orbiting and selecting at once helps nobody
    useScene.getState().setDrag(true, 0);
  }

  updateDrag(x: number, y: number): ScreenRect | null {
    if (!this.dragFrom || !this.cloud) return null;
    const { clientWidth: w, clientHeight: h } = this.container;
    this.dragRect = rectFromPointers(this.dragFrom, { x, y }, w, h);

    // A stride here, exact on release. Projecting 1.5M centres every pointermove would drop
    // the frame rate exactly while the user is judging what they have caught.
    const { indices } = pick(
      this.cloud.centers,
      this.cloud.opacities,
      this.cloud.count,
      this.camera,
      this.dragRect,
      { stride: DRAG_STRIDE },
    );
    useScene.getState().setDrag(true, indices.length * DRAG_STRIDE);
    return this.dragRect;
  }

  endDrag(): void {
    const rect = this.dragRect;
    this.dragFrom = null;
    this.dragRect = null;
    this.controls.enabled = true;

    const store = useScene.getState();
    store.setDrag(false, 0);
    if (!rect || !this.cloud) return;

    // The tiny rectangle a click produces is not a selection.
    if (Math.abs(rect.x1 - rect.x0) < 0.01 && Math.abs(rect.y1 - rect.y0) < 0.01) {
      this.clearSelection();
      return;
    }

    void this.commitSelection(rect);
  }

  /**
   * Turn a finished drag into a selection.
   *
   * Deliberately does NOT run the model. Segmentation happens when the user names the thing,
   * because the sentence is half the prompt -- see `physicalize`. What happens here is the
   * cheap rectangle path, so the highlight and the count appear the instant the mouse comes
   * up, and a freeze-frame of the view is taken for the model to look at later.
   */
  private async commitSelection(rect: ScreenRect): Promise<void> {
    if (!this.cloud) return;

    const { indices } = pick(
      this.cloud.centers,
      this.cloud.opacities,
      this.cloud.count,
      this.camera,
      rect,
    );
    if (indices.length < 32) {
      this.clearSelection();
      return;
    }

    // The rectangle is a HINT: it caught part of the object and MISSED whatever fell outside
    // the box. Grow it into something plausible, so that a selection made with the sidecar
    // stopped is still usable.
    const hint = measureFrame(
      this.cloud.centers,
      indices,
      new THREE.Vector3(0, 0, 1),
      this.camera.position,
    );
    const grown = grow(
      voxelise(this.cloud.centers, this.cloud.count, hint),
      this.cloud.centers,
      indices,
      hint,
      { surfaces: this.surfaces },
    );
    const escaped = grown.escaped || grown.indices.length < 32;
    const owned = escaped ? Uint32Array.from(indices) : grown.indices;
    const frame = escaped
      ? hint
      : measureFrame(this.cloud.centers, owned, new THREE.Vector3(0, 0, 1), this.camera.position);

    // The view as it was when the rectangle was drawn, and the camera that drew it. The user
    // is about to type, and may orbit while doing so; a mask computed from one camera and
    // applied to another is nonsense that looks like the model being wrong.
    this.pending = {
      rect,
      camera: this.camera.clone(),
      view: await captureView(this.renderer.domElement),
    };

    this.selectionId = `sel_${(this.counter += 1)}`;
    this.applySelection(owned, frame, indices.length, escaped ? "rectangle (grow escaped)" : "rectangle");

    useScene.getState().setPrompt({
      kind: "asking",
      selectionId: this.selectionId,
      splatCount: owned.length,
    });
  }

  /**
   * Adopt a set of splats as the selection: measure its shape, show it, and tell the service.
   *
   * Shared by the rectangle path and the mask path, and re-run when the mask replaces the
   * rectangle's answer. The selection id is deliberately NOT changed on a re-run: the service
   * keys selections by id, so committing again simply corrects the one it already has.
   */
  private applySelection(
    owned: Uint32Array,
    frame: MeasuredFrame,
    fromCount: number,
    via: string,
  ): void {
    if (!this.cloud || !this.selectionId) return;

    // The collision shape, from the cells the owned splats actually occupy. Measured in the
    // frame the object ENDED UP with: a grid built around an earlier frame offsets every box
    // by however far the centroid moved.
    const grid = voxelise(this.cloud.centers, this.cloud.count, frame);
    const shape: ShapeBox[] = toBoxes(grid, cellsOf(grid, this.cloud.centers, frame, owned));

    this.selectedIndices = owned;
    this.selectedFrame = frame;
    this.showSelection(frame);

    if (import.meta.env.DEV) {
      console.debug(
        `rsrsplat: selection via ${via} — ${fromCount} -> ${owned.length} splats, ` +
          `${shape.length} collision boxes`,
      );
    }

    this.emit({
      type: "selection.commit",
      selection: toSelection(this.selectionId, frame, owned.length, shape),
    });
  }

  /**
   * Tint the selection and dim everything else, on the GPU.
   *
   * Two box edits rather than per-splat colour writes: recolouring 1.5M splats on the CPU
   * takes long enough to feel like a hang, and the selection has to appear on release.
   */
  private showSelection(frame: MeasuredFrame): void {
    if (!this.staticMesh) return;
    this.clearHighlight();

    const place = (box: SplatEditSdf) => {
      box.position.copy(frame.centroid);
      box.quaternion.copy(orientationOf(frame));
      box.scale.copy(frame.halfExtents);
    };

    // Everything outside the box loses most of its opacity.
    this.dimmer = new SplatEdit({ rgbaBlendMode: SplatEditRgbaBlendMode.MULTIPLY, softEdge: 0 });
    const outside = new SplatEditSdf({ type: SplatEditSdfType.BOX, invert: true, opacity: DIM });
    place(outside);
    this.dimmer.addSdf(outside);
    this.staticMesh.add(this.dimmer);

    // And what is inside takes the accent, which means exactly one thing: this is selected.
    this.highlight = new SplatEdit({ rgbaBlendMode: SplatEditRgbaBlendMode.SET_RGB, softEdge: 0 });
    const inside = new SplatEditSdf({
      type: SplatEditSdfType.BOX,
      color: new THREE.Color("#d2ff3a"),
      opacity: 1,
    });
    place(inside);
    this.highlight.addSdf(inside);
    this.staticMesh.add(this.highlight);
  }

  private clearHighlight(): void {
    if (!this.staticMesh) return;
    if (this.highlight) this.staticMesh.remove(this.highlight);
    if (this.dimmer) this.staticMesh.remove(this.dimmer);
    this.highlight = null;
    this.dimmer = null;
  }

  clearSelection(): void {
    this.clearHighlight();
    this.selectedIndices = null;
    this.selectedFrame = null;
    this.selectionId = null;
    useScene.getState().setPrompt({ kind: "idle" });
  }

  // -- physicalize ----------------------------------------------------------------------

  /**
   * The height of the surface this selection is standing on.
   *
   * The highest detected surface at or just below the object's underside. "Just below"
   * matters: the underside is measured from splats and sits a centimetre or two proud of the
   * thing it rests on, so an exact test finds nothing and the hole is filled at floor level
   * underneath a desk.
   */
  private restingSurface(frame: MeasuredFrame): number | null {
    const bottom = frame.centroid.z - frame.halfExtents.z;
    let best: number | null = null;
    for (const z of this.surfaces) {
      if (z > bottom + 0.08) continue;
      if (best === null || z > best) best = z;
    }
    return best;
  }

  /**
   * Name the selection, and make it real.
   *
   * The sentence does two jobs, and this is the order they have to happen in. First it tells
   * the segmentation model WHAT to look for, which turns the loose rectangle into an object
   * silhouette. Only then is it worth deciding what the object is made of, because the mass
   * and the joints are derived from half-extents that the silhouette has just corrected.
   *
   * Segmenting at drag time instead -- which is what this did first -- throws away the most
   * informative thing the user gives us. A box says which one; the sentence says what.
   */
  physicalize(prompt: string): void {
    if (!this.selectionId) {
      console.warn("rsrsplat: physicalize with no live selection");
      return;
    }
    useScene.getState().setPrompt({ kind: "pending", selectionId: this.selectionId, prompt });
    void this.segmentThenPhysicalize(prompt);
  }

  private async segmentThenPhysicalize(prompt: string): Promise<void> {
    if (!this.cloud || !this.selectionId) return;

    // Refine the selection with the model, now that there is a sentence to give it.
    const pending = this.pending;
    if (pending?.view) {
      const mask = await segmentView(pending.view, pending.rect, prompt);
      if (mask) {
        const { indices } = pick(
          this.cloud.centers,
          this.cloud.opacities,
          this.cloud.count,
          // The camera the rectangle was drawn with, NOT the one now: the user may well have
          // orbited while typing, and the mask belongs to the frozen view.
          pending.camera,
          pending.rect,
          { mask },
        );
        if (indices.length >= 32) {
          const owned = Uint32Array.from(indices);
          // No grow. The model has already said what the object is; dilating its answer only
          // walks out into whatever the object is resting on.
          const frame = measureFrame(
            this.cloud.centers,
            owned,
            new THREE.Vector3(0, 0, 1),
            pending.camera.position,
          );
          this.applySelection(owned, frame, indices.length, `SAM, box from ${lastBoxSource || "drag"}`);
        } else if (import.meta.env.DEV) {
          console.debug("rsrsplat: mask matched too few splats, keeping the rectangle");
        }
      } else if (import.meta.env.DEV) {
        console.debug("rsrsplat: no mask (sidecar down or empty), keeping the rectangle");
      }
    }
    this.pending = null;

    // Close the hole this is about to open. Computed here rather than after the object
    // arrives, so its collision reaches the service in the same message: a floor that is
    // whole one frame later is a floor something has already started falling through.
    this.patch = null;
    const surface = this.selectedFrame ? this.restingSurface(this.selectedFrame) : null;
    if (this.selectedIndices && surface !== null) {
      const patch = healSurface(this.cloud, this.selectedIndices, surface, {
        id: `patch_${this.selectionId}`,
      });
      if (patch.healed) this.patch = patch;
      if (import.meta.env.DEV) {
        console.debug(
          patch.healed
            ? `rsrsplat: healing the hole at z=${surface.toFixed(2)} with ${patch.count} splats, ${patch.obstacles.length} boxes`
            : `rsrsplat: no flat surface under the selection, leaving the hole`,
        );
      }
    }

    this.emit({
      type: "object.physicalize",
      selectionId: this.selectionId,
      prompt,
      obstacles: this.patch?.obstacles ?? [],
    });
  }

  /** Drive a joint. Wire units: degrees for a hinge, metres for a slide. */
  setJoint(bodyName: string, value: number): void {
    this.emit({ type: "joint.set", bodyName, value });
  }

  control(action: "play" | "pause" | "reset"): void {
    this.emit({ type: "sim.control", action });
  }

  remove(objectId: string): void {
    this.emit({ type: "object.remove", objectId });
    this.unbindOne(objectId);
    useScene.getState().removeObject(objectId);
  }

  // -- messages -------------------------------------------------------------------------

  /** Send, and say so. A message that never went is indistinguishable from one ignored. */
  private emit(message: ClientMessage): void {
    if (import.meta.env.DEV) console.debug("rsrsplat ->", message.type, message);
    if (!this.service) {
      console.warn("rsrsplat: no service yet, dropping", message.type);
      return;
    }
    this.service.send(message);
  }

  private receive(message: ServerMessage): void {
    if (import.meta.env.DEV) console.debug("rsrsplat <-", message.type, message);
    const store = useScene.getState();
    switch (message.type) {
      case "scene.ready":
        return;
      case "sim.status":
        return store.setRunning(message.running);
      case "object.failed":
        if (message.selectionId === "protocol") {
          // Not about any selection: the service could not parse something we sent, which
          // in practice means the two sides are running different versions of the contract.
          console.error("rsrsplat:", message.reason);
          return store.fail(message.reason);
        }
        return store.setPrompt({
          kind: "failed",
          selectionId: message.selectionId,
          reason: message.reason,
        });
      case "object.created":
        try {
          return this.onCreated(message.object);
        } catch (error) {
          // Binding is the payoff, so a failure here is the one thing that must never be
          // swallowed. Without this the object arrives, throws inside a socket handler, and
          // the interface simply sits there.
          const reason = error instanceof Error ? error.message : String(error);
          console.error("rsrsplat: binding failed", error);
          return store.setPrompt({
            kind: "failed",
            selectionId: message.object.selectionId,
            reason: `built, but could not bind its splats: ${reason}`,
          });
        }
      case "pose.batch":
        this.stream.push(message);
        return;
    }
  }

  private onCreated(object: PhysicsObject): void {
    if (!this.cloud || !this.staticMesh || !this.selectedIndices || !this.selectedFrame) return;

    const bound = bind(this.cloud, this.selectedIndices, this.selectedFrame, object);
    for (const part of bound.parts) {
      this.scene.add(part.mesh);
      this.meshFor.set(part.bodyName, part.mesh);
    }
    // Only a free body can be picked up. A plumbed-in dishwasher is worked with its joint
    // sliders, and letting someone drag it across the kitchen would be a lie about it.
    for (const part of object.parts) {
      if (part.jointType === "free") this.draggable.add(part.bodyName);
    }
    this.bound.set(object.id, bound);

    // The static scene, minus exactly this object's splats, plus the patch that closes the
    // hole they leave. `bind` produced the remainder in the same pass it used to gather the
    // parts, so this costs nothing beyond swapping the mesh.
    if (this.patch) mergeInto(bound.remaining, this.patch.splats);
    this.patch = null;
    this.replaceStatic(bound.remaining);

    // The selection has become an object, so it stops being a selection.
    this.clearHighlight();
    this.selectedIndices = null;
    this.selectedFrame = null;
    this.selectionId = null;

    useScene.getState().addObject(object);
  }

  private unbindOne(objectId: string): void {
    const bound = this.bound.get(objectId);
    if (!bound) return;
    for (const part of bound.parts) {
      this.meshFor.delete(part.bodyName);
      this.stream.forget(part.bodyName);
      this.draggable.delete(part.bodyName);
    }
    unbind(bound, this.scene);
    this.bound.delete(objectId);
    // Removing an object puts its splats back, so the static cloud is rebuilt from the
    // original minus whatever is still bound. Only on remove, which is rare.
    this.rebuildStatic();
  }

  /** Swap in a new static cloud, disposing the old mesh. */
  private replaceStatic(packed: PackedSplats): void {
    const mesh = new SplatMesh({ packedSplats: packed, editable: true });
    void mesh.initialized.then(() => {
      if (this.staticMesh) {
        this.scene.remove(this.staticMesh);
        this.staticMesh.dispose();
      }
      this.scene.add(mesh);
      this.staticMesh = mesh;
    });
  }

  /** The original cloud minus every splat currently owned by a bound object. */
  private rebuildStatic(): void {
    if (!this.cloud) return;
    const removed = new Uint8Array(this.cloud.count);
    for (const bound of this.bound.values()) {
      for (const i of bound.removed) removed[i] = 1;
    }

    const packed = new PackedSplats();
    const centre = new THREE.Vector3();
    const scales = new THREE.Vector3();
    const rotation = new THREE.Quaternion();
    const colour = new THREE.Color();
    this.cloud.packed.forEachSplat((i, c, sc, q, opacity, col) => {
      if (removed[i]) return;
      packed.pushSplat(
        centre.copy(c),
        scales.copy(sc),
        rotation.copy(q),
        opacity,
        colour.copy(col),
      );
    });
    this.replaceStatic(packed);
  }

  private clearBindings(): void {
    for (const id of [...this.bound.keys()]) this.unbindOne(id);
    this.stream.clear();
    this.meshFor.clear();
  }

  // -- the frame loop -------------------------------------------------------------------

  private frames = 0;
  private sinceReport = 0;
  private last = performance.now();

  private tick = (): void => {
    this.raf = requestAnimationFrame(this.tick);
    const now = performance.now();
    const dt = now - this.last;
    this.last = now;

    // Interpolate between buffered poses by ELAPSED TIME, rather than easing toward the
    // newest one. Easing is a low-pass filter: it damps acceleration and smears impacts, and
    // it is what made a falling crate look like it was descending through syrup.
    this.stream.advance(dt / 1000);
    for (const [bodyName, mesh] of this.meshFor) {
      if (this.stream.sample(bodyName, SAMPLED_POSITION, SAMPLED_ROTATION)) {
        mesh.position.copy(SAMPLED_POSITION);
        mesh.quaternion.copy(SAMPLED_ROTATION);
      }
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);

    this.frames += 1;
    this.sinceReport += dt;
    if (this.sinceReport >= PERF_INTERVAL_MS) {
      useScene.getState().setPerf(
        (this.frames * 1000) / this.sinceReport,
        this.sinceReport / this.frames,
      );
      this.frames = 0;
      this.sinceReport = 0;
    }
  };
}

const SAMPLED_POSITION = new THREE.Vector3();
const SAMPLED_ROTATION = new THREE.Quaternion();
