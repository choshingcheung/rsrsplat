/**
 * What the interface knows about the scene.
 *
 * Deliberately small. The heavy things — the Gaussians, the Three.js objects — live in the
 * viewport and are never put in here: a store holding a million splats would re-render the
 * whole tree every time a frame counter ticked. What crosses into React is the handful of
 * facts the chrome actually displays.
 */

import { create } from "zustand";

export type Phase = "empty" | "reading" | "ready" | "failed";

export interface SceneState {
  phase: Phase;
  /** The dropped file's name, shown as the scene's identity. */
  fileName: string | null;
  /** Gaussians in the loaded capture. Cross-checked against the Python reference parser. */
  splatCount: number;
  /** Bytes read so far, over total, while parsing. A 371 MB drop is not instantaneous. */
  progress: number;
  error: string | null;

  /** Live rendering numbers, updated from the frame loop rather than from React. */
  fps: number;
  frameMs: number;

  /** Connection to the physics service. Absent is a state to show, not an error to throw. */
  connected: boolean;

  beginReading: (fileName: string) => void;
  setProgress: (progress: number) => void;
  ready: (splatCount: number) => void;
  fail: (error: string) => void;
  setPerf: (fps: number, frameMs: number) => void;
  setConnected: (connected: boolean) => void;
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
  connected: false,
};

export const useScene = create<SceneState>((set) => ({
  ...EMPTY,

  beginReading: (fileName) =>
    set({ phase: "reading", fileName, progress: 0, error: null, splatCount: 0 }),
  setProgress: (progress) => set({ progress }),
  ready: (splatCount) => set({ phase: "ready", splatCount, progress: 1 }),
  fail: (error) => set({ phase: "failed", error }),
  setPerf: (fps, frameMs) => set({ fps, frameMs }),
  setConnected: (connected) => set({ connected }),
  reset: () => set(EMPTY),
}));
