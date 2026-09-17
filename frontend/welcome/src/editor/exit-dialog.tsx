import React, { useEffect, useRef } from "react";

export function ExitDialog({
  title,
  busy,
  onSave,
  onDiscard,
  onCancel,
}: {
  title: string;
  busy: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    node?.querySelector<HTMLElement>("[data-cancel]")?.focus();
    return () => node?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="exit-confirm"
      aria-label="Несохранённые изменения"
      onCancel={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!busy) onCancel();
      }}
    >
      <h3>{title}</h3>
      <div className="composer-actions">
        <button disabled={busy} onClick={onSave}>
          Сохранить и выйти
        </button>
        <button disabled={busy} onClick={onDiscard}>
          Выйти без сохранения
        </button>
        <button data-cancel disabled={busy} onClick={onCancel}>
          Продолжить редактирование
        </button>
      </div>
    </dialog>
  );
}
export type LeaveGuard = (leave: () => void) => void;
