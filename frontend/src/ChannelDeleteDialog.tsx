import { useState } from "react";
import { ApiError, type Channel, useDeleteChannel } from "./api";
import { tr, tt } from "./i18n";
import { btn, Dialog, ErrorText } from "./ui";

export function ChannelDeleteDialog({
  channel,
  onClose,
  onDeleted,
}: {
  channel: Channel;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const remove = useDeleteChannel();
  const [force, setForce] = useState(false);
  const destroy = () =>
    remove.mutate(
      { id: channel.id, force },
      {
        onSuccess: onDeleted,
        onError: (error) => {
          if (error instanceof ApiError && error.code === "conflict" && !force)
            setForce(true);
        },
      },
    );
  const error =
    remove.error instanceof ApiError && remove.error.code === "conflict"
      ? null
      : remove.error;

  return (
    <Dialog open onClose={onClose} title={tt`#${channel.name} 삭제`}>
      <div className="flex flex-col gap-4 text-[13px] leading-relaxed text-text-2">
        <p className="m-0">
          {tr(
            "이 채널과 메시지를 삭제할까요? 연결된 옵시디언 노트 파일은 남아요.",
          )}
        </p>
        {force && (
          <p className="m-0 text-danger">
            {tr(
              "이 채널에는 할 일이나 일정도 있어요. 계속하면 함께 삭제됩니다.",
            )}
          </p>
        )}
        <ErrorText error={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button
            type="button"
            className={btn.danger}
            disabled={remove.isPending}
            onClick={destroy}
          >
            {force ? tr("모두 삭제") : tr("채널 삭제")}
          </button>
        </div>
      </div>
    </Dialog>
  );
}
