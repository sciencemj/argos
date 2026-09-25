import { useNavigate } from "react-router";
import { useAnalytics, useChannels, useMakeReview } from "../api";
import { fmt } from "../dates";
import { btn, card, ErrorText, label } from "../ui";

/** How the weeks go (PLAN Phase 11): done per week, days from creation to done per
 * channel, and the weekly review on demand. */
export function ReviewPage() {
  const stats = useAnalytics();
  const make = useMakeReview();
  const channels = useChannels();
  const navigate = useNavigate();
  const weekly = stats.data?.weekly_done ?? [];
  const most = Math.max(1, ...weekly.map((w) => w.count));
  const processing = stats.data?.processing ?? [];
  const slowest = Math.max(1, ...processing.map((p) => p.hours));

  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-9 pt-8 pb-8">
        <div className="flex items-baseline gap-3">
          <h1 className="m-0 grow text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            리뷰
          </h1>
          <button
            type="button"
            className={btn.outline}
            disabled={make.isPending}
            onClick={() =>
              make.mutate(undefined, {
                onSuccess: (message) => {
                  const today = channels.data?.channels.find(
                    (c) => c.id === message.channel_id,
                  );
                  if (today) navigate(`/c/${today.id}`);
                },
              })
            }
          >
            {make.isPending ? "만드는 중…" : "이번 주 리뷰 만들기"}
          </button>
        </div>
        <p className="m-0 text-[13px] leading-relaxed text-text-3">
          주간 리뷰는 설정한 요일·시간에 #today로 자동으로 와요. 끝낸 일, 오래된
          backlog, 비슷한 인박스 메모를 모아 보여줘요.
        </p>
        <ErrorText error={stats.error ?? make.error} />
        <section
          aria-label="주간 완료"
          className={`${card} flex flex-col gap-3 p-6`}
        >
          <span className={label}>주마다 끝낸 할 일</span>
          <div className="flex h-36 items-end gap-3">
            {weekly.map((w, i) => (
              <div
                key={w.week}
                className="flex grow flex-col items-center gap-1.5"
              >
                <span className="font-mono text-[11px] text-text-3">
                  {w.count}
                </span>
                <div
                  className={`w-full rounded-t-md ${i === weekly.length - 1 ? "bg-ink" : "bg-step-4"}`}
                  style={{ height: `${Math.max(2, (w.count / most) * 100)}px` }}
                />
                <span className="font-mono text-[10.5px] text-meta">
                  {fmt(`${w.week}T12:00:00`, "M/d")}
                </span>
              </div>
            ))}
          </div>
        </section>
        <section
          aria-label="처리 시간"
          className={`${card} flex flex-col gap-3 p-6`}
        >
          <span className={label}>만든 뒤 끝내기까지 평균 (최근 30일)</span>
          {processing.length === 0 && (
            <span className="text-[13px] text-text-3">
              아직 끝낸 할 일이 없어요.
            </span>
          )}
          {processing.map((p) => (
            <div
              key={p.channel}
              className="flex items-center gap-3 text-[13px]"
            >
              <span className="w-32 shrink-0 truncate text-ink">
                # {p.channel}
              </span>
              <div className="h-2 grow overflow-hidden rounded-full bg-inset">
                <div
                  className="h-full rounded-full bg-step-4"
                  style={{ width: `${(p.hours / slowest) * 100}%` }}
                />
              </div>
              <span className="w-28 shrink-0 text-right font-mono text-[12px] text-text-3">
                {(p.hours / 24).toFixed(1)}일 · {p.count}개
              </span>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
