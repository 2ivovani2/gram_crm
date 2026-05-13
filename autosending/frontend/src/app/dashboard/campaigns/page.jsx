"use client";
import React, { useEffect, useState, Fragment } from "react";
import {
  getCampaigns, createCampaign, startCampaign, stopCampaign, deleteCampaign,
  getAccounts, getChannels, getMessages,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import toast from "react-hot-toast";
import {
  Plus, Play, Square, Trash2, Zap, X, Check,
  Users, Hash, MessageSquare, Clock, RefreshCw, ChevronRight, ChevronLeft,
  Settings, MessageCircle, MoreHorizontal, Info, Sparkles, Layers,
} from "lucide-react";

/* ── Avatar (mini, for account stack) ───────────────────────────────────── */
function MiniAvatar({ name, phone, size = 26 }) {
  const seed = (name || phone || "?").split("").reduce((a,c)=>a + c.charCodeAt(0), 0);
  const hues = [
    ["#5a3a14", "#8b6326"], ["#1c3a4a", "#2d5a6e"], ["#4a2c3d", "#6e3d5a"],
    ["#2c4a2d", "#3d6e3f"], ["#4a3c1c", "#7a6224"], ["#3a2c4a", "#5a3d7a"],
  ];
  const [a, b] = hues[seed % hues.length];
  const initials = (name || phone || "?")
    .split(/[\s+\-_]/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase() ?? "").join("") || "?";
  return (
    <div style={{
      width: size, height: size, borderRadius: 7,
      background: `linear-gradient(135deg, ${a}, ${b})`,
      color: "var(--ink-1)", fontSize: 9, fontWeight: 700, fontFamily: "var(--mono)",
      display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
    }}>
      {initials}
    </div>
  );
}

/* ── Select list ─────────────────────────────────────────────────────────── */
function SelectList({ items, selected, onChange, renderItem, emptyText }) {
  const toggle = (id) =>
    onChange(selected.includes(id) ? selected.filter(s => s !== id) : [...selected, id]);

  if (!items.length) {
    return (
      <div style={{ textAlign: "center", padding: "32px 0", fontSize: 13, color: "var(--ink-4)" }}>
        {emptyText}
      </div>
    );
  }

  return (
    <div className="select-list">
      {items.map(item => {
        const on = selected.includes(item.id);
        return (
          <div key={item.id} className={`select-item${on ? " on" : ""}`} onClick={() => toggle(item.id)}>
            <div className={`checkbox${on ? " on" : ""}`}>
              {on && <Check size={11} strokeWidth={3} />}
            </div>
            <div style={{ flex: 1, overflow: "hidden", display: "flex", alignItems: "center", gap: 10 }}>
              {renderItem(item)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Create modal ────────────────────────────────────────────────────────── */
function CreateModal({ onClose, onCreated }) {
  const [accounts, setAccounts] = useState([]);
  const [channels, setChannels] = useState([]);
  const [messages, setMessages] = useState([]);
  const [form, setForm] = useState({
    name: "",
    account_ids: [],
    channel_ids: [],
    message_ids: [],
    min_delay: 45,
    max_delay: 180,
    comment_on_posts: false,
  });
  const [loading, setLoading] = useState(false);
  const [step, setStep] = useState(0);

  useEffect(() => {
    Promise.all([getAccounts(), getChannels(), getMessages()]).then(([a, c, m]) => {
      setAccounts(a.filter(x => x.status === "online"));
      setChannels(c.filter(x => x.is_active));
      setMessages(m.filter(x => x.is_active));
    });
  }, []);

  const STEPS = [
    { label: "Настройки", icon: Settings },
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
      await createCampaign({
        ...form,
        min_delay: Number(form.min_delay),
        max_delay: Number(form.max_delay),
      });
      toast.success("Кампания создана");
      onCreated(); onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Ошибка создания");
    } finally { setLoading(false); }
  };

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-lg animate-in">
        <div className="modal-header">
          <div>
            <div className="modal-title">Новая кампания</div>
            <div className="modal-subtitle">Аккаунты работают параллельно с заданной задержкой</div>
          </div>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="modal-body">
          {/* Steps */}
          <div className="steps">
            {STEPS.map((s, i) => {
              const Icon = s.icon;
              return (
                <Fragment key={s.label}>
                  <div className={`step ${i < step ? "step-done" : i === step ? "step-active" : ""}`}>
                    <div className="step-dot">
                      {i < step ? <Check size={11} strokeWidth={2.5} /> : <Icon size={11} />}
                    </div>
                    <span className="step-label">{s.label}</span>
                  </div>
                  {i < STEPS.length - 1 && (
                    <div className={`step-line${i < step ? " done" : ""}`} />
                  )}
                </Fragment>
              );
            })}
          </div>

          {/* Content */}
          <div style={{ minHeight: 280 }}>
            {step === 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <div className="field">
                  <label className="field-label">Название кампании</label>
                  <div style={{ position: "relative" }}>
                    <Sparkles size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
                    <input className="inp" style={{ paddingLeft: 36 }} placeholder="напр. Промо-волна #3"
                      value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} autoFocus />
                  </div>
                  <span className="field-hint">Будет видно в обзоре и логах</span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                  <div className="field">
                    <label className="field-label">Задержка от <span className="field-label-tag">сек</span></label>
                    <input type="number" min={5} className="inp" style={{ fontFamily: "var(--mono)" }}
                      value={form.min_delay} onChange={e => setForm({ ...form, min_delay: e.target.value })} />
                  </div>
                  <div className="field">
                    <label className="field-label">Задержка до <span className="field-label-tag">сек</span></label>
                    <input type="number" min={10} className="inp" style={{ fontFamily: "var(--mono)" }}
                      value={form.max_delay} onChange={e => setForm({ ...form, max_delay: e.target.value })} />
                  </div>
                </div>

                <div style={{
                  padding: "12px 14px",
                  background: "var(--azure-tint)", border: "1px solid var(--azure-edge)",
                  borderRadius: 9, display: "flex", gap: 10, alignItems: "flex-start",
                  fontSize: 12, color: "var(--ink-2)", lineHeight: 1.55,
                }}>
                  <Info size={14} style={{ color: "var(--azure)", marginTop: 1, flexShrink: 0 }} />
                  <div>
                    <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>Рекомендации: </strong>
                    новые аккаунты — 120–300с, старые (6+ мес) — 30–60с. Слишком быстрая отправка приводит к FloodWait.
                  </div>
                </div>

                <div
                  className="toggle-row"
                  onClick={() => setForm({ ...form, comment_on_posts: !form.comment_on_posts })}
                >
                  <div className={`toggle${form.comment_on_posts ? " toggle-on" : ""}`}>
                    <div className="toggle-thumb" />
                  </div>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink-1)" }}>Режим комментариев</div>
                    <div style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 2 }}>Отвечать на последний пост канала вместо ЛС</div>
                  </div>
                </div>
              </div>
            )}

            {step === 1 && (
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
                    Выбрано <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{form.account_ids.length}</strong> из {accounts.length} онлайн-аккаунтов
                  </span>
                  {accounts.length > 0 && (
                    <button
                      className="btn-ghost btn-sm"
                      onClick={() => setForm(f => ({
                        ...f,
                        account_ids: f.account_ids.length === accounts.length ? [] : accounts.map(a => a.id),
                      }))}
                    >
                      {form.account_ids.length === accounts.length ? "Снять всё" : "Выбрать всё"}
                    </button>
                  )}
                </div>
                <SelectList
                  items={accounts}
                  selected={form.account_ids}
                  onChange={ids => setForm({ ...form, account_ids: ids })}
                  emptyText="Нет онлайн-аккаунтов. Авторизуйте аккаунты сначала."
                  renderItem={a => (
                    <>
                      <MiniAvatar name={a.first_name || a.phone} size={28} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, color: "var(--ink-1)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {(a.first_name || "") + " " + (a.last_name || "")}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{a.phone}</div>
                      </div>
                      <span style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>
                        {(a.messages_sent ?? 0).toLocaleString("ru-RU")}
                      </span>
                    </>
                  )}
                />
              </div>
            )}

            {step === 2 && (
              <div>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
                  <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
                    Выбрано <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{form.channel_ids.length}</strong> из {channels.length}
                  </span>
                  {channels.length > 0 && (
                    <button
                      className="btn-ghost btn-sm"
                      onClick={() => setForm(f => ({
                        ...f,
                        channel_ids: f.channel_ids.length === channels.length ? [] : channels.map(c => c.id),
                      }))}
                    >
                      {form.channel_ids.length === channels.length ? "Снять всё" : "Выбрать всё"}
                    </button>
                  )}
                </div>
                <SelectList
                  items={channels}
                  selected={form.channel_ids}
                  onChange={ids => setForm({ ...form, channel_ids: ids })}
                  emptyText="Нет активных каналов."
                  renderItem={c => (
                    <>
                      <div style={{
                        width: 28, height: 28, borderRadius: 7,
                        background: "var(--emerald-tint)", border: "1px solid var(--emerald-edge)",
                        color: "var(--emerald)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
                      }}>
                        <Hash size={13} />
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, color: "var(--ink-1)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title || c.url}</div>
                        <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{c.url}</div>
                      </div>
                    </>
                  )}
                />
              </div>
            )}

            {step === 3 && (
              <div>
                <div style={{ fontSize: 13, color: "var(--ink-2)", marginBottom: 10 }}>
                  Выбрано <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{form.message_ids.length}</strong> шаблонов · выбираются случайно
                </div>
                <SelectList
                  items={messages}
                  selected={form.message_ids}
                  onChange={ids => setForm({ ...form, message_ids: ids })}
                  emptyText="Нет активных шаблонов."
                  renderItem={m => (
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
                        {m.category && (
                          <span style={{ padding: "1px 7px", background: "var(--plum-tint)", border: "1px solid var(--plum-edge)", color: "var(--plum)", borderRadius: 100, fontSize: 10, fontWeight: 500 }}>
                            #{m.category}
                          </span>
                        )}
                        <span style={{ fontSize: 10.5, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{m.use_count}×</span>
                      </div>
                      <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                        {m.content}
                      </div>
                    </div>
                  )}
                />
              </div>
            )}
          </div>
        </div>

        <div className="modal-footer">
          {step > 0 && (
            <button onClick={() => setStep(s => s - 1)} className="btn-ghost">
              <ChevronLeft size={14} /> Назад
            </button>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 12, color: "var(--ink-4)", marginRight: 8 }}>
            Шаг {step + 1} из {STEPS.length}
          </span>
          {step < 3 ? (
            <button disabled={!canNext()} onClick={() => setStep(s => s + 1)} className="btn-primary">
              Далее <ChevronRight size={14} />
            </button>
          ) : (
            <button disabled={!canNext() || loading} onClick={handleCreate} className="btn-primary">
              {loading
                ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} />
                : <Sparkles size={14} />
              }
              {loading ? "Создание…" : "Создать и запустить"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Main page ───────────────────────────────────────────────────────────── */
export default function CampaignsPage() {
  const [campaigns, setCampaigns]   = useState([]);
  const [accounts,  setAccounts]    = useState([]);
  const [loading, setLoading]       = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [acting, setActing]         = useState({});

  const load = async () => {
    try {
      const [cs, as] = await Promise.all([getCampaigns(), getAccounts()]);
      setCampaigns(cs);
      setAccounts(as);
    }
    catch { toast.error("Не удалось загрузить кампании"); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const act = async (id, fn, label) => {
    setActing(a => ({ ...a, [id]: true }));
    try { await fn(); toast.success(label); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Ошибка"); }
    finally { setActing(a => ({ ...a, [id]: false })); }
  };

  const running = campaigns.filter(c => c.is_running).length;

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Кампании</h1>
          <p className="page-sub">
            {campaigns.length} всего · {running} запущено · параллельная работа аккаунтов
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            <Plus size={14} /> Новая кампания
          </button>
        </div>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 120, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : campaigns.length === 0 ? (
        <div className="card card-no-hover">
          <div className="empty">
            <div className="empty-icon"><Zap size={22} /></div>
            <span className="empty-title">Пока ни одной кампании</span>
            <span className="empty-sub">Создайте кампанию, чтобы начать рассылку</span>
            <div style={{ marginTop: 14 }}>
              <button onClick={() => setShowCreate(true)} className="btn-primary"><Plus size={14} /> Создать</button>
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {campaigns.map((camp, i) => {
            const campAccounts = accounts.filter(a => camp.account_ids.includes(a.id));
            return (
              <div
                key={camp.id}
                className="card animate-fade-in"
                style={{
                  borderColor: camp.is_running ? "var(--gold-edge)" : undefined,
                  background: camp.is_running ? "linear-gradient(180deg, var(--gold-tint), rgba(232,201,138,0.015))" : undefined,
                  animationDelay: `${i * 35}ms`,
                }}
              >
                <div className="card-body" style={{ padding: "22px 24px", display: "flex", alignItems: "flex-start", gap: 18 }}>
                  {/* Visual */}
                  <div
                    style={{
                      position: "relative",
                      width: 48, height: 48, borderRadius: 12,
                      background: camp.is_running
                        ? "linear-gradient(180deg, var(--gold-tint), rgba(232,201,138,0.02))"
                        : "linear-gradient(180deg, var(--surface-2), var(--surface-1))",
                      border: `1px solid ${camp.is_running ? "var(--gold-edge)" : "var(--line-2)"}`,
                      display: "flex", alignItems: "center", justifyContent: "center",
                      color: camp.is_running ? "var(--gold-hi)" : "var(--ink-3)",
                      flexShrink: 0,
                      boxShadow: camp.is_running
                        ? "0 0 20px rgba(232,201,138,0.18), 0 1px 0 0 rgba(255,250,240,0.05) inset"
                        : "var(--shadow-1)",
                    }}
                  >
                    <Zap size={20} strokeWidth={1.8} />
                    {camp.is_running && (
                      <span
                        style={{
                          position: "absolute", top: -3, right: -3,
                          width: 12, height: 12, borderRadius: "50%",
                          background: "var(--emerald)", border: "2.5px solid var(--bg)",
                          boxShadow: "0 0 6px rgba(142,201,154,0.6)",
                        }}
                      />
                    )}
                  </div>

                  {/* Info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6, flexWrap: "wrap" }}>
                      <span style={{ fontSize: 16, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.014em", fontFamily: "var(--display)" }}>
                        {camp.name}
                      </span>
                      <StatusBadge status={camp.is_running ? "running" : camp.status} pulse={camp.is_running} />
                      {camp.comment_on_posts && (
                        <span className="badge badge-info"><MessageCircle size={11} /> комментарии</span>
                      )}
                    </div>

                    <div style={{ display: "flex", flexWrap: "wrap", gap: "5px 20px", fontSize: 12.5, color: "var(--ink-3)", marginBottom: 10 }}>
                      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <Users size={12} /> <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{camp.account_ids.length}</strong> аккаунтов
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <Hash size={12} /> <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{camp.channel_ids.length}</strong> каналов
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <MessageSquare size={12} /> <strong style={{ color: "var(--ink-1)", fontWeight: 600 }}>{camp.message_ids.length}</strong> шаблонов
                      </span>
                      <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                        <Clock size={12} /> <span style={{ fontFamily: "var(--mono)" }}>{camp.min_delay}–{camp.max_delay}с</span>
                      </span>
                    </div>

                    <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
                      {/* Account stack */}
                      {campAccounts.length > 0 && (
                        <div style={{ display: "flex", alignItems: "center" }}>
                          {campAccounts.slice(0, 4).map((a, idx) => (
                            <div key={a.id} style={{ marginLeft: idx === 0 ? 0 : -8, zIndex: 4 - idx, border: "2px solid var(--bg)", borderRadius: 7 }}>
                              <MiniAvatar name={a.first_name || a.phone} size={26} />
                            </div>
                          ))}
                          {campAccounts.length > 4 && (
                            <div style={{
                              marginLeft: -8, width: 26, height: 26, borderRadius: 7,
                              background: "var(--surface-3)", border: "2px solid var(--bg)",
                              color: "var(--ink-3)", fontSize: 10, fontWeight: 600, fontFamily: "var(--mono)",
                              display: "flex", alignItems: "center", justifyContent: "center",
                            }}>+{campAccounts.length - 4}</div>
                          )}
                        </div>
                      )}

                      <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                        <span style={{ fontSize: 10.5, color: "var(--ink-5)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600 }}>Отправлено</span>
                        <span className="t-numeric" style={{ fontSize: 17, color: "var(--ink-1)", lineHeight: 1 }}>
                          {(camp.total_sent ?? 0).toLocaleString("ru-RU")}
                        </span>
                      </div>

                      {camp.started_at && (
                        <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                          <span style={{ fontSize: 10.5, color: "var(--ink-5)", letterSpacing: "0.08em", textTransform: "uppercase", fontWeight: 600 }}>Запущена</span>
                          <span style={{ fontSize: 12, color: "var(--ink-2)", fontFamily: "var(--mono)" }}>
                            {new Date(camp.started_at).toLocaleString("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}
                          </span>
                        </div>
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
                        <Square size={11} strokeWidth={2.5} /> Стоп
                      </button>
                    ) : (
                      <button
                        disabled={acting[camp.id]}
                        onClick={() => act(camp.id, () => startCampaign(camp.id), "Кампания запущена")}
                        className="btn-success"
                      >
                        <Play size={11} strokeWidth={2.5} /> Старт
                      </button>
                    )}
                    <button
                      disabled={camp.is_running || acting[camp.id]}
                      className="btn-icon"
                      onClick={() => {
                        if (!confirm("Удалить эту кампанию?")) return;
                        act(camp.id, () => deleteCampaign(camp.id), "Удалено");
                      }}
                      title="Удалить"
                      onMouseEnter={e => { if (!e.currentTarget.disabled) { e.currentTarget.style.background = "var(--coral-tint)"; e.currentTarget.style.color = "var(--coral)"; e.currentTarget.style.borderColor = "var(--coral-edge)"; } }}
                      onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showCreate && <CreateModal onClose={() => setShowCreate(false)} onCreated={load} />}
    </div>
  );
}
