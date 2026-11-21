import { Canvas, useFrame, useLoader } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import {
  type AnimationAction,
  AnimationMixer,
  LoopOnce,
  LoopRepeat,
  type Material,
  Mesh,
  MeshBasicMaterial,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import dogUrl from "./argos-dog.glb?url";
import { GREET_CLIP, JUMP_CLIP, MOOD_CLIP, type Mood } from "./mood";

const FADE = 0.35;
/** The black rim drawn by the inverted-hull shell (Blender: Solidify, flipped normals). */
const OUTLINE_MATERIAL = "Dog_Outline";

export interface MascotProps {
  mood: Mood;
  /** Bumped by the parent to make the dog jump once (a click, a finished task). */
  jumps: number;
  reducedMotion: boolean;
}

/** The 3D dog itself; loaded lazily (three.js is a big chunk) by MascotSlot. */
export default function ArgosMascot(props: MascotProps) {
  return (
    <Canvas
      dpr={[1, 2]}
      gl={{ alpha: true, antialias: true }}
      camera={{ position: [2.4, 1.9, 6.4], fov: 28 }}
      onCreated={({ camera }) => camera.lookAt(0.15, 1.1, 0)}
      style={{ background: "transparent" }}
    >
      <hemisphereLight args={["#ffffff", "#c9d3e0", 1.6]} />
      <directionalLight position={[3, 5, 4]} intensity={1.8} />
      <directionalLight position={[-4, 3, -3]} intensity={0.7} />
      <Dog {...props} />
    </Canvas>
  );
}

function Dog({ mood, jumps, reducedMotion }: MascotProps) {
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

  useEffect(() => {
    gltf.scene.traverse((o) => {
      if (!(o instanceof Mesh)) return;
      o.frustumCulled = false; // skinned bounds don't follow the jump
      const mat = o.material as Material;
      if (mat.name === OUTLINE_MATERIAL) {
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

  useFrame((_, dt) => {
    mixer.update(reducedMotion ? 0 : Math.min(dt, 0.1));
  });

  return <primitive object={gltf.scene} />;
}
