"use client";
import { useEffect, useState } from "react";
import {
  getAccounts, addAccount, deleteAccount,
  sendCode, verifyCode, updateProfile, syncAccount,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import toast from "react-hot-toast";
import { Plus, Trash2, RefreshCw, Edit2, X, Check, UserPlus, Phone, Key, User } from "lucide-react";

/* ── Shared modal shell ────────────────────────────────────────────────────── */
function Modal({ title, onClose, size, children }) {
  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className={`modal${size === "lg" ? " modal-lg" : ""}`}>
        <div className="modal-header">
          <span className="modal-title">{title}</span>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div className="field">
      <label className="field-label">{label}</label>
      {children}
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
  const [loading, setLoading] = useState(false);

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
    <Modal title="Редактировать профиль" onClose={onClose}>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <Field label="Имя">
          <div style={{ position: "relative" }}>
            <User size={12} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
            <input className="inp" style={{ paddingLeft: 30 }} value={form.first_name} placeholder="Имя"
              onChange={e => setForm({ ...form, first_name: e.target.value })} />
          </div>
        </Field>
        <Field label="Фамилия">
          <div style={{ position: "relative" }}>
            <User size={12} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
            <input className="inp" style={{ paddingLeft: 30 }} value={form.last_name} placeholder="Фамилия"
              onChange={e => setForm({ ...form, last_name: e.target.value })} />
          </div>
        </Field>
        <Field label="О себе">
          <textarea rows={3} className="inp" style={{ resize: "none" }} value={form.bio} placeholder="Краткое описание..."
            onChange={e => setForm({ ...form, bio: e.target.value })} />
        </Field>
        <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
          <button onClick={save} disabled={loading} className="btn-primary">
            {loading && <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} />}
            Сохранить
          </button>
          <button onClick={onClose} className="btn-ghost">Отмена</button>
        </div>
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
  const STEP_LABELS = ["Данные", "Код", "Проверка"];

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
      if (r.status === "already_authorized") { toast.success("Уже авторизован!"); onDone(); onClose(); }
      else { toast.success("Код отправлен"); setStep("verifyCode"); }
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
      if (e.response?.data?.detail === "2FA_REQUIRED") { setNeed2fa(true); toast("Введите пароль 2FA"); }
      else toast.error(e.response?.data?.detail || "Неверный код");
    } finally { setLoading(false); }
  };

  return (
    <Modal title="Добавить Telegram аккаунт" onClose={onClose}>
      {/* Step indicator */}
      <div className="steps">
        {STEPS.map((s, i) => (
          <>
            <div key={s} className={`step-dot ${i < stepIdx ? "step-dot-done" : i === stepIdx ? "step-dot-active" : "step-dot-idle"}`}>
              {i < stepIdx ? <Check size={10} /> : i + 1}
            </div>
            {i < STEPS.length - 1 && (
              <div key={`line-${i}`} className={`step-line${i < stepIdx ? " step-line-done" : ""}`} />
            )}
          </>
        ))}
        <span style={{ fontSize: 12, color: "var(--text-3)", marginLeft: 8 }}>{STEP_LABELS[stepIdx]}</span>
      </div>

      {step === "add" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            style={{
              padding: "10px 12px",
              background: "var(--accent-bg)",
              border: "1px solid var(--accent-border)",
              borderRadius: "var(--r-sm)",
              fontSize: 12,
              color: "var(--accent-hi)",
            }}
          >
            Получите ключи на{" "}
            <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>my.telegram.org</span>
          </div>
          {[
            { label: "API ID",   field: "api_id",   icon: Key,   type: "number", ph: "12345678" },
            { label: "API Hash", field: "api_hash",  icon: Key,   type: "text",   ph: "0f8c3a..." },
            { label: "Телефон", field: "phone",     icon: Phone, type: "text",   ph: "+79001234567" },
          ].map(({ label, field, icon: Icon, type, ph }) => (
            <Field key={field} label={label}>
              <div style={{ position: "relative" }}>
                <Icon size={12} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
                <input className="inp" style={{ paddingLeft: 30 }} type={type} placeholder={ph}
                  value={form[field]} onChange={e => setForm({ ...form, [field]: e.target.value })} />
              </div>
            </Field>
          ))}
          <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
            <button onClick={handleAdd} disabled={loading} className="btn-primary">
              {loading && <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} />}
              <UserPlus size={13} /> Далее
            </button>
            <button onClick={onClose} className="btn-ghost">Отмена</button>
          </div>
        </div>
      )}

      {step === "sendCode" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            style={{
              padding: "16px",
              background: "var(--overlay)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-sm)",
              textAlign: "center",
            }}
          >
            <Phone size={20} style={{ color: "var(--text-3)", margin: "0 auto 8px" }} />
            <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 4 }}>Отправить код на номер</div>
            <div style={{ fontSize: 16, fontWeight: 600, color: "var(--text-1)", fontFamily: "var(--font-mono)" }}>{form.phone}</div>
          </div>
          <button onClick={handleSendCode} disabled={loading} className="btn-primary" style={{ justifyContent: "center" }}>
            {loading && <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} />}
            Отправить код
          </button>
        </div>
      )}

      {step === "verifyCode" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div
            style={{
              padding: "10px 12px",
              background: "var(--green-bg)",
              border: "1px solid var(--green-border)",
              borderRadius: "var(--r-sm)",
              fontSize: 12,
              color: "var(--green)",
            }}
          >
            Код отправлен на {form.phone}. Проверьте Telegram или SMS.
          </div>
          <Field label="Код подтверждения">
            <div style={{ position: "relative" }}>
              <Key size={12} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
              <input className="inp" style={{ paddingLeft: 30, fontFamily: "var(--font-mono)", letterSpacing: "0.1em" }}
                placeholder="12345" value={code} onChange={e => setCode(e.target.value)} />
            </div>
          </Field>
          {need2fa && (
            <Field label="Пароль 2FA">
              <input className="inp" type="password" placeholder="Ваш пароль"
                value={password} onChange={e => setPassword(e.target.value)} />
            </Field>
          )}
          <div style={{ display: "flex", gap: 8, paddingTop: 4 }}>
            <button onClick={handleVerify} disabled={loading} className="btn-primary">
              {loading ? <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} /> : <Check size={13} />}
              Подтвердить
            </button>
            <button onClick={() => setStep("sendCode")} className="btn-ghost">Повторить код</button>
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

  const online = accounts.filter(a => a.status === "online").length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
            Аккаунты
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-3)" }}>
            {accounts.length} аккаунтов — {online} онлайн
          </p>
        </div>
        <button onClick={() => setShowAdd(true)} className="btn-primary">
          <Plus size={13} /> Добавить
        </button>
      </div>

      {/* List */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 72, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : accounts.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><UserPlus size={18} /></div>
            <span className="empty-title">Нет аккаунтов</span>
            <span className="empty-sub">Нажмите «Добавить», чтобы авторизовать первый аккаунт</span>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {accounts.map(acc => (
            <div
              key={acc.id}
              className="card animate-fade-in"
              style={{ padding: "14px 16px" }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {/* Avatar */}
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: "var(--r)",
                    background: "var(--accent-bg)",
                    border: "1px solid var(--accent-border)",
                    color: "var(--accent-hi)",
                    fontWeight: 600,
                    fontSize: 14,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  {(acc.first_name || acc.phone)?.[0]?.toUpperCase() || "?"}
                </div>

                {/* Info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 2 }}>
                    <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-1)" }}>
                      {acc.first_name || acc.phone}{acc.last_name ? ` ${acc.last_name}` : ""}
                    </span>
                    {acc.username && (
                      <span style={{ fontSize: 12, color: "var(--text-4)", fontFamily: "var(--font-mono)" }}>@{acc.username}</span>
                    )}
                    <StatusBadge status={acc.status} />
                  </div>
                  <div style={{ display: "flex", gap: 12, fontSize: 12, color: "var(--text-3)" }}>
                    <span style={{ fontFamily: "var(--font-mono)" }}>{acc.phone}</span>
                    <span>Отправлено: <strong style={{ color: "var(--text-2)", fontWeight: 500 }}>{acc.messages_sent}</strong></span>
                    {acc.errors_count > 0 && (
                      <span style={{ color: "var(--red)" }}>Ошибок: {acc.errors_count}</span>
                    )}
                  </div>
                </div>

                {/* Actions */}
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button
                    onClick={() => handleSync(acc.id)}
                    className="btn-icon"
                    title="Синхронизировать"
                  >
                    <RefreshCw size={13} />
                  </button>
                  {acc.status === "online" && (
                    <button
                      onClick={() => setEditAcc(acc)}
                      className="btn-icon"
                      title="Редактировать профиль"
                    >
                      <Edit2 size={13} />
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(acc.id)}
                    className="btn-icon"
                    title="Удалить"
                    style={{ color: "var(--text-3)", borderColor: "var(--border)" }}
                    onMouseEnter={e => { e.currentTarget.style.background = "var(--red-bg)"; e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red-border)"; }}
                    onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = "var(--text-3)"; e.currentTarget.style.borderColor = "var(--border)"; }}
                  >
                    <Trash2 size={13} />
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
