"use client";
import { useEffect, useState } from "react";
import { getMessages, addMessage, updateMessage, deleteMessage } from "@/lib/api";
import toast from "react-hot-toast";
import { Plus, Trash2, Edit2, Check, X, MessageSquare, ToggleRight, ToggleLeft, RefreshCw } from "lucide-react";

function MessageCard({ msg, onSave, onDelete, onToggle }) {
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
        opacity: !editing && !msg.is_active ? 0.55 : 1,
        borderColor: editing ? "var(--accent-border)" : undefined,
        boxShadow: editing ? "0 0 0 3px var(--accent-bg)" : undefined,
        transition: "opacity 200ms",
      }}
    >
      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <textarea
            rows={4}
            className="inp"
            style={{ resize: "none" }}
            value={text}
            onChange={e => setText(e.target.value)}
            autoFocus
          />
          <input className="inp" placeholder="Категория (необязательно)"
            value={cat} onChange={e => setCat(e.target.value)} />
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={save} disabled={saving} className="btn-primary" style={{ height: 30, fontSize: 12 }}>
              {saving ? <RefreshCw size={11} style={{ animation: "spin 0.65s linear infinite" }} /> : <Check size={11} />}
              Сохранить
            </button>
            <button onClick={cancel} className="btn-ghost" style={{ height: 30, fontSize: 12 }}>
              <X size={11} /> Отмена
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <pre style={{ fontSize: 13, whiteSpace: "pre-wrap", fontFamily: "var(--sans)", lineHeight: 1.6, color: "var(--text-2)", wordBreak: "break-word" }}>
              {msg.content}
            </pre>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10, alignItems: "center" }}>
              {msg.category && (
                <span
                  style={{
                    padding: "2px 8px",
                    background: "var(--violet-bg)",
                    border: "1px solid var(--violet-border)",
                    borderRadius: "100px",
                    fontSize: 11,
                    fontWeight: 500,
                    color: "var(--violet)",
                  }}
                >
                  #{msg.category}
                </span>
              )}
              <span style={{ fontSize: 11, color: "var(--text-4)" }}>
                Использовано: <strong style={{ color: "var(--text-3)", fontWeight: 500 }}>{msg.use_count}×</strong>
              </span>
              <span style={{ fontSize: 11, color: msg.is_active ? "var(--green)" : "var(--text-4)" }}>
                {msg.is_active ? "Активен" : "Неактивен"}
              </span>
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 4, flexShrink: 0 }}>
            <button
              className="btn-icon"
              onClick={() => onToggle(msg.id, !msg.is_active)}
              title="Переключить"
              style={{ color: msg.is_active ? "var(--accent)" : "var(--text-4)" }}
            >
              {msg.is_active ? <ToggleRight size={15} /> : <ToggleLeft size={15} />}
            </button>
            <button className="btn-icon" onClick={() => setEditing(true)} title="Редактировать">
              <Edit2 size={13} />
            </button>
            <button
              className="btn-icon"
              onClick={() => onDelete(msg.id)}
              title="Удалить"
              onMouseEnter={e => { e.currentTarget.style.background = "var(--red-bg)"; e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red-border)"; }}
              onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function MessagesPage() {
  const [messages, setMessages] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [text, setText]         = useState("");
  const [cat, setCat]           = useState("");
  const [adding, setAdding]     = useState(false);

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

  const active = messages.filter(m => m.is_active).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div>
        <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
          Шаблоны
        </h1>
        <p style={{ fontSize: 13, color: "var(--text-3)" }}>
          {messages.length} шаблонов — {active} активных · случайная ротация без повторений
        </p>
      </div>

      {/* Add form */}
      <div className="card">
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-3)", marginBottom: 10 }}>Новый шаблон</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <textarea
            rows={4}
            className="inp"
            style={{ resize: "none" }}
            placeholder={"Введите текст сообщения...\n\nДобавьте несколько вариантов для разнообразия."}
            value={text}
            onChange={e => setText(e.target.value)}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <input className="inp" style={{ flex: 1 }} placeholder="Категория (необязательно)"
              value={cat} onChange={e => setCat(e.target.value)} />
            <button onClick={handleAdd} disabled={adding || !text.trim()} className="btn-primary">
              {adding ? <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} /> : <Plus size={13} />}
              {adding ? "Добавление…" : "Добавить"}
            </button>
          </div>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 100, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : messages.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><MessageSquare size={18} /></div>
            <span className="empty-title">Нет шаблонов</span>
            <span className="empty-sub">Добавьте 3–5 вариантов для лучшей ротации</span>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {messages.map(msg => (
            <MessageCard key={msg.id} msg={msg} onSave={handleSave} onDelete={handleDelete} onToggle={handleToggle} />
          ))}
        </div>
      )}
    </div>
  );
}
