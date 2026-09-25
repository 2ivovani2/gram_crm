import React, { useEffect, useRef, useState } from "react";
import { Mark, Node, mergeAttributes } from "@tiptap/core";
import {
  EditorContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
} from "@tiptap/react";
import type { NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { adminApi, api } from "../api";
import { fromDocument, toDocument, unsupportedEntities } from "./entities";
import { emojiBase } from "./types";
import type { RichText, Scope } from "./types";

export function EmojiArt({
  id,
  alt,
  scope,
  animate = false,
}: {
  id: string;
  alt: string;
  scope: Scope;
  animate?: boolean;
}) {
  const host = useRef<HTMLSpanElement>(null);
  const [kind, setKind] = useState("");
  const [failed, setFailed] = useState(false);
  const src = `${emojiBase(scope)}/emoji/${id}`;
  useEffect(() => {
    if (!animate || matchMedia("(prefers-reduced-motion: reduce)").matches)
      return;
    let disposed = false;
    let destroy: undefined | (() => void);
    const controller = new AbortController();
    fetch(src, { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) throw Error();
        const mime = r.headers.get("content-type") || "";
        if (mime.includes("json")) {
          const animationData = await r.json();
          const { default: lottie } = await import(
            "lottie-web/build/player/lottie_light"
          );
          if (disposed || !host.current) return;
          const instance = lottie.loadAnimation({
            container: host.current,
            renderer: "svg",
            loop: true,
            autoplay: true,
            animationData,
          });
          destroy = () => instance.destroy();
          setKind("tgs");
        } else if (mime.includes("video")) setKind("webm");
      })
      .catch(() => {});
    return () => {
      disposed = true;
      controller.abort();
      destroy?.();
    };
  }, [id, animate, src]);
  return (
    <span className="telegram-emoji" aria-label={alt} role="img">
      <span
        ref={host}
        className={
          kind === "tgs" ? "emoji-animation" : "emoji-animation hidden"
        }
      />
      {kind === "webm" ? (
        <video
          src={src}
          autoPlay
          muted
          loop
          playsInline
          onError={() => {
            setKind("");
          }}
        />
      ) : (
        kind !== "tgs" &&
        (failed ? (
          <span>{alt}</span>
        ) : (
          <img
            src={`${src}?thumbnail=true`}
            alt={alt}
            loading="lazy"
            onError={() => setFailed(true)}
          />
        ))
      )}
    </span>
  );
}
const TelegramEmoji = Node.create({
  name: "telegramEmoji",
  group: "inline",
  inline: true,
  atom: true,
  selectable: true,
  addAttributes: () => ({
    id: { default: "" },
    alt: { default: "⭐" },
    scope: { default: "owner" },
  }),
  parseHTML: () => [{ tag: "span[data-telegram-emoji]" }],
  renderHTML: ({ HTMLAttributes }) => [
    "span",
    mergeAttributes(HTMLAttributes, {
      "data-telegram-emoji": HTMLAttributes.id,
    }),
    HTMLAttributes.alt,
  ],
  addNodeView: () =>
    ReactNodeViewRenderer(({ node }: NodeViewProps) => (
      <NodeViewWrapper as="span" className="emoji-node" contentEditable={false}>
        <EmojiArt
          id={node.attrs.id}
          alt={node.attrs.alt}
          scope={node.attrs.scope}
          animate
        />
      </NodeViewWrapper>
    )),
});
const makeMark = (name: string, cls: string, selector = `span.${cls}`) =>
  Mark.create({
    name,
    parseHTML: () => [{ tag: selector }],
    renderHTML: ({ HTMLAttributes }) => [
      "span",
      mergeAttributes(HTMLAttributes, { class: cls }),
      0,
    ],
  });
