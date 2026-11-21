import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  type AnimationAction,
  AnimationMixer,
  Euler,
  LoopOnce,
  LoopRepeat,
  type Material,
  Mesh,
  MeshBasicMaterial,
  type Object3D,
  Quaternion,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import dogUrl from "./argos-dog.glb?url";
import { GREET_CLIP, JUMP_CLIP, MOOD_CLIP, type Mood } from "./mood";

const FADE = 0.35;
/** The black rim drawn by the inverted-hull shell (Blender: Solidify, flipped normals). */
const OUTLINE_MATERIAL = "Dog_Outline";
/** The model faces +X; turn it toward the camera and a little to the screen's left,
 * where the page (and so usually the pointer) is. */
const FACING = -2.05;
/** How far the eyes may turn toward the cursor, in radians, and how the turn is shared
 * between the neck and the head. */
const MAX_YAW = 0.5;
const MAX_PITCH = 0.32;
const NECK_SHARE = 0.35;
const GAZE_SPEED = 6;

const clamp = (v: number) => Math.max(-1, Math.min(1, v));

export interface MascotProps {
  mood: Mood;
  /** Bumped by the parent to make the dog jump once (a click, a finished task). */
  jumps: number;
  reducedMotion: boolean;
  /** Turn the head toward the mouse pointer anywhere on the page. */
  followCursor: boolean;
}

/** The 3D dog itself; loaded lazily (three.js is a big chunk) by MascotSlot. */
export default function ArgosMascot(props: MascotProps) {
  return (
    <Canvas
      dpr={[1, 2]}
      gl={{ alpha: true, antialias: true }}
      camera={{ position: [0, 1.75, 6.6], fov: 28 }}
      onCreated={({ camera }) => camera.lookAt(0, 1.15, 0)}
      style={{ background: "transparent" }}
    >
      <hemisphereLight args={["#ffffff", "#c9d3e0", 1.6]} />
      <directionalLight position={[3, 5, 4]} intensity={1.8} />
      <directionalLight position={[-4, 3, -3]} intensity={0.7} />
      <Dog {...props} />
    </Canvas>
  );
}

