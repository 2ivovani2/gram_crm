"use client";
import React, { useEffect, useState } from "react";
import {
  getCampaigns, createCampaign, startCampaign, stopCampaign, deleteCampaign,
  getAccounts, getChannels, getMessages,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import toast from "react-hot-toast";
import {
  Plus, Play, Square, Trash2, Zap, X, Check,
  Users, Hash, MessageSquare, Clock, RefreshCw, ChevronRight,
} from "lucide-react";

/* ── Select list ─────────────────────────────────────────────────────────────── */
function SelectList({ items, selected, onChange, renderItem, emptyText }) {
  const toggle = (id) =>
    onChange(selected.includes(id) ? selected.filter(s => s !== id) : [...selected, id]);

  if (!items.length) {
    return (
      <div style={{ textAlign: "center", padding: "24px 0", fontSize: 13, color: "var(--text-4)" }}>
        {emptyText}
      </div>
    );
  }

  return (
    <div className="select-list">
      {items.map(item => {
        const on = selected.includes(item.id);
        return (
          <div
            key={item.id}
            className={`select-item${on ? " select-item-on" : ""}`}
            onClick={() => toggle(item.id)}
          >
            <div className={`checkbox${on ? " checkbox-on" : ""}`}>
              {on && <Check size={9} color="var(--p-fg)" />}
            </div>
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {renderItem(item)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* ── Create modal ─────────────────────────────────────────────────────────────── */
function CreateModal({ onClose, onCreated }) {
  const [accounts, setAccounts] = useState([]);
  const [channels, setChannels] = useState([]);
  const [messages, setMessages] = useState([]);
  const [form, setForm]         = useState({
    name: "", account_ids: [], channel_ids: [], message_ids: [],
    min_delay: 30, max_delay: 120, comment_on_posts: false,
  });
  const [loading, setLoading]   = useState(false);
  const [step, setStep]         = useState(0);

  useEffect(() => {
    Promise.all([getAccounts(), getChannels(), getMessages()]).then(([a, c, m]) => {
      setAccounts(a.filter(x => x.status === "online"));
      setChannels(c.filter(x => x.is_active));
      setMessages(m.filter(x => x.is_active));
    });
  }, []);

  const STEPS = [
    { label: "Настройки", icon: Zap },
    { label: "Аккаунты",  icon: Users },
    { label: "Каналы",    icon: Hash },
    { label: "Шаблоны",   icon: MessageSquare },
  ];

  const canNext = () => {
    if (step === 0) return form.name.trim().length > 0;
    if (step === 1) return form.account_ids.length > 0;
    if (step === 2) return form.channel_ids.length > 0;
    return form.message_ids.length > 0;
  };

  const handleCreate = async () => {
    setLoading(true);
    try {
      await createCampaign({ ...form, min_delay: Number(form.min_delay), max_delay: Number(form.max_delay) });
      toast.success("Кампания создана!");
      onCreated(); onClose();
    } catch (e) { toast.error(e.response?.data?.detail || "Ошибка создания"); }
    finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-lg">
        <div className="modal-header">
          <span className="modal-title">Новая кампания</span>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="modal-body">
          {/* Steps */}
          <div className="steps">
            {STEPS.map((s, i) => (
              <React.Fragment key={s.label}>
                <div className={`step-dot ${i < step ? "step-done" : i === step ? "step-active" : "step-idle"}`}>
                  {i < step ? <Check size={9} /> : i + 1}
                </div>
                {i < STEPS.length - 1 && (
                  <div className={`step-line${i < step ? " step-line-done" : ""}`} />
                )}
              </React.Fragment>
            ))}
            <span style={{ fontSize: 12, color: "var(--text-3)", marginLeft: 8 }}>{STEPS[step].label}</span>
          </div>

          {/* Content */}
          <div style={{ minHeight: 220 }}>
            {step === 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="field">
                  <label className="field-label">Название кампании</label>
                  <input className="inp" placeholder="напр. Промо-волна #1"
                    value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                  {[
                    { key: "min_delay", label: "Мин. задержка (сек)", min: 5 },
                    { key: "max_delay", label: "Макс. задержка (сек)", min: 10 },
                  ].map(({ key, label, min }) => (
                    <div key={key} className="field">
                      <label className="field-label">{label}</label>
                      <input type="number" min={min} className="inp"
                        value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} />
                    </div>
                  ))}
                </div>
                <div
                  className="toggle-row"
                  onClick={() => setForm({ ...form, comment_on_posts: !form.comment_on_posts })}
                >
                  <div className={`toggle${form.comment_on_posts ? " toggle-on" : ""}`}>
                    <div className="toggle-thumb" />
                  </div>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-2)" }}>Режим комментариев</div>
                    <div style={{ fontSize: 12, color: "var(--text-4)" }}>Отвечать на последний пост канала</div>
                  </div>
                </div>
              </div>
            )}

            {step === 1 && (
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                    Выбрано {form.account_ids.length} из {accounts.length} · только онлайн
                  </span>
                  {accounts.length > 0 && (
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ height: 24, padding: "0 8px", fontSize: 11 }}
                      onClick={() => setForm(f => ({
                        ...f,
                        account_ids: f.account_ids.length === accounts.length ? [] : accounts.map(a => a.id),
                      }))}
                    >
                      {form.account_ids.length === accounts.length ? "Снять все" : "Выбрать все"}
                    </button>
                  )}
                </div>
                <SelectList
                  items={accounts}
                  selected={form.account_ids}
                  onChange={ids => setForm({ ...form, account_ids: ids })}
                  renderItem={a => `${a.first_name || ""} ${a.last_name || ""} (${a.phone})`.trim()}
                  emptyText="Нет онлайн-аккаунтов. Авторизуйте аккаунты сначала."
                />
              </div>
            )}

            {step === 2 && (
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: "var(--text-3)" }}>
                    Выбрано {form.channel_ids.length} из {channels.length}
                  </span>
                  {channels.length > 0 && (
                    <button
                      className="btn btn-ghost btn-sm"
                      style={{ height: 24, padding: "0 8px", fontSize: 11 }}
                      onClick={() => setForm(f => ({
                        ...f,
                        channel_ids: f.channel_ids.length === channels.length ? [] : channels.map(c => c.id),
                      }))}
                    >
                      {form.channel_ids.length === channels.length ? "Снять все" : "Выбрать все"}
                    </button>
                  )}
                </div>
                <SelectList
                  items={channels}
                  selected={form.channel_ids}
                  onChange={ids => setForm({ ...form, channel_ids: ids })}
                  renderItem={c => c.title || c.url}
                  emptyText="Нет активных каналов."
                />
              </div>
            )}

            {step === 3 && (
              <div>
                <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 8 }}>
                  Выбрано {form.message_ids.length} шаблонов · выбираются случайно
                </div>
                <SelectList
                  items={messages}
                  selected={form.message_ids}
                  onChange={ids => setForm({ ...form, message_ids: ids })}
                  renderItem={m => m.content.slice(0, 60) + (m.content.length > 60 ? "…" : "")}
                  emptyText="Нет активных шаблонов."
                />
              </div>
            )}
          </div>

          {/* Footer */}
          <div style={{ display: "flex", gap: 8, marginTop: 20, paddingTop: 16, borderTop: "1px solid var(--border)" }}>
            {step > 0 && (
              <button onClick={() => setStep(s => s - 1)} className="btn-ghost">← Назад</button>
            )}
            <div style={{ flex: 1 }} />
            {step < 3 ? (
              <button disabled={!canNext()} onClick={() => setStep(s => s + 1)} className="btn-primary">
                Далее <ChevronRight size={13} />
              </button>
            ) : (
              <button disabled={!canNext() || loading} onClick={handleCreate} className="btn-success">
                {loading ? <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} /> : <Check size={13} />}
                {loading ? "Создание…" : "Создать кампанию"}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Main page ─────────────────────────────────────────────────────────────── */
export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState([]);
  const [loading, setLoading]     = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [acting, setActing]       = useState({});

  const load = async () => {
    try { setCampaigns(await getCampaigns()); }
    catch { toast.error("Не удалось загрузить кампании"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id); }, []);

  const act = async (id, fn, label) => {
    setActing(a => ({ ...a, [id]: true }));
    try { await fn(); toast.success(label); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Ошибка"); }
    finally { setActing(a => ({ ...a, [id]: false })); }
  };

  const running = campaigns.filter(c => c.is_running).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
            Кампании
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-3)" }}>
            {campaigns.length} всего — {running} запущено
          </p>
        </div>
        <button onClick={() => setShowCreate(true)} className="btn-primary">
          <Plus size={13} /> Новая кампания
        </button>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 100, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : campaigns.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Zap size={18} /></div>
            <span className="empty-title">Нет кампаний</span>
            <span className="empty-sub">Создайте кампанию для начала автоматизации</span>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {campaigns.map(camp => (
            <div
              key={camp.id}
              className="card animate-fade-in"
              style={{
                borderColor: camp.is_running ? "var(--accent-border)" : undefined,
                background: camp.is_running ? "var(--accent-bg)" : undefined,
              }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
                {/* Status icon */}
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: "var(--r)",
                    background: camp.is_running ? "var(--accent-bg)" : "var(--raised)",
                    border: `1px solid ${camp.is_running ? "var(--accent-border)" : "var(--border)"}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    position: "relative",
                    flexShrink: 0,
                    color: camp.is_running ? "var(--accent)" : "var(--text-4)",
                  }}
                >
                  <Zap size={16} />
                  {camp.is_running && (
                    <span
                      style={{
                        position: "absolute",
                        top: -3,
                        right: -3,
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: "var(--green)",
                        border: "2px solid var(--bg)",
                      }}
                    />
                  )}
                </div>

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <span style={{ fontWeight: 600, fontSize: 14, color: "var(--text-1)" }}>{camp.name}</span>
                    <StatusBadge status={camp.is_running ? "running" : camp.status} />
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <Users size={11} /> {camp.account_ids.length} аккаунтов
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <Hash size={11} /> {camp.channel_ids.length} каналов
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <MessageSquare size={11} /> {camp.message_ids.length} шаблонов
                    </span>
                    <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <Clock size={11} /> {camp.min_delay}–{camp.max_delay}с
                    </span>
                    {camp.comment_on_posts && (
                      <span style={{ color: "var(--accent)", fontSize: 11 }}>● комментарии</span>
                    )}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-3)" }}>
                    Отправлено:{" "}
                    <strong style={{ color: "var(--text-2)", fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                      {camp.total_sent.toLocaleString("ru-RU")}
                    </strong>
                    {camp.started_at && (
                      <span style={{ color: "var(--text-4)", marginLeft: 8 }}>
                        {new Date(camp.started_at).toLocaleString("ru-RU")}
                      </span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: "flex", gap: 6, flexShrink: 0, alignItems: "center" }}>
                  {camp.is_running ? (
                    <button
                      disabled={acting[camp.id]}
                      onClick={() => act(camp.id, () => stopCampaign(camp.id), "Кампания остановлена")}
                      className="btn-danger"
                    >
                      <Square size={11} /> Стоп
                    </button>
                  ) : (
                    <button
                      disabled={acting[camp.id]}
                      onClick={() => act(camp.id, () => startCampaign(camp.id), "Кампания запущена!")}
                      className="btn-success"
                    >
                      <Play size={11} /> Старт
                    </button>
                  )}
                  <button
                    disabled={camp.is_running || acting[camp.id]}
                    className="btn-icon"
                    onClick={() => { if (!confirm("Удалить эту кампанию?")) return; act(camp.id, () => deleteCampaign(camp.id), "Удалено"); }}
                    onMouseEnter={e => { if (!e.currentTarget.disabled) { e.currentTarget.style.background = "var(--red-bg)"; e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red-border)"; } }}
                    onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
    </div>
  );
}
