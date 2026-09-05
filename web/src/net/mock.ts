/**
 * A physics service that lives in the browser.
 *
 * **This is not scaffolding.** It is the demo's insurance policy. If the venue network dies,
 * or Python does not start on an unfamiliar machine, or the socket drops thirty seconds
 * before the demo, one flag still leaves something that falls over when you push it. A
 * fallback you only wrote in order to delete it is a fallback nobody has run.
 *
 * It is also what lets the app track be built without waiting for the spine: every message
 * the real service sends, this sends, in the same shapes, validated against the same
 * fixtures.
 *
 * What it is NOT is a physics engine. It integrates a point mass under gravity with a floor,
 * and it rotates a hinge through its range on a spring. That is enough to make a crate
 * settle and a door swing, and it is nowhere near enough to be mistaken for MuJoCo. Anything
 * that needs to be true about the physics belongs on the Python side.
 */

import type {
  ClientMessage,
  PhysicsObject,
  PhysicsPart,
  PoseUpdate,
  Obstacle,
  Selection,
  ServerMessage,
  Vec3,
  WorldFrame,
} from "../types/protocol";

const GRAVITY = 9.81;
const TIMESTEP = 1 / 120;
/** Matches the real service. The client interpolates between these to reach 60 fps. */
const BROADCAST_HZ = 30;

/** How hard a body loses speed when it lands. Not restitution physics; just "it stops". */
const LANDING_DAMPING = 0.35;

/**
 * Time constant for a door swinging open, seconds.
 *
 * An exponential approach rather than a spring: it cannot oscillate, it settles predictably,
 * and it is what a real appliance door on a damper actually does. A spring stiff enough to
 * arrive within a second was also stiff enough to overshoot, and one soft enough not to was
 * still visibly creeping after three.
 */
const HINGE_TAU = 0.22;

interface Body {
  name: string;
  position: [number, number, number];
  velocity: [number, number, number];
  orientation: [number, number, number, number];
  /** Half-height, so the body rests with its base on the floor rather than its centre. */
  halfHeight: number;
  free: boolean;
  /** Radians, for a hinge. */
  angle: number;
  hingeRange: [number, number] | null;
  hingeAxis: Vec3;
  /** Where this body started, so reset can actually put it back. Both are needed: restoring
   *  the orientation alone leaves a crate wherever it had fallen to. */
  restPosition: [number, number, number];
  restQuaternion: [number, number, number, number];
}

/** Keyword matching, mirroring `service/app/schema/fallback.py` closely enough to be useful. */
const KNOWN_HINGED = /dishwasher|oven|microwave|fridge|washer|washing machine|bin|lid|door|cabinet/i;
const HEAVY = /heavy|solid|steel|concrete|stone|dense|massive/i;
const LIGHT = /light|empty|hollow|foam|cardboard|plastic/i;
const FITTED = /built.?in|fitted|installed|mounted|plumbed|bolted/i;

export interface MockOptions {
  /** Called with every message the service would have sent. */
  onMessage: (message: ServerMessage) => void;
}

export class MockService {
  private world: WorldFrame | null = null;
  /**
   * The room's solid geometry.
   *
   * Held but only used for the floor and worktops a falling body can land on. The mock is a
   * point mass with a floor, not a collision engine -- walls do nothing here, and a body
   * shoved sideways will leave the room. That is a real difference from the service, and the
   * readout says which one is answering.
   */
  private obstacles: readonly Obstacle[] = [];
  private selections = new Map<string, Selection>();
  private bodies: Body[] = [];
  private objects: PhysicsObject[] = [];
  // Not window.setInterval: the mock has to be drivable under fake timers in a test, and
  // a browser-only global would make its own insurance policy untestable.
  private timer: ReturnType<typeof setInterval> | null = null;
  private time = 0;
  private steps = 0;
  private running = true;
  private counter = 0;

  constructor(private options: MockOptions) {}

  send(message: ClientMessage): void {
    switch (message.type) {
      case "scene.load":
        return this.load(message.world, message.obstacles);
      case "selection.commit":
        this.selections.set(message.selection.id, message.selection);
        return;
      case "object.physicalize":
        return this.physicalize(message.selectionId, message.prompt);
      case "object.remove":
        return this.remove(message.objectId);
      case "sim.control":
        return this.control(message.action);
      case "mesh.attach":
        return; // Phase 9, on both sides
    }
  }