function Dog({ mood, jumps, reducedMotion, followCursor }: MascotProps) {
  const gltf = useLoader(GLTFLoader, dogUrl);
  const mixer = useMemo(() => new AnimationMixer(gltf.scene), [gltf]);
  const actions = useMemo(() => {
    const map = new Map<string, AnimationAction>();
    for (const clip of gltf.animations)
      map.set(clip.name, mixer.clipAction(clip));
    return map;
  }, [gltf, mixer]);
  const current = useRef<AnimationAction | null>(null);
  const oneShot = useRef(false);
  const moodRef = useRef(mood);
  moodRef.current = mood;

  const gaze = useGaze(followCursor && !reducedMotion);
  const bones = useMemo(
    () => ({
      neck: gltf.scene.getObjectByName("neck"),
      head: gltf.scene.getObjectByName("head"),
    }),
    [gltf],
  );

  useEffect(() => {
    gltf.scene.rotation.y = FACING;
    gltf.scene.traverse((o) => {
      if (!(o instanceof Mesh)) return;
      o.frustumCulled = false; // skinned bounds don't follow the jump
      const old = o.material as Material | Material[];
      const mats = Array.isArray(old) ? old : [old];
      if (mats.some((m) => m.name === OUTLINE_MATERIAL)) {
        for (const m of mats) m.dispose(); // replaced: free its GPU resources
        o.material = new MeshBasicMaterial({ color: "#1c1717" });
      }
    });
  }, [gltf]);

  const play = (name: string, once: boolean) => {
    const next = actions.get(name);
    const prev = current.current;
    if (!next || (next === prev && !once)) return;
    next.reset();
    next.setLoop(once ? LoopOnce : LoopRepeat, once ? 1 : Infinity);
    next.clampWhenFinished = once;
    next.play();
    if (prev && prev !== next) prev.crossFadeTo(next, FADE, false);
    current.current = next;
    oneShot.current = once;
  };

  // First appearance: get up and say hello, then settle into the mood.
  // biome-ignore lint/correctness/useExhaustiveDependencies: once per loaded model
  useEffect(() => {
    play(
      reducedMotion ? MOOD_CLIP[moodRef.current] : GREET_CLIP,
      !reducedMotion,
    );
    const done = () => {
      if (oneShot.current) play(MOOD_CLIP[moodRef.current], false);
    };
    mixer.addEventListener("finished", done);
    return () => {
      mixer.removeEventListener("finished", done);
      mixer.stopAllAction();
    };
  }, [mixer]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: react to the mood only
  useEffect(() => {
    if (!oneShot.current) play(MOOD_CLIP[mood], false);
  }, [mood]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: react to new jumps only
  useEffect(() => {
    if (jumps > 0 && !reducedMotion) play(JUMP_CLIP, true);
  }, [jumps]);

  // The pose each bone had before the gaze turn was added. The exporter drops
  // channels that never change, so the mixer does not reset every bone each frame:
  // undo last frame's turn first or it would pile up.
  const clean = useRef(new Map<Object3D, Quaternion>());

  useFrame((state, dt) => {
    const step = Math.min(dt, 0.1);
    for (const [bone, q] of clean.current) bone.quaternion.copy(q);
    mixer.update(reducedMotion ? 0 : step);
    const { yaw, pitch } = gaze.update(state.gl.domElement, step);
    for (const bone of [bones.neck, bones.head]) {
      if (!bone) continue;
      const saved = clean.current.get(bone);
      if (saved) saved.copy(bone.quaternion);
      else clean.current.set(bone, bone.quaternion.clone());
    }
    if (yaw === 0 && pitch === 0) return;
    turn(bones.neck, yaw * NECK_SHARE, pitch * NECK_SHARE);
    turn(bones.head, yaw * (1 - NECK_SHARE), pitch * (1 - NECK_SHARE));
  });

  return <primitive object={gltf.scene} />;
}

// Scratch objects reused every frame (no per-frame allocation).
const tmpParent = new Quaternion();
const tmpParentInv = new Quaternion();
const tmpTurn = new Quaternion();
const tmpEuler = new Euler(0, 0, 0, "YXZ");

/** Adds a turn about the world's up axis (yaw) and screen-horizontal axis (pitch) on
 * top of whatever the animation set for this bone this frame. */
function turn(bone: Object3D | undefined, yaw: number, pitch: number) {
  if (!bone?.parent) return;
  bone.parent.updateWorldMatrix(true, false);
  bone.parent.getWorldQuaternion(tmpParent);
  tmpTurn.setFromEuler(tmpEuler.set(pitch, yaw, 0));
  // local' = parent⁻¹ · turn · parent · local
  bone.quaternion.premultiply(
    tmpParentInv.copy(tmpParent).invert().multiply(tmpTurn).multiply(tmpParent),
  );
  bone.updateWorldMatrix(false, true);
}

/** Where the pointer is relative to the dog, eased so the head turns smoothly and
 * drifts back to the front when the pointer leaves the window or following is off. */
function useGaze(enabled: boolean) {
  const pointer = useRef<{ x: number; y: number } | null>(null);
  const eased = useRef({ yaw: 0, pitch: 0 });
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    if (!enabled) return;
    const move = (e: PointerEvent) => {
      pointer.current = { x: e.clientX, y: e.clientY };
    };
    const leave = () => {
      pointer.current = null;
    };
    window.addEventListener("pointermove", move, { passive: true });
    document.documentElement.addEventListener("pointerleave", leave);
    return () => {
      window.removeEventListener("pointermove", move);
      document.documentElement.removeEventListener("pointerleave", leave);
      pointer.current = null;
    };
  }, [enabled]);

  return useMemo(
    () => ({
      update(canvas: HTMLElement, dt: number) {
        let yaw = 0;
        let pitch = 0;
        const p = pointer.current;
        if (enabledRef.current && p) {
          const r = canvas.getBoundingClientRect();
          const dx = (p.x - (r.left + r.width / 2)) / (window.innerWidth / 2);
          const dy =
            (p.y - (r.top + r.height * 0.3)) / (window.innerHeight / 2);
          yaw = clamp(dx) * MAX_YAW;
          pitch = clamp(dy) * MAX_PITCH;
        }
        const k = Math.min(1, dt * GAZE_SPEED);
        const e = eased.current;
        e.yaw += (yaw - e.yaw) * k;
        e.pitch += (pitch - e.pitch) * k;
        if (Math.abs(e.yaw) < 1e-4 && Math.abs(e.pitch) < 1e-4) {
          e.yaw = 0;
          e.pitch = 0;
        }
        return e;
      },
    }),
    [],
  );
}
