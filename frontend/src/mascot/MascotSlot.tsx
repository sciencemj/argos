import {
  Component,
  lazy,
  type ReactNode,
  Suspense,
  useEffect,
  useRef,
  useState,
} from "react";
import { t } from "../i18n";
import { type Mood, moodLabel } from "./mood";
import { useMascotPref } from "./pref";

const ArgosMascot = lazy(() => import("./ArgosMascot"));

function hasWebGL(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return !!(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}

function useReducedMotion(): boolean {
  const query = "(prefers-reduced-motion: reduce)";
  const [reduced, setReduced] = useState(
    () => typeof matchMedia === "function" && matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(query);
    const on = () => setReduced(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** A broken GPU or model must never take the Today page down with it. */
class Quiet extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Argos, the dog, beside the Today heading: its mood follows the day (mood.ts), it
 * jumps when clicked or when a task gets done (`doneCount` goes up). */
export function MascotSlot({
  mood,
  doneCount,
}: {
  mood: Mood;
  doneCount: number;
}) {
  const pref = useMascotPref();
  const reducedMotion = useReducedMotion();
  const [webgl] = useState(hasWebGL);
  const [jumps, setJumps] = useState(0);
  const lastDone = useRef<number | null>(null);

  useEffect(() => {
    if (lastDone.current !== null && doneCount > lastDone.current) {
      setJumps((n) => n + 1);
    }
    lastDone.current = doneCount;
  }, [doneCount]);

  if (!webgl || pref === "off") return null;
  const label = `${t("Argos 강아지", "Argos the dog")} · ${moodLabel(mood)}`;
  return (
    <button
      type="button"
      onClick={() => setJumps((n) => n + 1)}
      aria-label={label}
      title={label}
      className="size-[150px] shrink-0 cursor-pointer rounded-xl border-0 bg-transparent p-0 max-md:size-[96px]"
    >
      <Quiet>
        <Suspense fallback={null}>
          <ArgosMascot
            mood={mood}
            jumps={jumps}
            reducedMotion={reducedMotion}
            followCursor={pref === "follow"}
          />
        </Suspense>
      </Quiet>
    </button>
  );
}
