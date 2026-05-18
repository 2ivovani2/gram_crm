"use client";
import { useEffect, useState, Fragment } from "react";
import {
  getAccounts, addAccount, deleteAccount,
  sendCode, verifyCode, updateProfile, uploadPhoto, syncAccount, importSession,
} from "@/lib/api";
import StatusBadge from "@/components/StatusBadge";
import toast from "react-hot-toast";
import {
  Plus, Trash2, RefreshCw, Edit3, X, Check, UserPlus,
  Phone, Key, User, ChevronRight, ChevronLeft, Smartphone,
  Hash, MoreHorizontal, CheckCircle2, RotateCcw, AlertTriangle, Upload,
  FileUp, FolderOpen, Users2,
} from "lucide-react";

/* ── Account tier info ────────────────────────────────────────────────────── */
function getTier(tgUserId, sent) {
  // Primary: Telegram user ID (sequential — lower = older account)
  if (tgUserId) {
    if (tgUserId < 6_000_000_000)   return { label: "Ветеран",  daily: 550, color: "#9b6dff",          bg: "rgba(155,109,255,0.12)", border: "rgba(155,109,255,0.25)" };
    if (tgUserId < 7_000_000_000)   return { label: "Опытный",  daily: 420, color: "var(--gold-hi)",   bg: "var(--gold-tint)",       border: "var(--gold-edge)" };
    if (tgUserId < 7_500_000_000)   return { label: "Растущий", daily: 300, color: "var(--emerald)",   bg: "var(--emerald-tint)",    border: "var(--emerald-edge)" };
    return                                 { label: "Новичок",  daily: 200, color: "var(--ink-3)",     bg: "var(--surface-2)",       border: "var(--line-2)" };
  }
  // Fallback: internal sent counter
  if (sent >= 5000) return { label: "Ветеран",  daily: 550, color: "#9b6dff",          bg: "rgba(155,109,255,0.12)", border: "rgba(155,109,255,0.25)" };
  if (sent >= 2000) return { label: "Опытный",  daily: 420, color: "var(--gold-hi)",   bg: "var(--gold-tint)",       border: "var(--gold-edge)" };
  if (sent >= 500)  return { label: "Растущий", daily: 300, color: "var(--emerald)",   bg: "var(--emerald-tint)",    border: "var(--emerald-edge)" };
  return                   { label: "Новичок",  daily: 200, color: "var(--ink-3)",     bg: "var(--surface-2)",       border: "var(--line-2)" };
}

