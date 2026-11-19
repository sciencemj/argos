import { useState } from "react";
import { Navigate, useParams } from "react-router";
import { useChannels } from "../api";
import { tr } from "../i18n";
import { AreaIcon, PlusIcon } from "../icons";
import { AddChannelDialog } from "../layout/Sidebar";
import { btn, card } from "../ui";

/** `/area/:areaId` (v0.2): an area with no channels yet. Opening it from the rail used
 * to go to "/", which now jumps back to the last channel, so a new area was unreachable. */
export function AreaPage() {
  const { areaId } = useParams();
  const { data, isLoading } = useChannels();
  const [adding, setAdding] = useState(false);
  if (isLoading) return null;
  const area = data?.areas.find((a) => a.id === areaId);
  const first = data?.channels.find((c) => c.area_id === areaId);
  if (first) return <Navigate to={`/c/${first.id}`} replace />;
  if (!area) {
    return (
      <div className="mx-auto w-full max-w-[640px] px-8 pt-16 text-[14px] text-text-3 max-md:px-4">
        {tr("영역을 찾을 수 없어요", "Area not found")}
      </div>
    );
  }
  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[640px] flex-col gap-6 px-8 pt-16 pb-16 max-md:px-4 max-md:pt-8">
        <div className="flex items-center gap-3">
          <span className="flex size-[42px] shrink-0 items-center justify-center rounded-xl border border-line bg-card text-[15px] font-semibold text-ink shadow-raised">
            <AreaIcon
              icon={area.icon}
              fallback={area.icon || area.name.slice(0, 1)}
              size={18}
            />
          </span>
          <h1 className="m-0 truncate text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {area.name}
          </h1>
        </div>
        <section
          className={`${card} flex flex-col items-start gap-4 p-6`}
          aria-label={tr("빈 영역", "Empty area")}
        >
          <p className="m-0 text-[14px] leading-relaxed text-text-2">
            {tr(
              "이 영역에는 아직 채널이 없어요. 프로젝트 채널을 만들어 시작하세요.",
              "No channels in this area yet. Make a project channel to start.",
            )}
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={`${btn.cta} inline-flex items-center gap-1.5`}
              onClick={() => setAdding(true)}
            >
              <PlusIcon size={15} />
              {tr("프로젝트 채널 만들기", "New project channel")}
            </button>
          </div>
        </section>
      </div>
      <AddChannelDialog
        open={adding}
        defaultAreaId={area.id}
        onClose={() => setAdding(false)}
      />
    </div>
  );
}
