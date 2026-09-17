import type { JSONContent } from "@tiptap/core";
import type { Entity, RichText, Scope } from "./types";
const mapping: Record<string, string> = {
  bold: "bold",
  italic: "italic",
  underline: "underline",
  strikethrough: "strike",
  text_link: "link",
  spoiler: "spoiler",
  blockquote: "telegramQuote",
  expandable_blockquote: "telegramExpandableQuote",
  code: "code",
  pre: "telegramPre",
};
export function unsupportedEntities(entities: Entity[]) {
  return (
    entities.some((e) => !mapping[e.type] && e.type !== "custom_emoji") ||
    entities.some(
      (e) =>
        e.type === "custom_emoji" &&
        entities.some(
          (m) =>
            (m.offset > e.offset && m.offset < e.offset + e.length) ||
            (m.offset + m.length > e.offset &&
              m.offset + m.length < e.offset + e.length),
        ),
    )
  );
}
export function toDocument(value: RichText, scope: Scope): JSONContent {
  // Hard breaks carry marks, including formatting that spans an empty line.
  const cuts = new Set([0, value.text.length]);
  value.entities.forEach((e) => {
    cuts.add(e.offset);
    cuts.add(e.offset + e.length);
  });
  for (let i = 0; i < value.text.length; i++)
    if (value.text[i] === "\n") {
      cuts.add(i);
      cuts.add(i + 1);
    }
  const points = [...cuts].sort((a, b) => a - b),
    content: JSONContent[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i],
      b = points[i + 1];
    if (a === b) continue;
    const covering = value.entities.filter(
      (e) => e.offset <= a && e.offset + e.length >= b,
    );
    const emoji = covering.find((e) => e.type === "custom_emoji");
    const marks = covering
      .filter((e) => mapping[e.type])
      .map((e) => ({
        type: mapping[e.type],
        attrs:
          e.type === "text_link"
            ? { href: e.url }
            : e.type === "pre"
              ? { language: e.language || "" }
              : undefined,
      }));
    content.push(
      value.text.slice(a, b) === "\n"
        ? { type: "hardBreak", marks }
        : emoji
          ? {
              type: "telegramEmoji",
              attrs: {
                id: emoji.custom_emoji_id,
                alt: value.text.slice(a, b),
                scope,
              },
              marks,
            }
          : { type: "text", text: value.text.slice(a, b), marks },
    );
  }
  return { type: "doc", content: [{ type: "paragraph", content }] };
}
export function fromDocument(doc: JSONContent): RichText {
  let text = "";
  const spans: Entity[] = [];
  const reverse = Object.fromEntries(
    Object.entries(mapping).map(([a, b]) => [b, a]),
  );
  const append = (node: JSONContent) => {
    const start = text.length;
    if (node.type === "hardBreak") text += "\n";
    else if (node.type === "telegramEmoji") text += node.attrs?.alt || "⭐";
    else if (node.type === "text") text += node.text || "";
    else node.content?.forEach(append);
    const length = text.length - start;
    if (!length) return;
    if (node.type === "telegramEmoji")
      spans.push({
        type: "custom_emoji",
        offset: start,
        length,
        custom_emoji_id: String(node.attrs?.id),
      });
    for (const mark of node.marks || []) {
      const type = reverse[mark.type];
      if (type)
        spans.push({
          type,
          offset: start,
          length,
          ...(type === "text_link" ? { url: mark.attrs?.href } : {}),
          ...(type === "pre" ? { language: mark.attrs?.language || "" } : {}),
        });
    }
  };
  doc.content?.forEach((node, index) => {
    if (index) text += "\n";
    append(node);
  });
  const entities: Entity[] = [];
  for (const e of spans.sort(
    (a, b) => a.offset - b.offset || b.length - a.length,
  )) {
    const previous = entities.find(
      (p) =>
        p.type !== "custom_emoji" &&
        p.offset + p.length === e.offset &&
        p.type === e.type &&
        p.url === e.url &&
        p.language === e.language,
    );
    if (previous) previous.length += e.length;
    else entities.push({ ...e });
  }
  return { text, entities };
}
