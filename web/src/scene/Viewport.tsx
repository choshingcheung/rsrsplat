/**
 * The viewport: a splat scan, full-bleed, orbited freely.
 *
 * Three.js and Spark live entirely outside React. The scene graph is mutable, sixty-times-a-
 * second state, and reconciling it through a component tree would be both slower and harder
 * to reason about. React owns the chrome; this owns the pixels; the store carries the few
 * numbers that cross between them.
 */

import { SparkRenderer, SplatMesh } from "@sparkjsdev/spark";
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { useScene } from "../store/scene";

/** How often the frame counter reaches React. Every frame would re-render the tree at 60 Hz. */
const PERF_INTERVAL_MS = 500;

export function Viewport() {
  const host = useRef<HTMLDivElement>(null);
  const meshRef = useRef<SplatMesh | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);

  // --- the renderer, created once ------------------------------------------------------
  useEffect(() => {
    const container = host.current;
    if (!container) return;

    const renderer = new THREE.WebGLRenderer({
      // Spark's own guidance: WebGL anti-aliasing does nothing for Gaussian splatting and
      // costs a great deal of performance.
      antialias: false,
      powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    sceneRef.current = scene;

    const camera = new THREE.PerspectiveCamera(
      55,
      container.clientWidth / container.clientHeight,
      0.05,
      500,
    );
    camera.position.set(0, 0, 4);

    const spark = new SparkRenderer({ renderer });
    scene.add(spark);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    // Damping that decelerates rather than glides: the whole interface claims to be an
    // instrument with mass, and the camera is the largest moving thing in it.
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.65;
    controls.zoomSpeed = 0.8;
    controlsRef.current = controls;

    const onResize = () => {
      const { clientWidth: w, clientHeight: h } = container;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(container);

    let raf = 0;
    let frames = 0;
    let sinceReport = 0;
    let last = performance.now();

    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      const dt = now - last;
      last = now;

      controls.update();
      renderer.render(scene, camera);

      frames += 1;
      sinceReport += dt;
      if (sinceReport >= PERF_INTERVAL_MS) {
        useScene.getState().setPerf((frames * 1000) / sinceReport, sinceReport / frames);
        frames = 0;
        sinceReport = 0;
      }
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      container.removeChild(renderer.domElement);
      sceneRef.current = null;
      controlsRef.current = null;
    };
  }, []);

  // --- loading a dropped capture -------------------------------------------------------
  useEffect(() => {
    const container = host.current;
    if (!container) return;

    const stop = (event: DragEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };

    const onDrop = async (event: DragEvent) => {
      stop(event);
      const file = event.dataTransfer?.files?.[0];
      if (!file) return;

      const store = useScene.getState();
      store.beginReading(file.name);

      try {
        const fileBytes = new Uint8Array(await file.arrayBuffer());
        const scene = sceneRef.current;
        if (!scene) return;

        if (meshRef.current) {
          scene.remove(meshRef.current);
          meshRef.current.dispose();
          meshRef.current = null;
        }

        const mesh = new SplatMesh({
          fileBytes,
          fileName: file.name,
          onProgress: (progressEvent) => {
            if (progressEvent.lengthComputable && progressEvent.total > 0) {
              store.setProgress(progressEvent.loaded / progressEvent.total);
            }
          },
        });
        await mesh.initialized;

        // A trained capture arrives in whatever frame reconstruction happened to pick, and
        // is usually upside down relative to a y-up renderer. Aligning it properly is A3's
        // ground fit; this is the standard 3DGS flip so the first frame is not inverted.
        mesh.quaternion.set(1, 0, 0, 0);

        scene.add(mesh);
        meshRef.current = mesh;

        frameCamera(mesh, controlsRef.current);
        store.ready(mesh.packedSplats?.numSplats ?? 0);
      } catch (error) {
        useScene
          .getState()
          .fail(error instanceof Error ? error.message : "could not read that file");
      }
    };

    container.addEventListener("dragover", stop);
    container.addEventListener("dragenter", stop);
    container.addEventListener("drop", onDrop);
    return () => {
      container.removeEventListener("dragover", stop);
      container.removeEventListener("dragenter", stop);
      container.removeEventListener("drop", onDrop);
    };
  }, []);

  return <div ref={host} className="absolute inset-0" />;
}

/**
 * Point the camera at what was just loaded.
 *
 * Uses a robust extent rather than the bounding box. A trained scene carries floaters —
 * stray Gaussians far outside the room — and on the playroom capture they inflate the raw
 * box threefold. Framing on that puts the camera so far back the room is a speck.
 */
function frameCamera(mesh: SplatMesh, controls: OrbitControls | null) {
  if (!controls) return;

  const samples: number[][] = [[], [], []];
  const stride = Math.max(1, Math.floor((mesh.packedSplats?.numSplats ?? 1) / 20_000));
  mesh.forEachSplat((index, center) => {
    if (index % stride !== 0) return;
    samples[0].push(center.x);
    samples[1].push(center.y);
    samples[2].push(center.z);
  });

  const centre = new THREE.Vector3();
  const span = new THREE.Vector3();
  for (let axis = 0; axis < 3; axis++) {
    const values = samples[axis].sort((a, b) => a - b);
    if (!values.length) continue;
    const lo = values[Math.floor(values.length * 0.02)];
    const hi = values[Math.floor(values.length * 0.98)];
    centre.setComponent(axis, (lo + hi) / 2);
    span.setComponent(axis, hi - lo);
  }

  const radius = Math.max(span.x, span.y, span.z) * 0.5 || 2;
  controls.target.copy(centre);
  controls.object.position.set(centre.x, centre.y + radius * 0.15, centre.z + radius * 1.9);
  controls.update();
}
