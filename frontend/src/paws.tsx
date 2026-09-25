/** Waiting indicator (backlog: "채팅 대기 아이콘"): a dog's paw prints stepping forward
 * one after another, left and right, then fading — Argos, the dog, on its way. */
function Paw({ index }: { index: number }) {
  const up = index % 2 === 0;
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className="paw-step"
      style={{
        animationDelay: `${index * 0.28}s`,
        transform: `translateY(${up ? -3 : 3}px) rotate(90deg)`,
      }}
    >
      <ellipse cx="12" cy="15.6" rx="4.3" ry="3.7" />
      <circle cx="6.3" cy="10.4" r="1.9" />
      <circle cx="9.9" cy="6.8" r="1.9" />
      <circle cx="14.1" cy="6.8" r="1.9" />
      <circle cx="17.7" cy="10.4" r="1.9" />
    </svg>
  );
}

export function PawTrail({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-flex items-center gap-[3px] text-ink"
        role="img"
        aria-label="기다리는 중"
      >
        {[0, 1, 2, 3].map((i) => (
          <Paw key={i} index={i} />
        ))}
      </span>
      {label && <span>{label}</span>}
    </span>
  );
}
