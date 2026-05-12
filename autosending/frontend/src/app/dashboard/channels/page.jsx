"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { getChannels, addChannel, toggleChannel, deleteChannel } from "@/lib/api";
import toast from "react-hot-toast";
import {
  Plus, Trash2, Hash, ToggleLeft, ToggleRight, Link,
  RefreshCw, Upload, X, CheckCircle, AlertCircle, Copy, FileText,
} from "lucide-react";

/* ─── Channel normalization ──────────────────────────────────────────────── */

// Telegram username: 5–32 chars, letters/digits/underscores, must start with a letter
const TG_USERNAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{3,30}$/;

/**
 * Normalize a single raw line into a canonical channel identifier.
 * Returns { handle, type: "username" | "invite" } or null if unparseable.
 *
 * Supported inputs:
 *   - @username
 *   - t.me/username  /  https://t.me/username
 *   - t.me/+HASH  /  https://t.me/+HASH            → invite link
 *   - t.me/joinchat/HASH  / https://t.me/joinchat/HASH → invite link
 *   - bare username (no prefix)
 *   - any of the above wrapped in CSV quotes ("…")
 */
function normalizeChannel(raw) {
  // Strip surrounding whitespace + CSV quote artifacts
  let s = raw.trim().replace(/^["']+|["']+$/g, "").trim();
  if (!s || s.startsWith("#")) return null;

  // Normalize to "no protocol, no domain"
  const noProto = s.replace(/^https?:\/\//i, "");
  const noTme   = noProto.replace(/^(?:t\.me|telegram\.me)\//i, "");

  // Invite links: start with + or joinchat/
  if (noTme.startsWith("+") || /^joinchat\//i.test(noTme)) {
    const path = noTme.split("?")[0].split("#")[0].replace(/\/+$/, "");
    return { handle: "https://t.me/" + path, type: "invite" };
  }

  // Username: strip leading @ and clean tail
  const username = noTme.replace(/^@+/, "").split("?")[0].split("#")[0].replace(/\/+$/, "");
  if (!username) return null;

  return { handle: "@" + username, type: "username" };
}

function isValidChannel({ handle, type }) {
  if (type === "invite") return true; // structural validity checked by normalizeChannel
  const username = handle.slice(1);
  return TG_USERNAME_RE.test(username);
}

function parseFile(text, existingUrls) {
  const existing = new Set(existingUrls.map(u => u.toLowerCase()));
  const seen = new Set();
  const rows = [];

  for (const raw of text.split(/[\r\n]+/)) {
    const trimmed = raw.trim().replace(/^["']+|["']+$/g, "").trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const normalized = normalizeChannel(trimmed);

    if (!normalized) {
      rows.push({ raw: trimmed, handle: trimmed, type: "username", status: "invalid", reason: "Не удалось распознать" });
      continue;
    }

    const { handle, type } = normalized;
    const key = handle.toLowerCase();

    if (existing.has(key)) {
      rows.push({ raw: trimmed, handle, type, status: "exists", reason: "Уже в базе" });
      continue;
    }

    if (seen.has(key)) {
      rows.push({ raw: trimmed, handle, type, status: "duplicate", reason: "Дубликат в файле" });
      continue;
    }

    if (!isValidChannel(normalized)) {
      rows.push({ raw: trimmed, handle, type, status: "invalid", reason: "Неверный username" });
      continue;
    }

    seen.add(key);
    rows.push({ raw: trimmed, handle, type, status: "valid", reason: "" });
  }

  return rows;
}

/* ─── Status badge for import preview ───────────────────────────────────── */

function ImportBadge({ status }) {
  const map = {
    valid:     { bg: "var(--green-bg)",  border: "var(--green-border)",  color: "var(--green)",  label: "Ок" },
    invalid:   { bg: "var(--red-bg)",    border: "var(--red-border)",    color: "var(--red)",    label: "Неверный" },
    duplicate: { bg: "var(--amber-bg)",  border: "var(--amber-border)",  color: "var(--amber)",  label: "Дубликат" },
    exists:    { bg: "var(--surface)",   border: "var(--border)",        color: "var(--text-4)", label: "Уже есть" },
  };
  const s = map[status] ?? map.invalid;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      height: 18, padding: "0 6px", borderRadius: 100,
      background: s.bg, border: `1px solid ${s.border}`, color: s.color,
      fontSize: 10, fontWeight: 600, whiteSpace: "nowrap", flexShrink: 0,
    }}>
      {s.label}
    </span>
  );
}

/* ─── CSV Import modal ───────────────────────────────────────────────────── */

function ImportModal({ onClose, existingChannels, onImported }) {
  const [rows, setRows]       = useState(null);
  const [dragging, setDrag]   = useState(false);
  const [progress, setProgress] = useState(null); // null | { done, total }
  const fileRef = useRef();

  const handleFile = useCallback((file) => {
    if (!file) return;
    const ext = file.name.split(".").pop().toLowerCase();
    if (!["csv", "txt"].includes(ext)) {
      toast.error("Поддерживаются только .csv и .txt файлы");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const parsed = parseFile(e.target.result, existingChannels.map(c => c.url));
      setRows(parsed.map(r => ({ ...r, excluded: r.status !== "valid" })));
    };
    reader.readAsText(file, "utf-8");
  }, [existingChannels]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDrag(false);
    handleFile(e.dataTransfer.files[0]);
  }, [handleFile]);

  const onDragOver = (e) => { e.preventDefault(); setDrag(true); };
  const onDragLeave = () => setDrag(false);

  const toggleRow = (i) => {
    setRows(rs => rs.map((r, idx) => idx === i ? { ...r, excluded: !r.excluded } : r));
  };

  const toImport = rows?.filter(r => !r.excluded && r.status === "valid") ?? [];
  const countValid     = rows?.filter(r => r.status === "valid").length ?? 0;
  const countInvalid   = rows?.filter(r => r.status === "invalid").length ?? 0;
  const countDuplicate = rows?.filter(r => r.status === "duplicate").length ?? 0;
  const countExists    = rows?.filter(r => r.status === "exists").length ?? 0;

  const handleImport = async () => {
    if (!toImport.length) return;
    setProgress({ done: 0, total: toImport.length });
    let imported = 0;
    let failed = 0;
    for (const row of toImport) {
      try {
        await addChannel({ url: row.handle });
        imported++;
      } catch {
        failed++;
      }
      setProgress({ done: imported + failed, total: toImport.length });
    }
    if (imported > 0) toast.success(`Добавлено ${imported} каналов`);
    if (failed > 0) toast.error(`Ошибка при добавлении ${failed} каналов`);
    onImported();
    onClose();
  };

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-xl animate-in">
        <div className="modal-header">
          <span className="modal-title">Загрузить список каналов</span>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* Drop zone */}
          {!rows && (
            <div
              className={`dropzone${dragging ? " dropzone-active" : ""}`}
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onClick={() => fileRef.current?.click()}
            >
              <input
                ref={fileRef}
                type="file"
                accept=".csv,.txt"
                style={{ display: "none" }}
                onChange={e => handleFile(e.target.files[0])}
              />
              <div style={{
                width: 38, height: 38,
                background: "var(--raised)", border: "1px solid var(--border-s)",
                borderRadius: "var(--r-lg)",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "var(--text-4)",
              }}>
                <Upload size={17} />
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-2)", marginBottom: 3 }}>
                  Перетащите файл или нажмите для выбора
                </div>
                <div style={{ fontSize: 11, color: "var(--text-4)" }}>
                  .csv, .txt — по одному каналу на строку
                </div>
              </div>
              <div style={{
                marginTop: 4, padding: "6px 12px",
                background: "var(--overlay)", border: "1px solid var(--border)",
                borderRadius: "var(--r-sm)",
                fontSize: 11, fontFamily: "var(--mono)", color: "var(--text-4)",
                lineHeight: 1.7,
              }}>
                @channel · t.me/channel · https://t.me/+HASH · t.me/joinchat/…
              </div>
            </div>
          )}

          {/* Results */}
          {rows && (
            <>
              {/* Stats bar */}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {[
                  { label: `${countValid} валидных`,    color: "var(--green)",  border: "var(--green-border)",  bg: "var(--green-bg)" },
                  { label: `${countInvalid} неверных`,  color: "var(--red)",    border: "var(--red-border)",    bg: "var(--red-bg)", hide: countInvalid === 0 },
                  { label: `${countDuplicate} дублей`,  color: "var(--amber)",  border: "var(--amber-border)",  bg: "var(--amber-bg)", hide: countDuplicate === 0 },
                  { label: `${countExists} уже есть`,   color: "var(--text-3)", border: "var(--border)",        bg: "var(--surface)", hide: countExists === 0 },
                ].filter(s => !s.hide).map((s, i) => (
                  <span key={i} style={{
                    display: "inline-flex", alignItems: "center",
                    height: 22, padding: "0 8px", borderRadius: 100,
                    background: s.bg, border: `1px solid ${s.border}`, color: s.color,
                    fontSize: 11, fontWeight: 600,
                  }}>
                    {s.label}
                  </span>
                ))}
                <button
                  style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "var(--text-4)", fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}
                  onClick={() => setRows(null)}
                >
                  <RefreshCw size={10} /> Выбрать другой файл
                </button>
              </div>

              {/* Preview table */}
              <div style={{
                maxHeight: 300, overflowY: "auto",
                border: "1px solid var(--border)", borderRadius: "var(--r-lg)",
              }}>
                <table className="table" style={{ tableLayout: "fixed", width: "100%" }}>
                  <colgroup>
                    <col style={{ width: 28 }} />
                    <col style={{ width: 60 }} />
                    <col />
                    <col style={{ width: 76 }} />
                    <col style={{ width: 100 }} />
                  </colgroup>
                  <thead style={{ position: "sticky", top: 0, background: "var(--surface)", zIndex: 1 }}>
                    <tr>
                      <th />
                      <th>Тип</th>
                      <th>Канал</th>
                      <th>Статус</th>
                      <th>Причина</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr
                        key={i}
                        style={{ opacity: row.excluded ? 0.4 : 1, cursor: row.status === "valid" ? "pointer" : "default" }}
                        onClick={() => row.status === "valid" && toggleRow(i)}
                      >
                        <td style={{ textAlign: "center", padding: "8px 6px" }}>
                          {row.status === "valid" && (
                            <div style={{
                              width: 14, height: 14, borderRadius: 3,
                              border: `1px solid ${row.excluded ? "var(--border-s)" : "var(--p)"}`,
                              background: row.excluded ? "var(--raised)" : "var(--p)",
                              display: "flex", alignItems: "center", justifyContent: "center",
                              margin: "0 auto",
                            }}>
                              {!row.excluded && <CheckCircle size={9} color="var(--p-fg)" />}
                            </div>
                          )}
                        </td>
                        <td>
                          <span style={{
                            display: "inline-flex", alignItems: "center",
                            height: 16, padding: "0 5px", borderRadius: 3,
                            background: row.type === "invite" ? "var(--blue-bg)" : "var(--raised)",
                            border: `1px solid ${row.type === "invite" ? "var(--blue-border)" : "var(--border)"}`,
                            color: row.type === "invite" ? "var(--blue)" : "var(--text-4)",
                            fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
                          }}>
                            {row.type === "invite" ? "INVITE" : "@USER"}
                          </span>
                        </td>
                        <td style={{ fontFamily: "var(--mono)", fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 0 }}>
                          <span title={row.handle}>{row.handle}</span>
                        </td>
                        <td><ImportBadge status={row.status} /></td>
                        <td style={{ color: "var(--text-4)", fontSize: 11 }}>{row.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {countValid > 0 && (
                <p style={{ fontSize: 11, color: "var(--text-4)" }}>
                  Нажмите на строку, чтобы снять/выбрать. К добавлению: <strong style={{ color: "var(--text-2)" }}>{toImport.length}</strong> каналов.
                </p>
              )}
            </>
          )}

          {/* Progress */}
          {progress && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 12, color: "var(--text-3)" }}>
                Добавление… {progress.done} / {progress.total}
              </div>
              <div style={{ height: 3, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{
                  height: "100%",
                  width: `${(progress.done / progress.total) * 100}%`,
                  background: "var(--p)",
                  borderRadius: 2,
                  transition: "width 200ms",
                }} />
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={!!progress}>Отмена</button>
          <div style={{ flex: 1 }} />
          <button
            className="btn btn-primary btn-sm"
            disabled={!toImport.length || !!progress}
            onClick={handleImport}
          >
            {progress
              ? <><span className="spinner" style={{ width: 13, height: 13 }} /> Добавление…</>
              : <>Добавить {toImport.length > 0 ? toImport.length : ""} каналов</>
            }
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────────────────────── */

export default function ChannelsPage() {
  const [channels, setChannels]   = useState([]);
  const [loading, setLoading]     = useState(true);
  const [url, setUrl]             = useState("");
  const [adding, setAdding]       = useState(false);
  const [showImport, setImport]   = useState(false);

  const load = async () => {
    try { setChannels(await getChannels()); }
    catch { toast.error("Не удалось загрузить каналы"); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, []);

  const handleAdd = async () => {
    if (!url.trim()) return;
    setAdding(true);
    try { await addChannel({ url: url.trim() }); setUrl(""); toast.success("Канал добавлен"); load(); }
    catch (e) { toast.error(e.response?.data?.detail || "Ошибка добавления"); }
    finally { setAdding(false); }
  };

  const handleToggle = async (id) => { await toggleChannel(id); load(); };

  const handleDelete = async (id) => {
    if (!confirm("Удалить этот канал?")) return;
    await deleteChannel(id); toast.success("Удалено"); load();
  };

  const active   = channels.filter(c => c.is_active).length;
  const inactive = channels.length - active;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 600, letterSpacing: "-0.03em", color: "var(--text-1)", marginBottom: 2 }}>
            Каналы
          </h1>
          <p style={{ fontSize: 13, color: "var(--text-3)" }}>
            {channels.length > 0
              ? `${channels.length} каналов — ${active} активных${inactive > 0 ? `, ${inactive} на паузе` : ""}`
              : "Добавьте каналы для рассылки"}
          </p>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setImport(true)}
          style={{ flexShrink: 0, marginTop: 4 }}
        >
          <FileText size={12} />
          Загрузить список
        </button>
      </div>

      {/* Add form */}
      <div className="card" style={{ padding: "16px 20px" }}>
        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-4)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 10 }}>
          Добавить канал или группу
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <div style={{ position: "relative", flex: 1 }}>
            <Link size={12} style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-4)", pointerEvents: "none" }} />
            <input
              className="inp"
              style={{ paddingLeft: 30 }}
              placeholder="@channel_username или https://t.me/channel"
              value={url}
              onChange={e => setUrl(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleAdd()}
            />
          </div>
          <button onClick={handleAdd} disabled={adding || !url.trim()} className="btn btn-primary">
            {adding
              ? <RefreshCw size={12} style={{ animation: "spin 0.65s linear infinite" }} />
              : <Plus size={13} />}
            {adding ? "Добавление…" : "Добавить"}
          </button>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-4)", marginTop: 6 }}>
          Принимает @username, t.me/username или полный URL
        </div>
      </div>

      {/* Channel list */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 60, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : channels.length === 0 ? (
        <div className="card">
          <div className="empty">
            <div className="empty-icon"><Hash size={18} /></div>
            <span className="empty-title">Нет каналов</span>
            <span className="empty-sub">
              Добавьте канал выше или загрузите список из .csv/.txt файла
            </span>
            <button
              className="btn btn-ghost btn-sm"
              style={{ marginTop: 8 }}
              onClick={() => setImport(true)}
            >
              <Upload size={12} /> Загрузить список
            </button>
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {channels.map(ch => (
            <div
              key={ch.id}
              className="card animate-fade-in"
              style={{
                padding: "11px 16px",
                opacity: ch.is_active ? 1 : 0.55,
                display: "flex",
                alignItems: "center",
                gap: 12,
              }}
            >
              <div style={{
                width: 30, height: 30,
                borderRadius: "var(--r-sm)",
                background: ch.is_active ? "var(--green-bg)" : "var(--raised)",
                border: `1px solid ${ch.is_active ? "var(--green-border)" : "var(--border)"}`,
                color: ch.is_active ? "var(--green)" : "var(--text-4)",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0,
              }}>
                <Hash size={13} />
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 500, fontSize: 13, color: "var(--text-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {ch.title || ch.url}
                </div>
                {ch.title && (
                  <div style={{ fontSize: 11, color: "var(--text-4)", fontFamily: "var(--mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {ch.url}
                  </div>
                )}
              </div>

              <span style={{
                fontSize: 11, fontWeight: 500,
                color: ch.is_active ? "var(--green)" : "var(--text-4)",
                flexShrink: 0,
              }}>
                {ch.is_active ? "Активен" : "Пауза"}
              </span>

              <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                <button
                  onClick={() => handleToggle(ch.id)}
                  className="btn btn-icon"
                  title={ch.is_active ? "Приостановить" : "Активировать"}
                  style={{ color: ch.is_active ? "var(--green)" : "var(--text-4)" }}
                >
                  {ch.is_active ? <ToggleRight size={16} /> : <ToggleLeft size={16} />}
                </button>
                <button
                  onClick={() => handleDelete(ch.id)}
                  className="btn btn-icon"
                  title="Удалить"
                  onMouseEnter={e => { e.currentTarget.style.background = "var(--red-bg)"; e.currentTarget.style.color = "var(--red)"; e.currentTarget.style.borderColor = "var(--red-border)"; }}
                  onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Import modal */}
      {showImport && (
        <ImportModal
          existingChannels={channels}
          onClose={() => setImport(false)}
          onImported={load}
        />
      )}
    </div>
  );
}