  stop(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  // -- messages -------------------------------------------------------------------------

  private load(world: WorldFrame, obstacles: readonly Obstacle[] = []) {
    this.world = world;
    this.obstacles = obstacles;
    this.selections.clear();
    this.bodies = [];
    this.objects = [];
    this.time = 0;
    this.steps = 0;
    this.running = true;

    this.emit({ type: "scene.ready", sessionId: `mock_${Math.random().toString(16).slice(2, 8)}` });
    this.emit({ type: "sim.status", running: true, stepCount: 0 });

    this.stop();
    this.timer = setInterval(() => this.tick(), 1000 / BROADCAST_HZ);
  }

  private physicalize(selectionId: string, prompt: string) {
    const selection = this.selections.get(selectionId);
    if (!this.world || !selection) {
      this.emit({
        type: "object.failed",
        selectionId,
        reason: !this.world
          ? "no scene loaded yet - send scene.load first"
          : `unknown selection '${selectionId}'; commit it first`,
      });
      return;
    }

    const id = `obj_mock${(this.counter += 1)}`;
    const label = labelFrom(prompt);
    const free = !FITTED.test(prompt) && !KNOWN_HINGED.test(prompt);
    const hinged = KNOWN_HINGED.test(prompt);

    const volume = 8 * selection.halfExtents[0] * selection.halfExtents[1] * selection.halfExtents[2];
    const density = HEAVY.test(prompt) ? 1000 : LIGHT.test(prompt) ? 140 : 400;
    const massKg = Math.min(500, Math.max(0.2, density * volume));

    const orientation = quaternionFromAxes(selection.axes);
    const shell: Body = {
      name: `${id}__${label.replace(/\s+/g, "_")}`,
      position: [...selection.centroid],
      velocity: [0, 0, 0],
      orientation,
      restPosition: [...selection.centroid],
      restQuaternion: orientation,
      halfHeight: selection.halfExtents[2],
      free,
      angle: 0,
      hingeRange: null,
      hingeAxis: [0, 1, 0],
    };
    this.bodies.push(shell);

    const parts: PhysicsPart[] = [
      {
        bodyName: shell.name,
        jointType: free ? "free" : "fixed",
        range: null,
        splatSubset: "all",
        initialPose: poseOf(shell),
      },
    ];

    if (hinged) {
      // The door's origin sits on the bottom front edge, exactly as the generator places it:
      // centroid + halfDepth * front - halfHeight * up.
      const front = [selection.axes[0], selection.axes[1], selection.axes[2]] as Vec3;
      const up = [selection.axes[6], selection.axes[7], selection.axes[8]] as Vec3;
      const h = selection.halfExtents;
      const door: Body = {
        name: `${id}__door`,
        position: [
          selection.centroid[0] + h[0] * front[0] - h[2] * up[0],
          selection.centroid[1] + h[0] * front[1] - h[2] * up[1],
          selection.centroid[2] + h[0] * front[2] - h[2] * up[2],
        ],
        velocity: [0, 0, 0],
        orientation,
        restPosition: [
          selection.centroid[0] + h[0] * front[0] - h[2] * up[0],
          selection.centroid[1] + h[0] * front[1] - h[2] * up[1],
          selection.centroid[2] + h[0] * front[2] - h[2] * up[2],
        ],
        restQuaternion: orientation,
        halfHeight: 0,
        free: false,
        angle: 0,
        hingeRange: [0, Math.PI / 2],
        hingeAxis: [0, 1, 0],
      };
      this.bodies.push(door);
      parts.push({
        bodyName: door.name,
        jointType: "hinge",
        range: [0, 90], // DEGREES on the wire, as everywhere else
        splatSubset: "front",
        initialPose: poseOf(door),
      });
    }

    const object: PhysicsObject = {
      id,
      label,
      selectionId,
      parts,
      massKg,
      friction: 0.6,
    };
    this.objects.push(object);
    this.emit({ type: "object.created", object });
  }

  private remove(objectId: string) {
    this.objects = this.objects.filter((o) => o.id !== objectId);
    this.bodies = this.bodies.filter((b) => !b.name.startsWith(`${objectId}__`));
    this.emit({ type: "sim.status", running: this.running, stepCount: this.steps });
  }

  private control(action: "play" | "pause" | "reset") {
    if (action === "play") this.running = true;
    if (action === "pause") this.running = false;
    if (action === "reset") {
      this.steps = 0;
      this.time = 0;
      for (const body of this.bodies) {
        body.position = [...body.restPosition];
        body.velocity = [0, 0, 0];
        body.angle = 0;
        body.orientation = body.restQuaternion;
      }
    }
    this.emit({ type: "sim.status", running: this.running, stepCount: this.steps });
  }

  // -- the loop -------------------------------------------------------------------------

  private tick() {
    if (!this.running || !this.world) return;

    const floor = this.world.groundHeight;
    const perBroadcast = Math.round(1 / (BROADCAST_HZ * TIMESTEP));

    for (let step = 0; step < perBroadcast; step++) {
      for (const body of this.bodies) {
        if (body.free) {
          body.velocity[2] -= GRAVITY * TIMESTEP;
          body.position[0] += body.velocity[0] * TIMESTEP;
          body.position[1] += body.velocity[1] * TIMESTEP;
          body.position[2] += body.velocity[2] * TIMESTEP;

          const rest = this.restHeightUnder(body) + body.halfHeight;
          if (body.position[2] <= rest) {
            body.position[2] = rest;
            // Lose most of the speed on contact, and stop outright once it is small. Without
            // the second half a body jitters against the floor forever, which reads as a
            // physics bug even in a mock.
            body.velocity[2] = Math.abs(body.velocity[2]) < 0.25 ? 0 : -body.velocity[2] * LANDING_DAMPING;
            body.velocity[0] *= 0.6;
            body.velocity[1] *= 0.6;
          }
        }

        if (body.hingeRange) {
          // Swing open, then hold.
          const target = body.hingeRange[1];
          body.angle += (target - body.angle) * (1 - Math.exp(-TIMESTEP / HINGE_TAU));
          body.angle = clamp(body.angle, body.hingeRange[0], target);
          body.orientation = composeHinge(body.restQuaternion, body.hingeAxis, body.angle);
        }
      }
      this.steps += 1;
      this.time += TIMESTEP;
    }

    if (this.bodies.length) {
      this.emit({ type: "pose.batch", t: this.time, poses: this.bodies.map(poseOf) });
    }
  }

  /**
   * The top of the highest declared surface beneath a body, or the floor.
   *
   * Enough for a bottle to land on a worktop rather than sail through it. It is not
   * collision detection: nothing here handles a body sliding off an edge.
   */
  private restHeightUnder(body: Body): number {
    let best = this.world?.groundHeight ?? 0;
    for (const o of this.obstacles) {
      if (o.kind !== "surface") continue;
      const top = o.position[2] + o.halfExtents[2];
      if (top > best && top <= body.position[2] + 1e-6) {
        if (
          Math.abs(body.position[0] - o.position[0]) <= o.halfExtents[0] &&
          Math.abs(body.position[1] - o.position[1]) <= o.halfExtents[1]
        ) {
          best = top;
        }
      }
    }
    return best;
  }

  private emit(message: ServerMessage) {
    this.options.onMessage(message);
  }
}

// -- helpers ------------------------------------------------------------------------------

function poseOf(body: Body): PoseUpdate {
  return {
    bodyName: body.name,
    position: [...body.position] as Vec3,
    orientation: [...body.orientation] as [number, number, number, number],
  };
}

function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

function labelFrom(prompt: string): string {
  const words = prompt.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  const skip = new Set(["a", "an", "the", "some", "this", "that", "my"]);
  return words.find((w) => !skip.has(w)) ?? "object";
}

/**
 * The selection's canonical axes as a (w, x, y, z) quaternion.
 *
 * Columns are front, left, up — the object's own frame — so the matrix whose columns are
 * those axes IS the local-to-world rotation. Shepperd's method, matching the Python side.
 */
function quaternionFromAxes(a: readonly number[]): [number, number, number, number] {
  const m = [
    [a[0], a[3], a[6]],
    [a[1], a[4], a[7]],
    [a[2], a[5], a[8]],
  ];
  const trace = m[0][0] + m[1][1] + m[2][2];
  let q: number[];
  if (trace > 0) {
    const s = Math.sqrt(trace + 1) * 2;
    q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s];
  } else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) {
    const s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2;
    q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s];
  } else if (m[1][1] > m[2][2]) {
    const s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2;
    q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s];
  } else {
    const s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2;
    q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s];
  }
  const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1;
  return [q[0] / n, q[1] / n, q[2] / n, q[3] / n];
}

/** Hamilton product: the shell's rest orientation, then a rotation about the hinge axis. */
function composeHinge(
  rest: [number, number, number, number],
  axis: Vec3,
  angle: number,
): [number, number, number, number] {
  const half = angle / 2;
  const s = Math.sin(half);
  const b: [number, number, number, number] = [Math.cos(half), axis[0] * s, axis[1] * s, axis[2] * s];
  const [aw, ax, ay, az] = rest;
  const [bw, bx, by, bz] = b;
  return [
    aw * bw - ax * bx - ay * by - az * bz,
    aw * bx + ax * bw + ay * bz - az * by,
    aw * by - ax * bz + ay * bw + az * bx,
    aw * bz + ax * by - ay * bx + az * bw,
  ];
}
