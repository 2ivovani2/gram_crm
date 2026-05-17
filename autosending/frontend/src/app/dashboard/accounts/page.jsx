"use client";
import { useEffect, useState, Fragment } from "react";
import {
  getAccounts, addAccount, deleteAccount,
  sendCode, verifyCode, updateProfile, uploadPhoto, syncAccount,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import toast from "react-hot-toast";
import {
  Plus, Trash2, RefreshCw, Edit3, X, Check, UserPlus,
  Phone, Key, User, ChevronRight, ChevronLeft, Smartphone,
  Hash, MoreHorizontal, CheckCircle2, RotateCcw, AlertTriangle, Upload,
} from "lucide-react";

/* ── Account tier info ────────────────────────────────────────────────────── */
function getTier(sent) {
  if (sent >= 5000) return { label: "Ветеран",    daily: 550, color: "#9b6dff", bg: "rgba(155,109,255,0.12)", border: "rgba(155,109,255,0.25)" };
  if (sent >= 2000) return { label: "Опытный",    daily: 420, color: "var(--gold-hi)",   bg: "var(--gold-tint)",    border: "var(--gold-edge)" };
  if (sent >= 500)  return { label: "Растущий",   daily: 300, color: "var(--emerald)",   bg: "var(--emerald-tint)", border: "var(--emerald-edge)" };
  return               { label: "Новичок",     daily: 200, color: "var(--ink-3)",    bg: "var(--surface-2)",    border: "var(--line-2)" };
}

function TierBadge({ sent }) {
  const t = getTier(sent ?? 0);
  return (
    <span
      style={{
        display: "inline-flex", alignItems: "center", gap: 5,
        padding: "2px 8px",
        borderRadius: 5,
        fontSize: 11,
        fontWeight: 600,
        color: t.color,
        background: t.bg,
        border: `1px solid ${t.border}`,
        whiteSpace: "nowrap",
        flexShrink: 0,
      }}
    >
      {t.label} · ≈{t.daily.toLocaleString("ru-RU")}/сут
    </span>
  );
}

/* ── Avatar with deterministic gradient ───────────────────────────────────── */
function Avatar({ name, phone, size = 44, status }) {
  const seed = (name || phone || "?").split("").reduce((a,c)=>a + c.charCodeAt(0), 0);
  const hues = [
    ["#5a3a14", "#8b6326"], ["#1c3a4a", "#2d5a6e"], ["#4a2c3d", "#6e3d5a"],
    ["#2c4a2d", "#3d6e3f"], ["#4a3c1c", "#7a6224"], ["#3a2c4a", "#5a3d7a"],
    ["#4a2c1c", "#7a3d24"],
  ];
  const [a, b] = hues[seed % hues.length];
  const initials = (name || phone || "?")
    .split(/[\s+\-_]/).filter(Boolean).slice(0, 2)
    .map(s => s[0]?.toUpperCase() ?? "").join("") || "?";

  return (
    <div
      style={{
        width: size, height: size,
        borderRadius: size >= 36 ? 9 : 6,
        background: `linear-gradient(135deg, ${a}, ${b})`,
        color: "var(--ink-1)",
        fontSize: size * 0.36,
        fontWeight: 700,
        fontFamily: "var(--mono)",
        display: "flex", alignItems: "center", justifyContent: "center",
        flexShrink: 0,
        boxShadow: "0 1px 0 0 rgba(255,255,255,0.12) inset, 0 1px 3px rgba(0,0,0,0.3)",
        position: "relative",
      }}
    >
      {initials}
      {status && (
        <span
          style={{
            position: "absolute", right: -2, bottom: -2,
            width: 10, height: 10, borderRadius: "50%",
            border: "2px solid var(--bg)",
            background:
              status === "online"  ? "var(--emerald)" :
              status === "working" ? "var(--gold)"    :
              status === "error"   ? "var(--coral)"   :
              status === "pending" ? "var(--amber)"   :
                                     "var(--ink-5)",
          }}
        />
      )}
    </div>
  );
}

/* ── Shared modal shell ────────────────────────────────────────────────────── */
function Modal({ title, subtitle, onClose, size, children, footer }) {
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={`modal${size === "lg" ? " modal-lg" : size === "xl" ? " modal-xl" : ""}`}>
        <div className="modal-header">
          <div>
            <div className="modal-title">{title}</div>
            {subtitle && <div className="modal-subtitle">{subtitle}</div>}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Закрыть"><X size={14} /></button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

function Field({ label, tag, hint, children }) {
  return (
    <div className="field">
      <label className="field-label">
        {label}
        {tag && <span className="field-label-tag">{tag}</span>}
      </label>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

/* ── Profile edit modal ────────────────────────────────────────────────────── */
function ProfileModal({ account, onClose, onSaved }) {
  const [form, setForm] = useState({
    first_name: account.first_name || "",
    last_name:  account.last_name  || "",
    bio:        account.bio        || "",
  });
  const [loading, setLoading]           = useState(false);
  const [photoLoading, setPhotoLoading] = useState(false);

  const handlePhotoChange = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPhotoLoading(true);
    try {
      await uploadPhoto(account.id, file);
      toast.success("Фото обновлено");
    } catch (err) {
      toast.error(err.response?.data?.detail || "Ошибка загрузки фото");
    } finally {
      setPhotoLoading(false);
      e.target.value = "";
    }
  };

  const save = async () => {
    setLoading(true);
    try {
      await updateProfile(account.id, form);
      toast.success("Профиль обновлён");
      onSaved(); onClose();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Ошибка обновления");
    } finally { setLoading(false); }
  };

  return (
    <Modal
      title="Профиль аккаунта"
      subtitle={account.phone}
      onClose={onClose}
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>Отмена</button>
          <div style={{ flex: 1 }} />
          <button onClick={save} disabled={loading} className="btn-primary">
            {loading ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Check size={14} />}
            Сохранить
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "center", padding: "4px 0" }}>
          <Avatar name={account.first_name || account.phone} size={56} status={account.status} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--ink-1)" }}>
              {account.first_name || "—"} {account.last_name || ""}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-4)", fontFamily: "var(--mono)", marginTop: 2 }}>
              {account.phone}
            </div>
          </div>
          <label style={{ cursor: photoLoading ? "not-allowed" : "pointer", flexShrink: 0 }}>
            <input
              type="file"
              accept="image/*"
              style={{ display: "none" }}
              onChange={handlePhotoChange}
              disabled={photoLoading}
            />
            <span
              className="btn-ghost btn-sm"
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                pointerEvents: photoLoading ? "none" : "auto",
                opacity: photoLoading ? 0.6 : 1,
                fontSize: 12,
              }}
            >
              {photoLoading
                ? <RefreshCw size={12} style={{ animation: "spin 0.7s linear infinite" }} />
                : <Upload size={12} />}
              Сменить фото
            </span>
          </label>
        </div>

        <Field label="Имя">
          <input
            className="inp"
            value={form.first_name}
            placeholder="Имя"
            onChange={e => setForm({ ...form, first_name: e.target.value })}
          />
        </Field>
        <Field label="Фамилия">
          <input
            className="inp"
            value={form.last_name}
            placeholder="Фамилия"
            onChange={e => setForm({ ...form, last_name: e.target.value })}
          />
        </Field>
        <Field label="О себе" tag={`${form.bio.length}/70`}>
          <textarea
            rows={3}
            className="inp"
            value={form.bio}
            maxLength={70}
            placeholder="Например: «Маркетолог · MarTech · Москва»"
            onChange={e => setForm({ ...form, bio: e.target.value })}
          />
        </Field>
      </div>
    </Modal>
  );
}