const Pre = Mark.create({
  name: "telegramPre",
  excludes: "_",
  code: true,
  addAttributes: () => ({ language: { default: "" } }),
  parseHTML: () => [{ tag: "pre" }],
  renderHTML: () => ["code", { class: "telegram-pre" }, 0],
});
const extensions = [
  StarterKit.configure({
    heading: false,
    bulletList: false,
    orderedList: false,
    listItem: false,
    listKeymap: false,
    horizontalRule: false,
    blockquote: false,
    codeBlock: false,
    link: {
      openOnClick: false,
      autolink: false,
      protocols: ["http", "https", "tg"],
    },
  }),
  TelegramEmoji,
  makeMark("spoiler", "telegram-spoiler"),
  makeMark("telegramQuote", "telegram-quote"),
  makeMark(
    "telegramExpandableQuote",
    "telegram-quote telegram-quote--expandable",
    "span.telegram-quote--expandable",
  ),
  Pre,
];
type Pack = {
  title: string;
  name: string;
  items: { id: string; emoji: string; format: string }[];
};
const lastPacks: Partial<Record<Scope, { link: string; pack: Pack }>> = {};
export function RichEditor({
  value,
  onChange,
  scope,
  label = "Текст сообщения",
}: {
  value: RichText;
  onChange: (v: RichText) => void;
  scope: Scope;
  label?: string;
}) {
  const [picker, setPicker] = useState(false),
    [link, setLink] = useState(lastPacks[scope]?.link || ""),
    [pack, setPack] = useState<Pack | null>(lastPacks[scope]?.pack || null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [linkEditor, setLinkEditor] = useState(false),
    [href, setHref] = useState("");
  const selection = useRef({ from: 1, to: 1 });
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const locked = unsupportedEntities(value.entities);
  const editor = useEditor(
    {
      extensions,
      content: toDocument(value, scope),
      editable: !locked,
      injectCSS: false,
      editorProps: {
        attributes: {
          role: "textbox",
          "aria-label": label,
          "aria-multiline": "true",
          class: "rich-input",
        },
      },
      onUpdate: ({ editor }) =>
        onChangeRef.current(fromDocument(editor.getJSON())),
    },
    [],
  );
  // Component keys change on record reload. Internal edits keep selection and history.
  useEffect(() => {
    editor?.setEditable(!locked);
  }, [editor, locked]);
  if (!editor) return <p>Загружаем редактор…</p>;
  const toolbar = (name: string, action: () => void, active = false) => (
    <button
      type="button"
      aria-pressed={active}
      disabled={locked}
      onMouseDown={(e) => e.preventDefault()}
      onClick={action}
    >
      {name}
    </button>
  );
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await (scope === "admin" ? adminApi : api)<Pack>(
        `/editor/emoji-sets?link=${encodeURIComponent(link)}`,
      );
      setPack(result);
      lastPacks[scope] = { link, pack: result };
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };
  return (
    <div className="rich-editor">
      <div
        className="rich-toolbar"
        role="group"
        aria-label="Форматирование текста"
      >
        {toolbar(
          "Жирный",
          () => editor.chain().focus().toggleBold().run(),
          editor.isActive("bold"),
        )}
        {toolbar(
          "Курсив",
          () => editor.chain().focus().toggleItalic().run(),
          editor.isActive("italic"),
        )}
        {toolbar(
          "Подчеркнуть",
          () => editor.chain().focus().toggleUnderline().run(),
          editor.isActive("underline"),
        )}
        {toolbar(
          "Зачеркнуть",
          () => editor.chain().focus().toggleStrike().run(),
          editor.isActive("strike"),
        )}
        {toolbar("Ссылка", () => {
          selection.current = {
            from: editor.state.selection.from,
            to: editor.state.selection.to,
          };
          setHref(editor.getAttributes("link").href || "");
          setLinkEditor(!linkEditor);
        })}
        {toolbar(
          "Спойлер",
          () => editor.chain().focus().toggleMark("spoiler").run(),
          editor.isActive("spoiler"),
        )}
        {toolbar(
          "Цитата",
          () =>
            editor
              .chain()
              .focus()
              .unsetMark("telegramExpandableQuote")
              .toggleMark("telegramQuote")
              .run(),
          editor.isActive("telegramQuote"),
        )}
        {toolbar(
          "Сворачиваемая цитата",
          () =>
            editor
              .chain()
              .focus()
              .unsetMark("telegramQuote")
              .toggleMark("telegramExpandableQuote")
              .run(),
          editor.isActive("telegramExpandableQuote"),
        )}
        {toolbar(
          "Код",
          () => editor.chain().focus().toggleCode().run(),
          editor.isActive("code"),
        )}
        {toolbar("Имя участника", () =>
          editor.chain().focus().insertContent("{name}").run(),
        )}
        {toolbar("Premium-эмодзи", () => {
          selection.current = {
            from: editor.state.selection.from,
            to: editor.state.selection.to,
          };
          setPicker(!picker);
        })}
      </div>
      {linkEditor && (
        <div className="inline-tools">
          <label>
            Адрес ссылки
            <input
              value={href}
              onChange={(e) => setHref(e.target.value)}
              placeholder="https://example.com"
            />
          </label>
          <button
            type="button"
            onClick={() => {
              if (
                href &&
                !/^https?:\/\//i.test(href) &&
                !href.startsWith("tg://")
              ) {
                setError("Ссылка должна начинаться с https://");
                return;
              }
              const c = editor
                .chain()
                .focus()
                .setTextSelection(selection.current);
              if (href) c.setLink({ href }).run();
              else c.unsetLink().run();
              setLinkEditor(false);
              setError("");
            }}
          >
            Применить ссылку
          </button>
        </div>
      )}
      {picker && (
        <section className="emoji-picker" aria-label="Premium-эмодзи">
          <p>Вставьте ссылку на набор Telegram и выберите эмодзи. Его ID и обычный символ сохранятся в шаблоне и перейдут в копию пользователя вместе с текстом и медиа.</p>
          <label>
            Ссылка на набор эмодзи
            <input
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://t.me/addemoji/…"
            />
          </label>
          <div className="composer-actions">
            <button
              type="button"
              disabled={loading}
              onClick={() => void load()}
            >
              {loading ? "Загружаем набор…" : "Показать эмодзи"}
            </button>
            <button
              type="button"
              onClick={() => {
                setPicker(false);
                editor.commands.focus();
              }}
            >
              Закрыть набор
            </button>
          </div>
          {pack && (
            <>
              <h4>{pack.title}</h4>
              <div className="emoji-grid">
                {pack.items.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    aria-label={`Вставить ${item.emoji}`}
                    onClick={() => {
                      editor
                        .chain()
                        .focus()
                        .setTextSelection(selection.current)
                        .insertContent({
                          type: "telegramEmoji",
                          attrs: { id: item.id, alt: item.emoji, scope },
                        })
                        .run();
                      setPicker(false);
                    }}
                  >
                    <EmojiArt id={item.id} alt={item.emoji} scope={scope} />
                  </button>
                ))}
              </div>
            </>
          )}
        </section>
      )}
      {error && (
        <p role="alert" className="composer-error">
          {error}
        </p>
      )}
      {locked ? (
        <>
          <p className="composer-notice">
            В этом тексте есть специальное Telegram-форматирование. Оно
            сохранится; редактирование доступно в боте.
          </p>
          <p className="rich-preview">{value.text}</p>
        </>
      ) : (
        <EditorContent editor={editor} />
      )}
      <small>
        {value.text.length} символов · {"{name}"} заменится именем участника
      </small>
    </div>
  );
}
export function RichPreview({
  value,
  scope,
}: {
  value: RichText;
  scope: Scope;
}) {
  const doc = toDocument(value, scope);
  return (
    <div className="rich-preview">
      {doc.content?.map((p, index) => (
        <p key={index}>
          {p.content?.map((n, i) => {
            let content: React.ReactNode =
              n.type === "hardBreak" ? (
                <br />
              ) : n.type === "telegramEmoji" ? (
                <EmojiArt
                  id={n.attrs!.id}
                  alt={n.attrs!.alt}
                  scope={scope}
                  animate
                />
              ) : (
                n.text
              );
            for (const m of n.marks || []) {
              if (m.type === "bold") content = <strong>{content}</strong>;
              else if (m.type === "italic") content = <em>{content}</em>;
              else if (m.type === "underline") content = <u>{content}</u>;
              else if (m.type === "strike") content = <s>{content}</s>;
              else if (m.type === "link")
                content = <span className="preview-link">{content}</span>;
              else
                content = (
                  <span
                    className={`telegram-${["telegramQuote", "telegramExpandableQuote"].includes(m.type) ? "quote" : m.type === "telegramPre" ? "pre" : m.type}`}
                  >
                    {content}
                  </span>
                );
            }
            return <React.Fragment key={i}>{content}</React.Fragment>;
          }) || <br />}
        </p>
      ))}
    </div>
  );
}
