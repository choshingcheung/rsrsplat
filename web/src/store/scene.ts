/**
 * What the interface knows about the scene.
 *
 * Deliberately small. The heavy things — the Gaussians, the Three.js objects, the splat
 * meshes — live in the scene controller and are never put in here: a store holding a million
 * splats would re-render the whole tree every time a frame counter ticked. What crosses into
 * React is the handful of facts the chrome actually displays.
 */

import { create } from "zustand";

import type { PhysicsObject } from "../types/protocol";

export type Phase = "empty" | "reading" | "ready" | "failed";

/** Where a physicalize request has got to. */
export type PromptState =
  | { kind: "idle" }
  | { kind: "asking"; selectionId: string; splatCount: number }
  | { kind: "pending"; selectionId: string; prompt: string }
  | { kind: "failed"; selectionId: string; reason: string };

export interface SceneState {
  phase: Phase;
  fileName: string | null;
  splatCount: number;
  progress: number;
  error: string | null;

  /** Live rendering numbers, pushed from the frame loop rather than pulled by React. */
  fps: number;
  frameMs: number;

  /** Which service is answering: the Python one, or the in-browser stand-in. */
  transport: "service" | "local" | null;
  running: boolean;

  /** How many splats are inside the drag rectangle right now. */
  hoverCount: number;
  dragging: boolean;

  /** The body currently being held, so the interface can say so. */
  grabbed: string | null;

  prompt: PromptState;
  objects: PhysicsObject[];

  beginReading: (fileName: string) => void;
  setProgress: (progress: number) => void;
  ready: (splatCount: number) => void;
  fail: (error: string) => void;
  setPerf: (fps: number, frameMs: number) => void;
  setTransport: (transport: "service" | "local") => void;
  setRunning: (running: boolean) => void;
  setDrag: (dragging: boolean, hoverCount: number) => void;
  setGrabbed: (bodyName: string | null) => void;
  setPrompt: (prompt: PromptState) => void;
  addObject: (object: PhysicsObject) => void;
  removeObject: (id: string) => void;
  reset: () => void;
}

const EMPTY = {
  phase: "empty" as Phase,
  fileName: null,
  splatCount: 0,
  progress: 0,
  error: null,
  fps: 0,
  frameMs: 0,
  transport: null,
  running: true,
  hoverCount: 0,
  dragging: false,
  grabbed: null,
  prompt: { kind: "idle" } as PromptState,
  objects: [] as PhysicsObject[],
};

export const useScene = create<SceneState>((set) => ({
  ...EMPTY,

  beginReading: (fileName) =>
    set({ phase: "reading", fileName, progress: 0, error: null, splatCount: 0, objects: [] }),
  setProgress: (progress) => set({ progress }),
  ready: (splatCount) => set({ phase: "ready", splatCount, progress: 1 }),
  fail: (error) => set({ phase: "failed", error }),
  setPerf: (fps, frameMs) => set({ fps, frameMs }),
  setTransport: (transport) => set({ transport }),
  setRunning: (running) => set({ running }),
  setDrag: (dragging, hoverCount) => set({ dragging, hoverCount }),
  setGrabbed: (grabbed) => set({ grabbed }),
  setPrompt: (prompt) => set({ prompt }),
  addObject: (object) =>
    set((s) => ({ objects: [...s.objects, object], prompt: { kind: "idle" } })),
  removeObject: (id) => set((s) => ({ objects: s.objects.filter((o) => o.id !== id) })),
  reset: () => set(EMPTY),
}));
