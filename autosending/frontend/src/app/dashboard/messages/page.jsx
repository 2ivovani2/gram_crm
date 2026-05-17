"use client";
import { useEffect, useState } from "react";
import { getMessages, addMessage, updateMessage, deleteMessage } from "@/lib/api";
import toast from "react-hot-toast";
import {
  Plus, Trash2, Edit3, Check, X, MessageSquare,
  RefreshCw, PenLine, Tag, Eye, Copy, Sparkles, FlaskConical,
} from "lucide-react";

function PreviewModal({ text, onClose }) {
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <div className="modal-title">Предпросмотр</div>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="modal-body">
          <div style={{
            background: "var(--sunken)", border: "1px solid var(--line-2)", borderRadius: 12,
            padding: "14px 16px", fontSize: 13.5, lineHeight: 1.6, color: "var(--ink-1)",
            whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "var(--sans)",
          }}>
            {text}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 10 }}>
            {text.length} символов
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}

function MessageCard({ msg, onSave, onDelete, onToggle, onDuplicate, onPreview }) {
  const [editing, setEditing] = useState(false);
  const [text, setText]       = useState(msg.content);
  const [cat,  setCat]        = useState(msg.category || "");
  const [saving, setSaving]   = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await onSave(msg.id, { content: text, category: cat || null });
      setEditing(false);
      toast.success("Сохранено");
    } catch { toast.error("Ошибка сохранения"); }
    finally { setSaving(false); }
  };

  const cancel = () => { setText(msg.content); setCat(msg.category || ""); setEditing(false); };

  return (
    <div
      className="card animate-fade-in"
      style={{
        opacity: !editing && !msg.is_active ? 0.6 : 1,
        borderColor: editing ? "var(--gold-edge)" : undefined,
        boxShadow: editing ? "0 0 0 3px var(--gold-tint), var(--shadow-2)" : undefined,
        transition: "opacity 200ms, box-shadow 200ms, border-color 200ms",
      }}
    >
      <div className="card-body">
        {editing ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <textarea
              rows={4}
              className="inp"
              value={text}
              onChange={e => setText(e.target.value)}
              autoFocus
            />
            <div style={{ position: "relative" }}>
              <Tag size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input className="inp" style={{ paddingLeft: 36 }} placeholder="Категория (необязательно)"
                value={cat} onChange={e => setCat(e.target.value)} />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={cancel} className="btn-ghost btn-sm">
                <X size={12} /> Отмена
              </button>
              <button onClick={save} disabled={saving} className="btn-primary btn-sm">
                {saving ? <RefreshCw size={12} style={{ animation: "spin 0.7s linear infinite" }} /> : <Check size={12} strokeWidth={2.5} />}
                Сохранить
              </button>
            </div>
          </div>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 12 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {msg.category && (
                  <span
                    style={{
                      padding: "2px 9px",
                      background: "var(--plum-tint)",
                      border: "1px solid var(--plum-edge)",
                      borderRadius: 100,
                      fontSize: 11,
                      fontWeight: 500,
                      color: "var(--plum)",
                    }}
                  >
                    #{msg.category}
                  </span>
                )}
                <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>
                  использован {msg.use_count}×
                </span>
              </div>

              <button
                onClick={() => onToggle(msg.id, !msg.is_active)}
                title={msg.is_active ? "Деактивировать" : "Активировать"}
                style={{
                  width: 32, height: 18,
                  background: msg.is_active ? "linear-gradient(180deg, var(--emerald), #6fa67c)" : "var(--surface-3)",
                  border: `1px solid ${msg.is_active ? "rgba(142,201,154,0.5)" : "var(--line-3)"}`,
                  borderRadius: 10, position: "relative", cursor: "pointer", flexShrink: 0,
                  padding: 0,
                }}
              >
                <div style={{
                  width: 12, height: 12, borderRadius: "50%", background: "#fafaf9",
                  position: "absolute", top: 2, left: 2,
                  transform: msg.is_active ? "translateX(14px)" : "none",
                  transition: "transform 200ms cubic-bezier(0.34,1.56,0.64,1)",
                  boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
                }} />
              </button>
            </div>

            <p
              style={{
                fontSize: 13.5,
                whiteSpace: "pre-wrap",
                fontFamily: "var(--sans)",
                lineHeight: 1.6,
                color: "var(--ink-2)",
                wordBreak: "break-word",
                margin: 0,
              }}
            >
              {msg.content}
            </p>

            <div
              style={{
                display: "flex", gap: 4,
                marginTop: 14, paddingTop: 12,
                borderTop: "1px solid var(--line-1)",
                justifyContent: "flex-end",
              }}
            >
              <button className="btn-icon" style={{ width: 30, height: 30 }} title="Предпросмотр" onClick={() => onPreview(msg.content)}><Eye size={12} /></button>
              <button className="btn-icon" style={{ width: 30, height: 30 }} title="Дублировать" onClick={() => onDuplicate(msg)}><Copy size={12} /></button>
              <button className="btn-icon" style={{ width: 30, height: 30 }} title="Редактировать" onClick={() => setEditing(true)}>
                <Edit3 size={12} />
              </button>
              <button
                className="btn-icon"
                style={{ width: 30, height: 30 }}
                onClick={() => onDelete(msg.id)}
                title="Удалить"
                onMouseEnter={e => { e.currentTarget.style.background = "var(--coral-tint)"; e.currentTarget.style.color = "var(--coral)"; e.currentTarget.style.borderColor = "var(--coral-edge)"; }}
                onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
              >
                <Trash2 size={12} />
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function MessagesPage() {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [text, setText]         = useState("");
  const [cat, setCat]           = useState("");
  const [adding, setAdding]     = useState(false);
  const [previewText, setPreviewText] = useState(null);

  const load = async () => {
    try { setMessages(await getMessages()); }
    catch { toast.error("Не удалось загрузить шаблоны"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!text.trim()) return;
    setAdding(true);
    try { await addMessage({ content: text.trim(), category: cat || null }); setText(""); setCat(""); toast.success("Шаблон добавлен"); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Ошибка"); }
    finally { setAdding(false); }
  };

  const handleSave   = async (id, data) => { await updateMessage(id, data); await load(); };
  const handleToggle = async (id, is_active) => { await updateMessage(id, { is_active }); load(); };
  const handleDelete = async (id) => {
    if (!confirm("Удалить этот шаблон?")) return;
    await deleteMessage(id); toast.success("Удалено"); load();
  };
  const handleDuplicate = async (msg) => {
    try {
      await addMessage({ content: msg.content, category: msg.category || null });
      toast.success("Шаблон продублирован");
      load();
    } catch (e) { toast.error(e.response?.data?.detail || "Ошибка дублирования"); }
  };

  const active = messages.filter(m => m.is_active).length;

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Шаблоны сообщений</h1>
          <p className="page-sub">
            {messages.length} шаблон{messages.length === 1 ? "" : "ов"} · {active} активны · случайная ротация без повторов
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost" onClick={() => toast("🚧 AI-генератор появится в следующем обновлении")}><Sparkles size={14} /> AI-генератор</button>
          <button className="btn-ghost" onClick={() => toast("🚧 A/B-тестирование появится в следующем обновлении")}><FlaskConical size={14} /> A/B-тест</button>
        </div>
      </div>

      {/* Composer */}
      <div className="card card-no-hover">
        <div style={{ padding: "18px 22px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13.5, fontWeight: 600, color: "var(--ink-1)" }}>
              <PenLine size={14} style={{ color: "var(--ink-3)" }} />
              Новый шаблон
            </div>
            <span style={{ fontSize: 11.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>
              {text.length}/1024
            </span>
          </div>
          <textarea
            rows={4}
            className="inp"
            placeholder={"Введите текст сообщения. Шаблоны выбираются случайно — добавьте 3–5 разных по тону и длине вариантов для естественности."}
            value={text}
            onChange={e => setText(e.target.value)}
          />
          <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
            <div style={{ position: "relative", flex: 1, maxWidth: 280 }}>
              <Tag size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input className="inp" style={{ paddingLeft: 36 }} placeholder="Категория · необязательно"
                value={cat} onChange={e => setCat(e.target.value)} />
            </div>
            <div style={{ flex: 1 }} />
            <button className="btn-ghost" disabled={!text.trim()} onClick={() => setPreviewText(text)}><Eye size={13} /> Предпросмотр</button>
            <button onClick={handleAdd} disabled={adding || !text.trim()} className="btn-primary">
              {adding ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Plus size={14} />}
              {adding ? "Добавление…" : "Добавить шаблон"}
            </button>
          </div>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 12 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 160, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : messages.length === 0 ? (
        <div className="card card-no-hover">
          <div className="empty">
            <div className="empty-icon"><MessageSquare size={22} /></div>
            <span className="empty-title">Пока ни одного шаблона</span>
            <span className="empty-sub">Добавьте 3–5 вариантов для естественной ротации</span>
          </div>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(360px, 1fr))", gap: 12 }}>
          {messages.map(msg => (
            <MessageCard
              key={msg.id}
              msg={msg}
              onSave={handleSave}
              onDelete={handleDelete}
              onToggle={handleToggle}
              onDuplicate={handleDuplicate}
              onPreview={setPreviewText}
            />
          ))}
        </div>
      )}

      {previewText !== null && (
        <PreviewModal text={previewText} onClose={() => setPreviewText(null)} />
      )}
    </div>
  );
}