function TierBadge({ tgUserId, sent }) {
  const t = getTier(tgUserId, sent ?? 0);
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

/* ── Bulk profile update modal ────────────────────────────────────────────── */
function BulkProfileModal({ accounts, onClose, onDone }) {
  const eligible = accounts.filter(a => a.status === "online" || a.status === "working");

  const [form, setForm] = useState({ first_name: "", last_name: "", bio: "" });
  const [photo, setPhoto]         = useState(null);      // File object
  const [photoPreview, setPhotoPreview] = useState(null);
  const [selected, setSelected]   = useState(() => new Set(eligible.map(a => a.id)));
  const [results, setResults]     = useState(null);      // null = not started
  const [running, setRunning]     = useState(false);

  const toggleAll = () => {
    if (selected.size === eligible.length) setSelected(new Set());
    else setSelected(new Set(eligible.map(a => a.id)));
  };
  const toggle = (id) => setSelected(prev => {
    const s = new Set(prev);
    s.has(id) ? s.delete(id) : s.add(id);
    return s;
  });

  const handlePhoto = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setPhoto(f);
    setPhotoPreview(URL.createObjectURL(f));
    e.target.value = "";
  };

  const hasChanges = form.first_name || form.last_name || form.bio || photo;

  const run = async () => {
    if (!hasChanges) { toast.error("Заполните хотя бы одно поле"); return; }
    if (!selected.size) { toast.error("Выберите хотя бы один аккаунт"); return; }

    const targets = eligible.filter(a => selected.has(a.id));
    setRunning(true);
    setResults(targets.map(a => ({ id: a.id, phone: a.phone, name: `${a.first_name || ""} ${a.last_name || ""}`.trim() || a.phone, status: "pending" })));

    for (let i = 0; i < targets.length; i++) {
      const acc = targets[i];
      setResults(prev => prev.map((r, idx) => idx === i ? { ...r, status: "loading" } : r));
      let err = null;
      try {
        if (form.first_name || form.last_name || form.bio) {
          await updateProfile(acc.id, {
            first_name: form.first_name || acc.first_name || "",
            last_name:  form.last_name  || acc.last_name  || "",
            bio:        form.bio        || acc.bio         || "",
          });
        }
        if (photo) await uploadPhoto(acc.id, photo);
      } catch (e) {
        err = e.response?.data?.detail || e.message || "Ошибка";
      }
      setResults(prev => prev.map((r, idx) => idx === i ? { ...r, status: err ? "error" : "ok", err } : r));
    }

    setRunning(false);
    onDone();
  };

  const done = results && results.every(r => r.status === "ok" || r.status === "error");
  const okCount = results?.filter(r => r.status === "ok").length ?? 0;

  return (
    <Modal
      title="Массовое обновление профилей"
      subtitle={`${eligible.length} доступных аккаунтов`}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>Закрыть</button>
          <div style={{ flex: 1 }} />
          {done
            ? <button className="btn-primary" onClick={onClose}><Check size={14} /> Готово · {okCount} обновлено</button>
            : <button className="btn-primary" onClick={run} disabled={running || !hasChanges || !selected.size}>
                {running ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Users2 size={14} />}
                Применить к {selected.size} аккаунт{selected.size % 10 === 1 && selected.size !== 11 ? "у" : selected.size % 10 < 5 && (selected.size < 10 || selected.size > 20) ? "ам" : "ам"}
              </button>
          }
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

        {/* Fields */}
        {!results && (
          <>
            <div style={{ display: "flex", gap: 10 }}>
              <Field label="Имя" hint="Оставь пустым — не менять">
                <input className="inp" placeholder={`текущее`} value={form.first_name} onChange={e => setForm({ ...form, first_name: e.target.value })} />
              </Field>
              <Field label="Фамилия" hint="Оставь пустым — не менять">
                <input className="inp" placeholder={`текущее`} value={form.last_name} onChange={e => setForm({ ...form, last_name: e.target.value })} />
              </Field>
            </div>

            <Field label="О себе" tag={form.bio ? `${form.bio.length}/70` : "необязательно"}>
              <textarea rows={2} className="inp" maxLength={70} placeholder="Оставь пустым — не менять" value={form.bio} onChange={e => setForm({ ...form, bio: e.target.value })} />
            </Field>

            <div>
              <label className="field-label">Фото профиля <span className="field-label-tag">необязательно</span></label>
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 6 }}>
                {photoPreview
                  ? <img src={photoPreview} alt="" style={{ width: 48, height: 48, borderRadius: 10, objectFit: "cover", border: "1px solid var(--line-3)" }} />
                  : <div style={{ width: 48, height: 48, borderRadius: 10, background: "var(--surface-2)", border: "1px solid var(--line-2)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--ink-4)" }}><User size={18} /></div>
                }
                <label style={{ cursor: "pointer" }}>
                  <input type="file" accept="image/*" style={{ display: "none" }} onChange={handlePhoto} />
                  <span className="btn-ghost btn-sm" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                    <Upload size={12} /> {photo ? photo.name : "Выбрать фото"}
                  </span>
                </label>
                {photo && (
                  <button className="btn-ghost btn-sm" style={{ fontSize: 12 }} onClick={() => { setPhoto(null); setPhotoPreview(null); }}>
                    <X size={12} /> Убрать
                  </button>
                )}
              </div>
            </div>

            {/* Account selector */}
            <div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <label className="field-label" style={{ margin: 0 }}>Применить к аккаунтам</label>
                <button onClick={toggleAll} className="btn-ghost btn-sm" style={{ fontSize: 11 }}>
                  {selected.size === eligible.length ? "Снять все" : "Выбрать все"}
                </button>
              </div>
              {eligible.length === 0 ? (
                <div style={{ fontSize: 12.5, color: "var(--ink-4)", padding: "10px 0" }}>Нет онлайн-аккаунтов</div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 5, maxHeight: 220, overflowY: "auto" }}>
                  {eligible.map(a => (
                    <label
                      key={a.id}
                      style={{
                        display: "flex", alignItems: "center", gap: 10,
                        padding: "8px 12px", borderRadius: 8, cursor: "pointer",
                        background: selected.has(a.id) ? "var(--surface-2)" : "transparent",
                        border: `1px solid ${selected.has(a.id) ? "var(--line-3)" : "transparent"}`,
                        transition: "all 0.1s",
                      }}
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(a.id)}
                        onChange={() => toggle(a.id)}
                        style={{ accentColor: "var(--gold-hi)", width: 14, height: 14 }}
                      />
                      <Avatar name={a.first_name || a.phone} size={28} status={a.status} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink-1)" }}>
                          {a.first_name || "—"} {a.last_name || ""}
                        </div>
                        <div style={{ fontSize: 11, color: "var(--ink-4)", fontFamily: "var(--mono)" }}>{a.phone}</div>
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          </>
        )}

        {/* Progress */}
        {results && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {results.map((r) => (
              <div
                key={r.id}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "9px 13px", borderRadius: 8,
                  background: r.status === "ok" ? "var(--emerald-tint)" : r.status === "error" ? "var(--coral-tint)" : "var(--surface-2)",
                  border: `1px solid ${r.status === "ok" ? "var(--emerald-edge)" : r.status === "error" ? "var(--coral-edge)" : "var(--line-2)"}`,
                }}
              >
                {r.status === "loading" && <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite", color: "var(--gold-hi)", flexShrink: 0 }} />}
                {r.status === "ok"      && <CheckCircle2 size={13} style={{ color: "var(--emerald)", flexShrink: 0 }} />}
                {r.status === "error"   && <AlertTriangle size={13} style={{ color: "var(--coral)", flexShrink: 0 }} />}
                {r.status === "pending" && <div style={{ width: 13, height: 13, borderRadius: "50%", background: "var(--line-3)", flexShrink: 0 }} />}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, fontFamily: "var(--mono)", color: "var(--ink-1)" }}>{r.phone}</div>
                  {r.err && <div style={{ fontSize: 11, color: "var(--coral)", marginTop: 2 }}>{r.err}</div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Modal>
  );
}

/* ── Session import modal ─────────────────────────────────────────────────── */
function ImportModal({ onClose, onDone }) {
  const [apiId, setApiId]     = useState("");
  const [apiHash, setApiHash] = useState("");
  const [files, setFiles]     = useState([]); // [{file, status, result}]
  const [dragging, setDragging] = useState(false);
  const [running, setRunning] = useState(false);

  const addFiles = (incoming) => {
    const valid = Array.from(incoming).filter(f => f.name.endsWith(".session"));
    if (!valid.length) { toast.error("Только .session файлы"); return; }
    setFiles(prev => {
      const existing = new Set(prev.map(f => f.file.name));
      const fresh = valid.filter(f => !existing.has(f.name)).map(f => ({ file: f, status: "pending", result: null }));
      return [...prev, ...fresh];
    });
  };

  const onDrop = (e) => {
    e.preventDefault(); setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const removeFile = (idx) => setFiles(prev => prev.filter((_, i) => i !== idx));

  const runImport = async () => {
    if (!apiId || !apiHash) { toast.error("Введите api_id и api_hash"); return; }
    if (!files.length) { toast.error("Добавьте хотя бы один файл"); return; }
    setRunning(true);
    let ok = 0, fail = 0;
    for (let i = 0; i < files.length; i++) {
      if (files[i].status === "ok") continue;
      setFiles(prev => prev.map((f, idx) => idx === i ? { ...f, status: "loading" } : f));
      try {
        const res = await importSession(files[i].file, Number(apiId), apiHash);
        setFiles(prev => prev.map((f, idx) => idx === i ? { ...f, status: "ok", result: res } : f));
        ok++;
      } catch (e) {
        const msg = e.response?.data?.detail || e.message || "Ошибка";
        setFiles(prev => prev.map((f, idx) => idx === i ? { ...f, status: "error", result: msg } : f));
        fail++;
      }
    }
    setRunning(false);
    if (ok > 0) { onDone(); toast.success(`Импортировано: ${ok}`); }
    if (fail > 0) toast.error(`Ошибок: ${fail}`);
  };

  const statusIcon = (s) => {
    if (s === "loading") return <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite", color: "var(--gold-hi)" }} />;
    if (s === "ok")      return <CheckCircle2 size={13} style={{ color: "var(--emerald)" }} />;
    if (s === "error")   return <AlertTriangle size={13} style={{ color: "var(--coral)" }} />;
    return <FileUp size={13} style={{ color: "var(--ink-4)" }} />;
  };

  const allDone = files.length > 0 && files.every(f => f.status === "ok" || f.status === "error");

  return (
    <Modal
      title="Импорт сессий"
      subtitle="Загрузи .session файлы от Telethon"
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn-ghost" onClick={onClose}>Закрыть</button>
          <div style={{ flex: 1 }} />
          {allDone
            ? <button className="btn-primary" onClick={onClose}><Check size={14} /> Готово</button>
            : <button className="btn-primary" onClick={runImport} disabled={running || !files.length}>
                {running ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Upload size={14} />}
                Импортировать
              </button>
          }
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {/* api_id / api_hash */}
        <div style={{ display: "flex", gap: 10 }}>
          <Field label="API ID" tag="число">
            <div style={{ position: "relative" }}>
              <Hash size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36, fontFamily: "var(--mono)", fontSize: 12.5 }}
                type="number"
                placeholder="12345678"
                value={apiId}
                onChange={e => setApiId(e.target.value)}
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
                value={apiHash}
                onChange={e => setApiHash(e.target.value)}
              />
            </div>
          </Field>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          style={{
            border: `2px dashed ${dragging ? "var(--gold-hi)" : "var(--line-3)"}`,
            borderRadius: 12,
            padding: "28px 20px",
            textAlign: "center",
            background: dragging ? "var(--gold-tint)" : "var(--surface-1)",
            transition: "all 0.15s",
            cursor: "pointer",
          }}
          onClick={() => document.getElementById("session-file-input").click()}
        >
          <input
            id="session-file-input"
            type="file"
            accept=".session"
            multiple
            style={{ display: "none" }}
            onChange={e => addFiles(e.target.files)}
          />
          <FolderOpen size={28} style={{ color: dragging ? "var(--gold-hi)" : "var(--ink-4)", marginBottom: 10 }} />
          <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink-2)", marginBottom: 4 }}>
            Перетащи .session файлы сюда
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-4)" }}>или кликни чтобы выбрать · можно несколько сразу</div>
        </div>

        {/* File list */}
        {files.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {files.map((f, i) => (
              <div
                key={i}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "9px 13px", borderRadius: 8,
                  background: f.status === "ok" ? "var(--emerald-tint)" : f.status === "error" ? "var(--coral-tint)" : "var(--surface-2)",
                  border: `1px solid ${f.status === "ok" ? "var(--emerald-edge)" : f.status === "error" ? "var(--coral-edge)" : "var(--line-2)"}`,
                }}
              >
                {statusIcon(f.status)}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12.5, fontFamily: "var(--mono)", color: "var(--ink-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {f.file.name}
                  </div>
                  {f.status === "ok" && f.result && (
                    <div style={{ fontSize: 11, color: "var(--emerald)", marginTop: 2 }}>
                      {f.result.first_name || ""} {f.result.last_name || ""} {f.result.username ? `@${f.result.username}` : ""} · {f.result.phone}
                      {f.result.sessions_killed > 0 && (
                        <span style={{ color: "var(--ink-4)", marginLeft: 6 }}>
                          · закрыто сессий: {f.result.sessions_killed}
                        </span>
                      )}
                    </div>
                  )}
                  {f.status === "error" && (
                    <div style={{ fontSize: 11, color: "var(--coral)", marginTop: 2 }}>{f.result}</div>
                  )}
                </div>
                {f.status === "pending" && !running && (
                  <button
                    onClick={() => removeFile(i)}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", padding: 2, display: "flex" }}
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
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
  const [accounts, setAccounts]       = useState([]);
  const [loading, setLoading]         = useState(true);
  const [showAdd, setShowAdd]         = useState(false);
  const [showImport, setShowImport]   = useState(false);
  const [showBulk, setShowBulk]       = useState(false);
  const [editAcc, setEditAcc]         = useState(null);
  const [filter, setFilter]           = useState("all");

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
          <button className="btn-ghost" onClick={() => setShowImport(true)}><FileUp size={14} /> Импорт сессий</button>
          {accounts.some(a => a.status === "online" || a.status === "working") && (
            <button className="btn-ghost" onClick={() => setShowBulk(true)}><Users2 size={14} /> Обновить всем профиль</button>
          )}
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
                    <TierBadge tgUserId={acc.tg_user_id} sent={acc.messages_sent ?? 0} />
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

      {showAdd    && <AddWizard onClose={() => setShowAdd(false)} onDone={load} />}
      {showImport && <ImportModal onClose={() => setShowImport(false)} onDone={load} />}
      {showBulk   && <BulkProfileModal accounts={accounts} onClose={() => setShowBulk(false)} onDone={load} />}
      {editAcc    && <ProfileModal account={editAcc} onClose={() => setEditAcc(null)} onSaved={load} />}
    </div>
  );
}