/* ── Add account wizard ────────────────────────────────────────────────────── */
function AddWizard({ onClose, onDone }) {
  const [step, setStep]         = useState("add");
  const [accountId, setAccountId] = useState(null);
  const [loading, setLoading]   = useState(false);
  const [form, setForm]         = useState({ api_id: "", api_hash: "", phone: "" });
  const [code, setCode]         = useState("");
  const [password, setPassword] = useState("");
  const [need2fa, setNeed2fa]   = useState(false);

  const STEPS = ["add", "sendCode", "verifyCode"];
  const stepIdx = STEPS.indexOf(step);
  const STEP_LABELS = ["Данные API", "Отправка кода", "Подтверждение"];

  const handleAdd = async () => {
    if (!form.api_id || !form.api_hash || !form.phone) { toast.error("Заполните все поля"); return; }
    setLoading(true);
    try {
      const acc = await addAccount({ ...form, api_id: Number(form.api_id) });
      setAccountId(acc.id);
      setStep("sendCode");
    } catch (e) { toast.error(e.response?.data?.detail || "Ошибка"); }
    finally { setLoading(false); }
  };

  const handleSendCode = async () => {
    setLoading(true);
    try {
      const r = await sendCode(accountId);
      if (r.status === "already_authorized") {
        toast.success("Уже авторизован!");
        onDone(); onClose();
      } else {
        toast.success("Код отправлен");
        setStep("verifyCode");
      }
    } catch (e) { toast.error(e.response?.data?.detail || "Ошибка отправки кода"); }
    finally { setLoading(false); }
  };

  const handleVerify = async () => {
    setLoading(true);
    try {
      await verifyCode(accountId, { code, password: password || undefined });
      toast.success("Аккаунт авторизован!");
      onDone(); onClose();
    } catch (e) {
      if (e.response?.data?.detail === "2FA_REQUIRED") {
        setNeed2fa(true);
        toast("Введите пароль 2FA");
      } else {
        toast.error(e.response?.data?.detail || "Неверный код");
      }
    } finally { setLoading(false); }
  };

  return (
    <Modal
      title="Добавить Telegram-аккаунт"
      subtitle="3 шага · около минуты"
      onClose={onClose}
      size="lg"
    >
      <div className="steps">
        {STEPS.map((s, i) => (
          <Fragment key={s}>
            <div className={`step ${i < stepIdx ? "step-done" : i === stepIdx ? "step-active" : ""}`}>
              <div className="step-dot">
                {i < stepIdx ? <Check size={11} strokeWidth={2.5} /> : i + 1}
              </div>
              <span className="step-label">{STEP_LABELS[i]}</span>
            </div>
            {i < STEPS.length - 1 && (
              <div className={`step-line${i < stepIdx ? " done" : ""}`} />
            )}
          </Fragment>
        ))}
      </div>

      {step === "add" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div
            style={{
              display: "flex", gap: 12, alignItems: "flex-start",
              padding: "13px 15px",
              background: "var(--gold-tint)", border: "1px solid var(--gold-edge)",
              borderRadius: 10,
            }}
          >
            <div
              style={{
                width: 24, height: 24, borderRadius: 6,
                background: "rgba(232,201,138,0.18)",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0, color: "var(--gold-hi)",
              }}
            >
              <Key size={13} />
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55 }}>
              Получите ключи на{" "}
              <span style={{ fontFamily: "var(--mono)", color: "var(--gold-hi)", fontWeight: 600 }}>my.telegram.org</span>{" "}
              → API Development Tools. Ключи хранятся зашифрованно.
            </div>
          </div>

          <Field label="API ID" tag="число">
            <div style={{ position: "relative" }}>
              <Hash size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36, fontFamily: "var(--mono)", fontSize: 12.5 }}
                type="number"
                placeholder="12345678"
                value={form.api_id}
                onChange={e => setForm({ ...form, api_id: e.target.value })}
              />
            </div>
          </Field>
          <Field label="API Hash" tag="32 символа">
            <div style={{ position: "relative" }}>
              <Key size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36, fontFamily: "var(--mono)", fontSize: 12.5 }}
                placeholder="0f8c3a4b2d9e1f5a..."
                value={form.api_hash}
                onChange={e => setForm({ ...form, api_hash: e.target.value })}
              />
            </div>
          </Field>
          <Field label="Номер телефона" hint="Telegram пришлёт код подтверждения на этот номер">
            <div style={{ position: "relative" }}>
              <Phone size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36, fontFamily: "var(--mono)", fontSize: 12.5 }}
                placeholder="+7 900 123 45 67"
                value={form.phone}
                onChange={e => setForm({ ...form, phone: e.target.value })}
              />
            </div>
          </Field>

          <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
            <button onClick={onClose} className="btn-ghost">Отмена</button>
            <div style={{ flex: 1 }} />
            <button onClick={handleAdd} disabled={loading} className="btn-primary">
              {loading && <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} />}
              Далее <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}

      {step === "sendCode" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ textAlign: "center", padding: "8px 0 4px" }}>
            <div
              style={{
                width: 52, height: 52, margin: "0 auto 14px",
                borderRadius: 14,
                background: "linear-gradient(180deg, var(--gold-tint), rgba(232,201,138,0.02))",
                border: "1px solid var(--gold-edge)",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "var(--gold-hi)",
              }}
            >
              <Smartphone size={22} />
            </div>
            <div style={{ fontSize: 14, color: "var(--ink-2)", marginBottom: 4 }}>Отправить код на</div>
            <div style={{ fontSize: 18, fontWeight: 600, color: "var(--ink-1)", fontFamily: "var(--mono)" }}>
              {form.phone}
            </div>
          </div>
          <button onClick={handleSendCode} disabled={loading} className="btn-primary" style={{ justifyContent: "center", width: "100%" }}>
            {loading && <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} />}
            Отправить код
          </button>
        </div>
      )}

      {step === "verifyCode" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div
            style={{
              padding: "11px 14px",
              background: "var(--emerald-tint)", border: "1px solid var(--emerald-edge)",
              borderRadius: 9, fontSize: 12.5, color: "var(--emerald)",
              display: "flex", alignItems: "center", gap: 10,
            }}
          >
            <CheckCircle2 size={14} strokeWidth={2} />
            Код отправлен на {form.phone}. Проверьте Telegram-приложение или SMS.
          </div>
          <Field label="Код подтверждения">
            <input
              className="inp"
              style={{ textAlign: "center", fontSize: 22, letterSpacing: "0.5em", height: 56, fontFamily: "var(--mono)" }}
              placeholder="• • • • •"
              maxLength={6}
              value={code}
              onChange={e => setCode(e.target.value)}
              autoFocus
            />
          </Field>
          {need2fa && (
            <Field label="Пароль 2FA">
              <input
                className="inp"
                type="password"
                placeholder="Облачный пароль Telegram"
                value={password}
                onChange={e => setPassword(e.target.value)}
              />
            </Field>
          )}
          <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
            <button onClick={() => setStep("sendCode")} className="btn-ghost">
              <RotateCcw size={12} /> Повторить
            </button>
            <div style={{ flex: 1 }} />
            <button onClick={handleVerify} disabled={loading} className="btn-primary">
              {loading ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Check size={14} strokeWidth={2.5} />}
              Подтвердить
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/* ── Main page ─────────────────────────────────────────────────────────────── */
export default function AccountsPage() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [showAdd, setShowAdd]   = useState(false);
  const [editAcc, setEditAcc]   = useState(null);
  const [filter, setFilter]     = useState("all");

  const load = async () => {
    try { setAccounts(await getAccounts()); }
    catch { toast.error("Не удалось загрузить аккаунты"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const handleDelete = async (id) => {
    if (!confirm("Удалить этот аккаунт?")) return;
    await deleteAccount(id); toast.success("Удалено"); load();
  };

  const handleSync = async (id) => {
    const r = await syncAccount(id);
    if (r.status === "online") { toast.success("Синхронизировано"); load(); }
    else toast.error("Не удалось — " + (r.reason || "не авторизован"));
  };

  const counts = {
    all:     accounts.length,
    online:  accounts.filter(a => a.status === "online").length,
    pending: accounts.filter(a => a.status === "pending").length,
    error:   accounts.filter(a => a.status === "error" || a.status === "banned").length,
  };
  const filtered = filter === "all"
    ? accounts
    : filter === "error"
      ? accounts.filter(a => a.status === "error" || a.status === "banned")
      : accounts.filter(a => a.status === filter);

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Аккаунты</h1>
          <p className="page-sub">
            {accounts.length} аккаунт{accounts.length === 1 ? "" : "ов"} · {counts.online} в сети{counts.error ? ` · ${counts.error} требуют внимания` : ""}
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost" onClick={() => toast("🚧 Импорт сессий скоро будет доступен")}><Upload size={14} /> Импорт сессий</button>
          <button onClick={() => setShowAdd(true)} className="btn-primary"><Plus size={14} /> Добавить аккаунт</button>
        </div>
      </div>

      {/* Filter tabs */}
      {accounts.length > 0 && (
        <div
          style={{
            display: "flex", gap: 4, padding: 4,
            background: "var(--surface-1)", border: "1px solid var(--line-2)",
            borderRadius: 10, width: "fit-content",
          }}
        >
          {[
            { id: "all",     label: "Все",      n: counts.all },
            { id: "online",  label: "В сети",   n: counts.online },
            { id: "pending", label: "Ожидание", n: counts.pending },
            { id: "error",   label: "Ошибки",   n: counts.error },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setFilter(tab.id)}
              className="btn-sm"
              style={{
                background: filter === tab.id ? "var(--surface-3)" : "transparent",
                border: "1px solid " + (filter === tab.id ? "var(--line-3)" : "transparent"),
                color: filter === tab.id ? "var(--ink-1)" : "var(--ink-3)",
                fontWeight: filter === tab.id ? 600 : 500,
                cursor: "pointer", borderRadius: 6,
                height: 30, padding: "0 12px", fontSize: 12,
                display: "inline-flex", alignItems: "center", gap: 6,
              }}
            >
              {tab.label}
              <span style={{ color: "var(--ink-4)", fontFamily: "var(--mono)", fontSize: 11 }}>{tab.n}</span>
            </button>
          ))}
        </div>
      )}

      {/* List */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 84, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="card card-no-hover">
          <div className="empty">
            <div className="empty-icon"><UserPlus size={22} /></div>
            <span className="empty-title">{accounts.length === 0 ? "Пока ни одного аккаунта" : "В этом фильтре пусто"}</span>
            <span className="empty-sub">
              {accounts.length === 0
                ? "Нажмите «Добавить», чтобы авторизовать первый Telegram-аккаунт"
                : "Переключите фильтр или добавьте новый аккаунт"}
            </span>
            <div style={{ marginTop: 14 }}>
              <button onClick={() => setShowAdd(true)} className="btn-primary"><Plus size={14} /> Добавить</button>
            </div>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {filtered.map((acc, i) => (
            <div key={acc.id} className="card animate-fade-in" style={{ animationDelay: `${i * 30}ms` }}>
              <div className="card-body" style={{ display: "flex", alignItems: "center", gap: 16 }}>
                <Avatar name={acc.first_name || acc.phone} size={44} status={acc.status} />

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 5, flexWrap: "wrap" }}>
                    <span style={{ fontSize: 14.5, fontWeight: 600, color: "var(--ink-1)", letterSpacing: "-0.012em" }}>
                      {acc.first_name || acc.phone}{acc.last_name ? ` ${acc.last_name}` : ""}
                    </span>
                    {acc.username && (
                      <span style={{ fontSize: 12, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>@{acc.username}</span>
                    )}
                    <StatusBadge status={acc.status} pulse={acc.status === "working"} />
                  </div>
                  <div style={{ display: "flex", gap: 14, fontSize: 12.5, color: "var(--ink-3)", alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{ fontFamily: "var(--mono)", color: "var(--ink-2)" }}>{acc.phone}</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 5 }}>
                      <span className="t-numeric" style={{ color: "var(--ink-1)", fontSize: 13, fontFamily: "var(--mono)", fontWeight: 600 }}>
                        {(acc.messages_sent ?? 0).toLocaleString("ru-RU")}
                      </span>
                      <span style={{ color: "var(--ink-5)" }}>отправок</span>
                    </span>
                    <TierBadge sent={acc.messages_sent ?? 0} />
                    {acc.errors_count > 0 && (
                      <span style={{ display: "flex", alignItems: "center", gap: 5, color: "var(--coral)" }}>
                        <AlertTriangle size={12} />
                        {acc.errors_count} {acc.errors_count === 1 ? "ошибка" : "ошибки"}
                      </span>
                    )}
                  </div>
                </div>

                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button onClick={() => handleSync(acc.id)} className="btn-icon" title="Синхронизировать">
                    <RefreshCw size={14} />
                  </button>
                  {acc.status === "online" && (
                    <button onClick={() => setEditAcc(acc)} className="btn-icon" title="Редактировать профиль">
                      <Edit3 size={14} />
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(acc.id)}
                    className="btn-icon"
                    title="Удалить"
                    onMouseEnter={e => { e.currentTarget.style.background = "var(--coral-tint)"; e.currentTarget.style.color = "var(--coral)"; e.currentTarget.style.borderColor = "var(--coral-edge)"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {showAdd && <AddWizard onClose={() => setShowAdd(false)} onDone={load} />}
      {editAcc  && <ProfileModal account={editAcc} onClose={() => setEditAcc(null)} onSaved={load} />}
    </div>
  );
}
