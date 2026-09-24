import React, { useState, useEffect, useRef } from "react";
import { adminApi, api, requestId } from "../api";
import { RichEditor, RichPreview } from "./rich-text";
import {
  blankStep,
  complexStep,
  delayLabel,
  mediaUrl,
  stepRich,
  withRich,
  withMedia,
} from "./types";
import type { Chain, Keyboard, Media, Scope, Step } from "./types";
import "./editor.css";

export function DelayField({
  value,
  onChange,
  label,
}: {
  value: number;
  onChange: (n: number) => void;
  label: string;
}) {
  const units = [
    ["Дни", 86400, 180],
    ["Часы", 3600, 23],
    ["Минуты", 60, 59],
    ["Секунды", 1, 59],
  ] as const;
  return (
    <fieldset className="delay-field">
      <legend>{label}</legend>
      <div>
        {units.map(([name, size, max], index) => (
          <label key={name}>
            {name}
            <input
              type="number"
              min={0}
              max={max}
              inputMode="numeric"
              value={
                Math.floor(value / size) %
                (index === 0 ? 10000 : index === 1 ? 24 : 60)
              }
              onChange={(e) => {
                const n = Math.max(
                  0,
                  Math.min(max, Number(e.target.value) || 0),
                );
                const old =
                  Math.floor(value / size) %
                  (index === 0 ? 10000 : index === 1 ? 24 : 60);
                onChange(Math.min(15552000, value + (n - old) * size));
              }}
            />
          </label>
        ))}
      </div>
      <small>{value ? `Через ${delayLabel(value)}` : "Без ожидания"}</small>
    </fieldset>
  );
}
export function MediaPreview({ media, scope }: { media: Media; scope: Scope }) {
  const src = mediaUrl(media, scope);
  if (!src)
    return (
      <span>
        {media.original_name || "Вложение"} · {media.media_type}
      </span>
    );
  if (media.mime_type.startsWith("image/"))
    return (
      <img
        className="editor-media-preview"
        src={src}
        alt={media.original_name || "Вложение"}
        loading="lazy"
      />
    );
  if (media.mime_type.startsWith("video/"))
    return (
      <video
        className="editor-media-preview"
        src={src}
        controls
        playsInline
        preload="metadata"
      />
    );
  if (media.mime_type.startsWith("audio/"))
    return (
      <audio
        className="editor-media-preview"
        src={src}
        controls
        preload="metadata"
      />
    );
  return (
    <a href={src} target="_blank" rel="noreferrer">
      Открыть {media.original_name || "файл"}
    </a>
  );
}
function KeyboardEditor({
  value,
  onChange,
  allowSubscribe,
}: {
  value: Keyboard | null;
  onChange: (v: Keyboard | null) => void;
  allowSubscribe: boolean;
}) {
  const rows = value?.rows || [];
  if (
    value &&
    (value.kind !== "inline" ||
      rows.some((row) => row.some((b) => !["url", "subscribe"].includes(b.action_type))))
  )
    return (
      <p className="composer-notice">
        Специальная клавиатура сохранится без изменений. Настроить её можно в
        боте.
      </p>
    );
  const update = (next: Keyboard["rows"]) =>
    onChange(next.length ? { ...value, kind: "inline", rows: next } : null);
  const newButton = () => ({
    text: "",
    value: "",
    action_type: "url",
    style: "default",
  });
  return (
    <section className="keyboard-editor">
      <h4>Кнопки под сообщением</h4>
      <p>Добавьте название и ссылку. Кнопки одного ряда будут стоять рядом.</p>
      {rows.map((row, r) => (
        <fieldset key={r}>
          <legend>Ряд {r + 1}</legend>
          {row.map((button, c) => (
            <div className="link-button-fields" key={c}>
              <label>
                Название кнопки
                <input
                  value={button.text}
                  maxLength={64}
                  onChange={(e) =>
                    update(
                      rows.map((a, i) =>
                        i === r
                          ? a.map((b, j) =>
                              j === c ? { ...b, text: e.target.value } : b,
                            )
                          : a,
                      ),
                    )
                  }
                />
              </label>
              <label>
                Действие
                <select value={button.action_type} onChange={(e) => update(rows.map((a, i) => i === r ? a.map((b, j) => j === c ? {...b, action_type:e.target.value, value:""} : b) : a))}>
                  <option value="url">Открыть ссылку</option>
                  {allowSubscribe&&<option value="subscribe">Добавить в базу</option>}
                </select>
              </label>
              {button.action_type === "url" && <label>
                Ссылка
                <input type="url" value={button.value} placeholder="https://example.com"
                  onChange={(e) => update(rows.map((a, i) => i === r ? a.map((b, j) => j === c ? {...b, value:e.target.value} : b) : a))}/>
              </label>}
              <fieldset className="button-style-picker">
                <legend>Цвет кнопки</legend>
                <div role="radiogroup" aria-label={`Цвет кнопки ${button.text || c + 1}`}>
                  {[
                    ["default", "Стандартная", "Нейтральное действие"],
                    ["primary", "Синяя", "Главное действие"],
                    ["success", "Зелёная", "Положительное действие"],
                    ["danger", "Красная", "Важное предупреждение"],
                  ].map(([style, label, hint]) => (
                    <button
                      type="button"
                      role="radio"
                      aria-checked={button.style === style}
                      className={`telegram-style-choice ${style} ${button.style === style ? "selected" : ""}`}
                      key={style}
                      onClick={() =>
                        update(
                          rows.map((a, i) =>
                            i === r
                              ? a.map((b, j) =>
                                  j === c ? { ...b, style } : b,
                                )
                              : a,
                          ),
                        )
                      }
                    >
                      <b>{label}</b>
                      <small>{hint}</small>
                    </button>
                  ))}
                </div>
                <small className="button-style-help">Telegram адаптирует оттенок к светлой или тёмной теме.</small>
              </fieldset>
              <button
                type="button"
                onClick={() =>
                  update(
                    rows
                      .map((a, i) =>
                        i === r ? a.filter((_, j) => j !== c) : a,
                      )
                      .filter((a) => a.length),
                  )
                }
              >
                Удалить кнопку {c + 1}
              </button>
            </div>
          ))}
          {row.length < 3 && (
            <button
              type="button"
              onClick={() =>
                update(rows.map((a, i) => (i === r ? [...a, newButton()] : a)))
              }
            >
              Добавить рядом
            </button>
          )}
        </fieldset>
      ))}
      {rows.length < 15 && (
        <button type="button" onClick={() => update([...rows, [newButton()]])}>
          Добавить ряд кнопок
        </button>
      )}
    </section>
  );
}
export function validateChain(chain: Chain, maxSteps = 5): string | null {
  if (!chain.steps.length || chain.steps.length > maxSteps)
    return `Добавьте от 1 до ${maxSteps} сообщений.${maxSteps === 5 ? " Для 6–10 сообщений требуется Business." : ""}`;
  if (chain.steps.reduce((n, s) => n + s.attachments.length, 0) > 5)
    return "Допускается до 5 файлов на цепочку.";
  for (let i = 0; i < chain.steps.length; i++) {
    const s = chain.steps[i];
    const text = stepRich(s).text;
    if (!complexStep(s) && !text.trim() && !s.attachments.length)
      return `Сообщение ${i + 1}: добавьте текст или файл.`;
    if (text.length > (s.attachments.length ? 1024 : 4096))
      return `Сообщение ${i + 1}: текст слишком длинный (${s.attachments.length ? 1024 : 4096} символов).`;
    for (const row of s.keyboard?.rows || [])
      for (const b of row)
        if (
          b.action_type === "url" &&
          (!b.text.trim() || !/^https?:\/\/[^\s]+$/i.test(b.value))
        )
          return `Сообщение ${i + 1}: укажите название кнопки и ссылку http:// или https://.`;
  }
  return null;
}
function StepPreview({ step, scope }: { step: Step; scope: Scope }) {
  const media = (m: Media, i: number) => (
    <div key={i}>
      <MediaPreview media={m} scope={scope} />
      {m.payload?.caption && (
        <RichPreview
          value={{
            text: m.payload.caption,
            entities: m.payload.caption_entities || [],
          }}
          scope={scope}
        />
      )}
    </div>
  );
  if (Array.isArray(step.payload.sequence))
    return (
      <>
        <small>Составное сообщение · редактируется в боте</small>
        {step.payload.sequence.map((item: any, i: number) =>
          item.kind === "message" ? (
            <RichPreview
              key={i}
              value={{
                text: item.payload?.text || item.payload?.caption || "",
                entities:
                  item.payload?.entities ||
                  item.payload?.caption_entities ||
                  [],
              }}
              scope={scope}
            />
          ) : step.attachments[item.attachment_index] ? (
            media(step.attachments[item.attachment_index], i)
          ) : null,
        )}
      </>
    );
  if (complexStep(step))
    return (
      <>
        <small>Специальное сообщение · редактируется в боте</small>
        <RichPreview value={stepRich(step)} scope={scope} />
        {step.attachments.map((m, i) =>
          media(
            {
              ...m,
              payload: { ...(step.payload.items?.[i] || {}), ...m.payload },
            },
            i,
          ),
        )}
      </>
    );
  return (
    <>
      <RichPreview value={stepRich(step)} scope={scope} />
      <div className="preview-media">
        {step.attachments.map((m, i) => (
          <MediaPreview key={i} media={m} scope={scope} />
        ))}
      </div>
    </>
  );
}
export function ChainPreview({
  chain,
  scope,
  buttonStyle,
}: {
  chain: Chain;
  scope: Scope;
  buttonStyle?: string | null;
}) {
  return (
    <div className="chain-preview" aria-label="Предпросмотр цепочки">
      <p className="timeline-delay">
        {chain.first_delay_seconds
          ? `Первое сообщение через ${delayLabel(chain.first_delay_seconds)}`
          : "Первое сообщение сразу"}
      </p>
      {chain.steps.map((step, index) => (
        <React.Fragment key={step.localId || step.id || index}>
          <article className="message-preview">
            <small>Сообщение {index + 1}</small>
            <StepPreview step={step} scope={scope} />
            {step.keyboard?.rows.map((row, r) => (
              <div className="preview-buttons" key={r}>
                {row.map((b, c) => (
                  <span key={c} data-color={buttonStyle || b.style}>
                    {b.text || "Название кнопки"}
                  </span>
                ))}
              </div>
            ))}
          </article>
          {index < chain.steps.length - 1 && (
            <p className="timeline-delay">
              {step.delay_after_seconds
                ? `Через ${delayLabel(step.delay_after_seconds)}`
                : "Следом, без ожидания"}
            </p>
          )}
        </React.Fragment>
      ))}
    </div>
  );
}
export function ChainComposer({
  value,
  onChange,
  scope,
  onBusyChange,
  problem,
  maxSteps = 5,
  allowSubscribe = true,
}: {
  problem?: string;
  maxSteps?: number;
  allowSubscribe?: boolean;
  value: Chain;
  onChange: (v: Chain) => void;
  scope: Scope;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [active, setActive] = useState(0),
    [uploading, setUploading] = useState(false),
    [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const match = problem?.match(/Сообщение (\d+)/);
    if (!match) return;
    setActive(Number(match[1]) - 1);
    const timer = setTimeout(() => {
      root.current
        ?.querySelector<HTMLElement>(
          problem?.includes("кнопки")
            ? ".is-editing .link-button-fields input"
            : '.is-editing [role="textbox"],.is-editing input',
        )
        ?.focus();
    }, 0);
    return () => clearTimeout(timer);
  }, [problem]);
  const update = (index: number, next: Step) =>
    onChange({
      ...value,
      steps: value.steps.map((s, i) => (i === index ? next : s)),
    });
  const move = (index: number, delta: number) => {
    const steps = [...value.steps];
    [steps[index], steps[index + delta]] = [steps[index + delta], steps[index]];
    onChange({ ...value, steps });
    setActive(index + delta);
  };
  const upload = async (index: number, file: File, replace?: number) => {
    setError("");
    if (file.size > 20 * 1024 * 1024) {
      setError("Максимальный размер файла — 20 МБ.");
      return;
    }
    if (
      replace === undefined &&
      value.steps.reduce((n, s) => n + s.attachments.length, 0) >= 5
    ) {
      setError("В цепочке уже 5 файлов. Удалите или замените один из них.");
      return;
    }
    setUploading(true);
    onBusyChange?.(true);
    try {
      const result = await (scope === "admin" ? adminApi : api)<Media>(
        scope === "admin" ? "/catalog-media" : "/editor/media",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-File-Name": encodeURIComponent(file.name),
          },
          body: file,
        },
      );
      if (["sticker", "video_note"].includes(result.media_type))
        throw new Error(
          "Этот формат отправляется отдельным стикером или видеокружком. Для вложения выберите JPG, PNG, GIF или MP4.",
        );
      result.original_name = file.name;
      const step = value.steps[index],
        attachments = [...step.attachments];
      if (replace === undefined) attachments.push(result);
      else attachments[replace] = result;
      update(index, withMedia(step, attachments));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      onBusyChange?.(false);
    }
  };
  return (
    <div className="chain-composer" ref={root}>
      <DelayField
        label="Когда отправить первое сообщение"
        value={value.first_delay_seconds}
        onChange={(n) => onChange({ ...value, first_delay_seconds: n })}
      />
      {error && (
        <p role="alert" className="composer-error">
          {error}
        </p>
      )}
      <div className="message-list">
        {value.steps.map((step, index) => {
          const complex = complexStep(step);
          return (
            <React.Fragment key={step.localId || step.id || index}>
              <article
                className={`message-card ${active === index ? "is-editing" : ""}`}
                key={step.localId || step.id || index}
              >
                <header>
                  <button
                    type="button"
                    className="message-select"
                    onClick={() => setActive(index)}
                    aria-expanded={active === index}
                  >
                    <strong>Сообщение {index + 1}</strong>
                    <span>
                      {active !== index
                        ? stepRich(step).text.slice(0, 70) ||
                          `${step.attachments.length} вложений`
                        : "Текст, медиа и кнопки"}
                    </span>
                  </button>
                  <div className="message-order">
                    <button
                      type="button"
                      disabled={uploading || index === 0}
                      onClick={() => move(index, -1)}
                    >
                      Выше
                    </button>
                    <button
                      type="button"
                      disabled={uploading || index === value.steps.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      Ниже
                    </button>
                  </div>
                </header>
                {active === index && (
                  <fieldset
                    disabled={uploading}
                    inert={uploading}
                    className="message-fields"
                  >
                    {complex ? (
                      <p className="composer-notice">
                        Составное сообщение создано в боте. Содержимое и порядок
                        вложений сохраняются; изменить их можно в боте.
                      </p>
                    ) : (
                      <RichEditor
                        key={step.localId || step.id || index}
                        scope={scope}
                        value={stepRich(step)}
                        onChange={(rich) => update(index, withRich(step, rich))}
                      />
                    )}
                    <section className="media-editor">
                      <h4>Медиа сообщения</h4>
                      <div className="media-list">
                        {step.attachments.map((media, position) => (
                          <div
                            className="media-item"
                            key={`${media.storage_key}-${position}`}
                          >
                            <MediaPreview media={media} scope={scope} />
                            <small>
                              {media.original_name} ·{" "}
                              {Math.ceil(media.size / 1024)} КБ
                            </small>
                            {!complex && (
                              <div className="composer-actions">
                                <label className="file-action">
                                  Заменить
                                  <input
                                    type="file"
                                    accept=".jpg,.jpeg,.png,.gif,.mp4,.mp3,.ogg,.pdf,.zip"
                                    onChange={(e) => {
                                      const f = e.target.files?.[0];
                                      e.target.value = "";
                                      if (f) void upload(index, f, position);
                                    }}
                                  />
                                </label>
                                <button
                                  type="button"
                                  disabled={position === 0}
                                  onClick={() => {
                                    const a = [...step.attachments];
                                    [a[position - 1], a[position]] = [
                                      a[position],
                                      a[position - 1],
                                    ];
                                    update(index, withMedia(step, a));
                                  }}
                                >
                                  Раньше
                                </button>
                                <button
                                  type="button"
                                  disabled={
                                    position === step.attachments.length - 1
                                  }
                                  onClick={() => {
                                    const a = [...step.attachments];
                                    [a[position + 1], a[position]] = [
                                      a[position],
                                      a[position + 1],
                                    ];
                                    update(index, withMedia(step, a));
                                  }}
                                >
                                  Позже
                                </button>
                                <button
                                  type="button"
                                  onClick={() =>
                                    update(
                                      index,
                                      withMedia(
                                        step,
                                        step.attachments.filter(
                                          (_, i) => i !== position,
                                        ),
                                      ),
                                    )
                                  }
                                >
                                  Убрать файл
                                </button>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                      {!complex && (
                        <label className="file-action">
                          Добавить файл
                          <input
                            type="file"
                            accept=".jpg,.jpeg,.png,.gif,.mp4,.mp3,.ogg,.pdf,.zip"
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              e.target.value = "";
                              if (f) void upload(index, f);
                            }}
                          />
                        </label>
                      )}
                      <small>До 5 файлов на цепочку, до 20 МБ каждый.</small>
                    </section>
                    <KeyboardEditor
                      value={step.keyboard}
                      allowSubscribe={allowSubscribe}
                      onChange={(keyboard) =>
                        update(index, { ...step, keyboard })
                      }
                    />
                    {((step.payload.type === "text" && step.attachments.length === 0) ||
                      (Array.isArray(step.payload.sequence) &&
                        step.payload.sequence.some(
                          (item: any) => item?.kind === "message" && item.payload?.type === "text",
                        ))) && (
                      <label className="link-preview-toggle">
                        <input type="checkbox" checked={step.payload.link_preview_enabled !== false}
                          onChange={(e) => update(index, {...step, payload:{...step.payload, link_preview_enabled:e.target.checked}})}/>
                        Показывать предпросмотр ссылки
                      </label>
                    )}
                    {index < value.steps.length - 1 && (
                      <DelayField
                        label="Пауза до следующего сообщения"
                        value={step.delay_after_seconds}
                        onChange={(n) =>
                          update(index, { ...step, delay_after_seconds: n })
                        }
                      />
                    )}
                    <div className="composer-actions">
                      <button
                        type="button"
                        disabled={
                          value.steps.length >= maxSteps ||
                          value.steps.reduce(
                            (n, s) => n + s.attachments.length,
                            0,
                          ) +
                            step.attachments.length >
                            5
                        }
                        onClick={() => {
                          const steps = [...value.steps];
                          steps.splice(index + 1, 0, {
                            ...structuredClone(step),
                            localId: requestId(),
                          });
                          onChange({ ...value, steps });
                          setActive(index + 1);
                        }}
                      >
                        Копировать сообщение
                      </button>
                      <button
                        type="button"
                        disabled={value.steps.length <= 1}
                        onClick={() => {
                          if (
                            !confirm(
                              `Удалить сообщение ${index + 1} из черновика?`,
                            )
                          )
                            return;
                          onChange({
                            ...value,
                            steps: value.steps.filter((_, i) => i !== index),
                          });
                          setActive(Math.max(0, index - 1));
                        }}
                      >
                        Удалить сообщение
                      </button>
                    </div>
                  </fieldset>
                )}
                {uploading && active === index && (
                  <p role="status">Загружаем файл…</p>
                )}
              </article>
              {index < value.steps.length - 1 && (
                <p className="timeline-delay">
                  {step.delay_after_seconds
                    ? `Через ${delayLabel(step.delay_after_seconds)}`
                    : "Следом, без ожидания"}
                </p>
              )}
            </React.Fragment>
          );
        })}
      </div>
      {maxSteps > 1 && <button
        className="button ghost"
        type="button"
        disabled={uploading || value.steps.length >= maxSteps}
        onClick={() => {
          onChange({ ...value, steps: [...value.steps, blankStep()] });
          setActive(value.steps.length);
        }}
      >
        Добавить сообщение · {value.steps.length}/{maxSteps}
      </button>}
    </div>
  );
}
