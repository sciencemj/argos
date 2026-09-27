import { usePendingApprovals } from "../api";
import { ApprovalCard } from "../cards";
import { tr } from "../i18n";

/** Everything agents asked to delete, waiting on the user (PLAN P5). */
export function ApprovalsPage() {
  const { data, isSuccess } = usePendingApprovals();
  return (
    <div className="min-h-0 grow overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[760px] flex-col gap-5 px-9 pt-8 pb-8">
        <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
          {tr("승인 대기")}
        </h1>
        <p className="m-0 max-w-[580px] text-[13px] text-text-3">
          {tr(
            "에이전트가 지우려는 항목이에요. 승인하기 전에는 아무것도 삭제되지 않아요.",
          )}
        </p>
        {isSuccess && data.length === 0 && (
          <p className="m-0 text-[13px] text-meta">
            {tr("기다리는 승인이 없어요.")}
          </p>
        )}
        {data?.map((a) => (
          <ApprovalCard key={a.id} approval={a} />
        ))}
      </div>
    </div>
  );
}
