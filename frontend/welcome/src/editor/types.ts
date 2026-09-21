export type Entity = {
  type: string;
  offset: number;
  length: number;
  url?: string;
  custom_emoji_id?: string;
  [key: string]: unknown;
};
export type RichText = { text: string; entities: Entity[] };
export type Media = {
  id?: number;
  media_id?: string;
  media_type: string;
  storage_key: string;
  original_name: string;
  mime_type: string;
  size: number;
  payload: Record<string, any>;
  preview_url?: string;
};
export type Keyboard = {
  kind: string;
  rows: Array<
    Array<{ text: string; action_type: string; value: string; style: string }>
  >;
  settings?: Record<string, unknown>;
};
export type Step = {
  editor_read_only?: boolean;
  id?: number;
  localId?: string;
  payload: Record<string, any>;
  attachments: Media[];
  keyboard: Keyboard | null;
  delay_after_seconds: number;
};
export type Chain = { first_delay_seconds: number; steps: Step[] };
export type Scope = "admin" | "owner";
export function mediaUrl(media: Media, scope: Scope) {
  return (
    media.preview_url ||
    (scope === "admin" && media.storage_key.startsWith("catalog/uploads/")
      ? `/api/admin/v1/catalog-media/${media.storage_key.split("/").pop()}`
      : "")
  );
}
export function blankStep(): Step {
  return {
    localId: crypto.randomUUID(),
    payload: { type: "text", text: "", entities: [], link_preview_enabled: true },
    attachments: [],
    keyboard: null,
    delay_after_seconds: 0,
  };
}
export function stepRich(step: Step): RichText {
  const first = step.attachments[0]?.payload;
  return first?.caption !== undefined
    ? { text: first.caption, entities: first.caption_entities || [] }
    : step.attachments.length && step.payload.caption !== undefined
      ? {
          text: step.payload.caption,
          entities: step.payload.caption_entities || [],
        }
      : {
          text: step.payload.text ?? step.payload.caption ?? "",
          entities:
            step.payload.entities ?? step.payload.caption_entities ?? [],
        };
}
export function delayLabel(n: number) {
  if (!n) return "сразу";
  const parts = [
    [86400, "дн."],
    [3600, "ч"],
    [60, "мин"],
    [1, "сек"],
  ] as const;
  return parts
    .flatMap(([size, label]) => {
      const value = Math.floor(n / size);
      n %= size;
      return value ? [`${value} ${label}`] : [];
    })
    .join(" ");
}
export function hasPremium(chain: Chain) {
  return chain.steps.some((s) =>
    JSON.stringify([s.payload, s.attachments]).includes("custom_emoji"),
  );
}
export const emojiBase = (scope: Scope) =>
  scope === "admin" ? "/api/admin/v1/editor" : "/api/v1/editor";

export function complexStep(step: Step) {
  return !!(
    step.editor_read_only ||
    step.payload.sequence ||
    step.payload.items ||
    (!step.attachments.length &&
      step.payload.type &&
      step.payload.type !== "text") ||
    step.attachments.some((m) =>
      ["sticker", "video_note"].includes(m.media_type),
    ) ||
    step.attachments.slice(1).some((m) => m.payload?.caption)
  );
}
export function withRich(step: Step, rich: RichText): Step {
  const caption = step.payload.caption !== undefined;
  return {
    ...step,
    payload: {
      ...step.payload,
      [caption ? "caption" : "text"]: rich.text,
      [caption ? "caption_entities" : "entities"]: rich.entities,
    },
    attachments: step.attachments.map((m, i) =>
      i === 0 && m.payload?.caption !== undefined
        ? {
            ...m,
            payload: {
              ...m.payload,
              caption: rich.text,
              caption_entities: rich.entities,
            },
          }
        : m,
    ),
  };
}

export function withMedia(step: Step, attachments: Media[]): Step {
  const rich = stepRich(step);
  const { caption, caption_entities, ...payload } = step.payload;
  const next = {
    ...step,
    payload: {
      ...payload,
      type: "text",
      text: rich.text,
      entities: rich.entities,
    },
  };
  return {
    ...next,
    attachments: attachments.map((m) => {
      const { caption, caption_entities, ...payload } = m.payload || {};
      return { ...m, payload };
    }),
  };
}
