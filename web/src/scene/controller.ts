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
import { measureFrame, toSelection, type MeasuredFrame } from "../selection/frame";
import { pick, rectFromPointers, type ScreenRect } from "../selection/pick";
import { useScene } from "../store/scene";
import type { ClientMessage, PhysicsObject, PoseUpdate, ServerMessage } from "../types/protocol";
import { bind, easePose, orientationOf, unbind, type BoundObject } from "./binding";
import { alignScene, roomObstacles } from "./ground";
import { applyAlignment, readPly, type SplatCloud } from "./splats";

/** How often the frame counter reaches React. Every frame would re-render the tree at 60 Hz. */
const PERF_INTERVAL_MS = 500;

/** Sampling during a drag. The commit always runs at stride 1. */
const DRAG_STRIDE = 12;

/** How much of the gap to a new pose to close each frame. See `easePose`. */
const POSE_EASE = 0.35;

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
  private targets = new Map<string, PoseUpdate>();

  private counter = 0;

  constructor(private container: HTMLDivElement) {
    this.renderer = new THREE.WebGLRenderer({
      // Spark's own guidance: WebGL anti-aliasing does nothing for Gaussian splatting and
      // costs a great deal of performance.
      antialias: false,
      powerPreference: "high-performance",
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
      const obstacles = roomObstacles(aligned.centers, aligned.count, alignment.groundHeight);

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
    if (!rect || !this.cloud || !this.service) return;

    // The tiny rectangle a click produces is not a selection.
    if (Math.abs(rect.x1 - rect.x0) < 0.01 && Math.abs(rect.y1 - rect.y0) < 0.01) {
      this.clearSelection();
      return;
    }

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

    const frame = measureFrame(
      this.cloud.centers,
      indices,
      new THREE.Vector3(0, 0, 1),
      this.camera.position,
    );

    this.selectedIndices = Uint32Array.from(indices);
    this.selectedFrame = frame;
    this.selectionId = `sel_${(this.counter += 1)}`;
    this.showSelection(frame);

    this.emit({
      type: "selection.commit",
      selection: toSelection(this.selectionId, frame, indices.length),
    });
    store.setPrompt({
      kind: "asking",
      selectionId: this.selectionId,
      splatCount: indices.length,
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

  physicalize(prompt: string): void {
    if (!this.selectionId) {
      console.warn("rsrsplat: physicalize with no live selection");
      return;
    }
    useScene.getState().setPrompt({ kind: "pending", selectionId: this.selectionId, prompt });
    this.emit({ type: "object.physicalize", selectionId: this.selectionId, prompt });
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
        for (const pose of message.poses) this.targets.set(pose.bodyName, pose);
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
    this.bound.set(object.id, bound);

    // The static scene, minus exactly this object's splats. `bind` produced it in the same
    // pass it used to gather the parts, so this costs nothing beyond swapping the mesh.
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
      this.targets.delete(part.bodyName);
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
    this.targets.clear();
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

    // Ease toward the latest pose rather than snapping to it. Thirty hertz of physics
    // snapped onto sixty frames reads as judder, which looks like a low frame rate.
    for (const [bodyName, pose] of this.targets) {
      const mesh = this.meshFor.get(bodyName);
      if (mesh) easePose(mesh, pose, POSE_EASE);
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
