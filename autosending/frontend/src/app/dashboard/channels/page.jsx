"use client";
import { useEffect, useRef, useState, useCallback } from "react";
import { createPortal } from "react-dom";
import { getChannels, addChannel, toggleChannel, deleteChannel, searchChannels, bulkAddChannels } from "@/lib/api";
import toast from "react-hot-toast";
import {
  Plus, Trash2, Hash, Link2, RefreshCw, Upload, X,
  CheckCircle2, AlertCircle, Copy, History, Search,
  ExternalLink, Pause, Play, ArrowDownUp, SlidersHorizontal,
  FileText, Check, Telescope, Users, Megaphone, MessageSquare,
  TrendingUp, Globe, Key, ChevronDown, ChevronUp,
} from "lucide-react";

/* ─── Channel normalization (logic unchanged) ────────────────────────────── */

const TG_USERNAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{3,30}$/;

function normalizeChannel(raw) {
  let s = raw.trim().replace(/^["']+|["']+$/g, "").trim();
  if (!s || s.startsWith("#")) return null;
  const noProto = s.replace(/^https?:\/\//i, "");
  const noTme   = noProto.replace(/^(?:t\.me|telegram\.me)\//i, "");

  if (noTme.startsWith("+") || /^joinchat\//i.test(noTme)) {
    const path = noTme.split("?")[0].split("#")[0].replace(/\/+$/, "");
    return { handle: "https://t.me/" + path, type: "invite" };
  }

  const username = noTme.replace(/^@+/, "").split("?")[0].split("#")[0].replace(/\/+$/, "");
  if (!username) return null;
  return { handle: "@" + username, type: "username" };
}

function isValidChannel({ handle, type }) {
  if (type === "invite") return true;
  return TG_USERNAME_RE.test(handle.slice(1));
}

function parseFile(text, existingUrls) {
  const existing = new Set(existingUrls.map(u => u.toLowerCase()));
  const seen = new Set();
  const rows = [];
  for (const raw of text.split(/[\r\n]+/)) {
    const trimmed = raw.trim().replace(/^["']+|["']+$/g, "").trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const normalized = normalizeChannel(trimmed);
    if (!normalized) { rows.push({ raw: trimmed, handle: trimmed, type: "username", status: "invalid", reason: "Не удалось распознать" }); continue; }
    const { handle, type } = normalized;
    const key = handle.toLowerCase();
    if (existing.has(key))      { rows.push({ raw: trimmed, handle, type, status: "exists",    reason: "Уже в базе" }); continue; }
    if (seen.has(key))          { rows.push({ raw: trimmed, handle, type, status: "duplicate", reason: "Дубликат в файле" }); continue; }
    if (!isValidChannel(normalized)) { rows.push({ raw: trimmed, handle, type, status: "invalid", reason: "Неверный username" }); continue; }
    seen.add(key);
    rows.push({ raw: trimmed, handle, type, status: "valid", reason: "" });
  }
  return rows;
}

/* ─── Status badge for import preview ───────────────────────────────────── */

function ImportBadge({ status }) {
  const map = {
    valid:     { tint: "var(--emerald-tint)", edge: "var(--emerald-edge)", color: "var(--emerald)", label: "Ок" },
    invalid:   { tint: "var(--coral-tint)",   edge: "var(--coral-edge)",   color: "var(--coral)",   label: "Неверный" },
    duplicate: { tint: "var(--amber-tint)",   edge: "var(--amber-edge)",   color: "var(--amber)",   label: "Дубликат" },
    exists:    { tint: "var(--surface-1)",    edge: "var(--line-2)",       color: "var(--ink-4)",   label: "Уже есть" },
  };
  const s = map[status] ?? map.invalid;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      height: 20, padding: "0 8px", borderRadius: 100,
      background: s.tint, border: `1px solid ${s.edge}`, color: s.color,
      fontSize: 11, fontWeight: 500, whiteSpace: "nowrap", flexShrink: 0,
    }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: "currentColor" }} />
      {s.label}
    </span>
  );
}

/* ─── CSV Import modal ───────────────────────────────────────────────────── */

function ImportModal({ onClose, existingChannels, onImported }) {
  const [rows, setRows]       = useState(null);
  const [dragging, setDrag]   = useState(false);
  const [progress, setProgress] = useState(null);
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
    e.preventDefault(); setDrag(false);
    handleFile(e.dataTransfer.files[0]);
  }, [handleFile]);

  const onDragOver  = (e) => { e.preventDefault(); setDrag(true); };
  const onDragLeave = () => setDrag(false);

  const toggleRow = (i) => setRows(rs => rs.map((r, idx) => idx === i ? { ...r, excluded: !r.excluded } : r));

  const toImport       = rows?.filter(r => !r.excluded && r.status === "valid") ?? [];
  const countValid     = rows?.filter(r => r.status === "valid").length ?? 0;
  const countInvalid   = rows?.filter(r => r.status === "invalid").length ?? 0;
  const countDuplicate = rows?.filter(r => r.status === "duplicate").length ?? 0;
  const countExists    = rows?.filter(r => r.status === "exists").length ?? 0;

  const handleImport = async () => {
    if (!toImport.length) return;
    setProgress({ done: 0, total: toImport.length });
    let imported = 0, failed = 0;
    for (const row of toImport) {
      try { await addChannel({ url: row.handle }); imported++; }
      catch { failed++; }
      setProgress({ done: imported + failed, total: toImport.length });
    }
    if (imported > 0) toast.success(`Добавлено ${imported} каналов`);
    if (failed > 0)   toast.error(`Ошибка при добавлении ${failed} каналов`);
    onImported(); onClose();
  };

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-xl animate-in">
        <div className="modal-header">
          <div>
            <div className="modal-title">Загрузить список каналов</div>
            <div className="modal-subtitle">CSV или TXT · по одному каналу на строку</div>
          </div>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="modal-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!rows && (
            <div
              className={`dropzone${dragging ? " dropzone-active" : ""}`}
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onClick={() => fileRef.current?.click()}
            >
              <input ref={fileRef} type="file" accept=".csv,.txt" style={{ display: "none" }}
                onChange={e => handleFile(e.target.files[0])} />
              <div
                style={{
                  width: 48, height: 48,
                  background: "linear-gradient(180deg, var(--surface-2), var(--surface-1))",
                  border: "1px solid var(--line-2)",
                  borderRadius: "var(--r-md)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  color: "var(--ink-2)",
                  boxShadow: "var(--shadow-1)",
                }}
              >
                <Upload size={22} strokeWidth={1.8} />
              </div>
              <div>
                <div style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink-1)" }}>
                  Перетащите файл или нажмите для выбора
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-4)", marginTop: 4 }}>
                  .csv, .txt · до 5000 строк
                </div>
              </div>
              <div style={{
                display: "flex", gap: 10, marginTop: 6, padding: "8px 14px",
                background: "var(--sunken)", border: "1px solid var(--line-2)", borderRadius: 8,
                fontSize: 11, fontFamily: "var(--mono)", color: "var(--ink-4)",
              }}>
                <code>@channel</code> · <code>t.me/channel</code> · <code>t.me/+invite</code>
              </div>
            </div>
          )}

          {rows && (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                {countValid > 0     && <span className="badge badge-success"><CheckCircle2 size={11} /> {countValid} валидных</span>}
                {countInvalid > 0   && <span className="badge badge-error"><AlertCircle size={11} /> {countInvalid} ошибок</span>}
                {countDuplicate > 0 && <span className="badge badge-pending"><Copy size={11} /> {countDuplicate} дублей</span>}
                {countExists > 0    && <span className="badge badge-neutral"><History size={11} /> {countExists} уже есть</span>}
                <button onClick={() => setRows(null)}
                  style={{ marginLeft: "auto", background: "none", border: "none", cursor: "pointer", color: "var(--ink-4)", fontSize: 11.5, display: "flex", alignItems: "center", gap: 4 }}>
                  <RefreshCw size={11} /> Выбрать другой файл
                </button>
              </div>

              <div style={{
                maxHeight: 360, overflowY: "auto",
                border: "1px solid var(--line-2)", borderRadius: 11,
              }}>
                <table className="table" style={{ tableLayout: "fixed", width: "100%" }}>
                  <colgroup>
                    <col style={{ width: 32 }} />
                    <col style={{ width: 80 }} />
                    <col />
                    <col style={{ width: 110 }} />
                    <col style={{ width: 150 }} />
                  </colgroup>
                  <thead style={{ position: "sticky", top: 0, zIndex: 1 }}>
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
                      <tr key={i}
                        style={{ opacity: row.excluded ? 0.4 : 1, cursor: row.status === "valid" ? "pointer" : "default" }}
                        onClick={() => row.status === "valid" && toggleRow(i)}
                      >
                        <td style={{ textAlign: "center", padding: "10px 6px" }}>
                          {row.status === "valid" && (
                            <div className={`checkbox${row.excluded ? "" : " on"}`}>
                              {!row.excluded && <Check size={10} strokeWidth={3} />}
                            </div>
                          )}
                        </td>
                        <td>
                          <span style={{
                            display: "inline-flex", alignItems: "center",
                            padding: "2px 7px", borderRadius: 5,
                            background: row.type === "invite" ? "var(--azure-tint)" : "var(--surface-2)",
                            border: `1px solid ${row.type === "invite" ? "var(--azure-edge)" : "var(--line-2)"}`,
                            color: row.type === "invite" ? "var(--azure)" : "var(--ink-3)",
                            fontSize: 9.5, fontWeight: 700, letterSpacing: "0.08em", fontFamily: "var(--mono)",
                          }}>
                            {row.type === "invite" ? "INVITE" : "@USER"}
                          </span>
                        </td>
                        <td style={{
                          fontFamily: "var(--mono)", fontSize: 12,
                          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                          maxWidth: 0, color: "var(--ink-1)",
                        }}>
                          <span title={row.handle}>{row.handle}</span>
                        </td>
                        <td><ImportBadge status={row.status} /></td>
                        <td style={{ color: "var(--ink-4)", fontSize: 12 }}>{row.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {countValid > 0 && (
                <p style={{ fontSize: 11.5, color: "var(--ink-4)" }}>
                  Нажмите на строку, чтобы снять/выбрать. К добавлению: <strong style={{ color: "var(--ink-2)" }}>{toImport.length}</strong> каналов.
                </p>
              )}
            </>
          )}

          {progress && (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ fontSize: 12.5, color: "var(--ink-3)", fontFamily: "var(--mono)" }}>
                Добавление… {progress.done} / {progress.total}
              </div>
              <div style={{ height: 4, background: "var(--sunken)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{
                  height: "100%",
                  width: `${(progress.done / progress.total) * 100}%`,
                  background: "linear-gradient(90deg, var(--gold), var(--gold-hi))",
                  borderRadius: 2, transition: "width 200ms",
                }} />
              </div>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose} disabled={!!progress}>Отмена</button>
          <div style={{ flex: 1 }} />
          <button className="btn-primary" disabled={!toImport.length || !!progress} onClick={handleImport}>
            {progress
              ? <><div className="spinner" style={{ width: 13, height: 13 }} /> Добавление…</>
              : <><Plus size={14} /> Импортировать {toImport.length || ""}</>
            }
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

/* ─── Parser modal ───────────────────────────────────────────────────────── */

const TGSTAT_KEY = "tgstat_token";

function fmtNum(n) {
  if (!n) return null;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(".0", "") + "M";
  if (n >= 1_000)     return (n / 1_000).toFixed(1).replace(".0", "") + "k";
  return String(n);
}

function ParserModal({ onClose, onAdded }) {
  const [query, setQuery]           = useState("");
  const [minMembers, setMinMembers] = useState(500);
  const [groupsOnly, setGroupsOnly] = useState(true);
  const [tgstatKey, setTgstatKey]   = useState(() => typeof localStorage !== "undefined" ? localStorage.getItem(TGSTAT_KEY) || "" : "");
  const [showSettings, setSettings] = useState(false);
  const [loading, setLoading]       = useState(false);
  const [results, setResults]       = useState(null);
  const [selected, setSelected]     = useState(new Set());
  const [adding, setAdding]         = useState(false);

  const saveTgstat = (v) => { setTgstatKey(v); localStorage.setItem(TGSTAT_KEY, v); };

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true); setResults(null); setSelected(new Set());
    try {
      const data = await searchChannels({
        q: query.trim(),
        min_members: minMembers,
        groups_only: groupsOnly,
        limit: 80,
        ...(tgstatKey ? { tgstat_token: tgstatKey } : {}),
      });
      setResults(data);
    } catch (e) {
      toast.error(e.response?.data?.detail || "Ошибка поиска");
    } finally {
      setLoading(false);
    }
  };

  const toggleCh = (url) => setSelected(s => {
    const n = new Set(s); n.has(url) ? n.delete(url) : n.add(url); return n;
  });
  const toggleAll = () => {
    const available = (results || []).filter(c => !c.already_added).map(c => c.url);
    setSelected(s => s.size === available.length ? new Set() : new Set(available));
  };

  const handleAdd = async () => {
    if (!selected.size) return;
    setAdding(true);
    try {
      const res = await bulkAddChannels([...selected]);
      toast.success(`Добавлено: ${res.added}${res.skipped ? `, уже было: ${res.skipped}` : ""}`);
      setResults(r => r.map(c => selected.has(c.url) ? { ...c, already_added: true } : c));
      setSelected(new Set());
      onAdded();
    } catch (e) {
      toast.error(e.response?.data?.detail || "Ошибка добавления");
    } finally {
      setAdding(false);
    }
  };

  const available = (results || []).filter(c => !c.already_added);

  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-xl animate-in" style={{ maxWidth: 760, maxHeight: "90vh", display: "flex", flexDirection: "column" }}>

        {/* Header */}
        <div className="modal-header">
          <div>
            <div className="modal-title">Парсер каналов</div>
            <div className="modal-subtitle">Telegram Search{tgstatKey ? " + tgstat.ru" : " · добавь tgstat ключ для расширенной аналитики"}</div>
          </div>
          <button className="modal-close" onClick={onClose}><X size={14} /></button>
        </div>

        <div className="modal-body" style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 14 }}>

          {/* Search bar */}
          <div style={{ display: "flex", gap: 8 }}>
            <div style={{ position: "relative", flex: 1 }}>
              <Search size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36 }}
                placeholder="Ключевое слово: крипто, авто, маркетинг…"
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => e.key === "Enter" && search()}
                autoFocus
              />
            </div>
            <button className="btn-primary" onClick={search} disabled={loading || !query.trim()}>
              {loading ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Search size={14} />}
              Найти
            </button>
          </div>

          {/* Filters row */}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 12, color: "var(--ink-4)" }}>Мин. подписчиков:</span>
              {[0, 500, 1000, 5000, 10000].map(v => (
                <button key={v} onClick={() => setMinMembers(v)} className="btn-sm" style={{
                  background: minMembers === v ? "var(--surface-3)" : "transparent",
                  border: "1px solid " + (minMembers === v ? "var(--line-3)" : "var(--line-2)"),
                  color: minMembers === v ? "var(--ink-1)" : "var(--ink-4)",
                  fontWeight: minMembers === v ? 600 : 400,
                  cursor: "pointer", borderRadius: 6, height: 28, padding: "0 10px", fontSize: 11.5,
                }}>
                  {v === 0 ? "Все" : fmtNum(v)}
                </button>
              ))}
            </div>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12, color: "var(--ink-3)", userSelect: "none" }}>
              <input type="checkbox" checked={groupsOnly} onChange={e => setGroupsOnly(e.target.checked)} style={{ accentColor: "var(--gold-hi)" }} />
              Только группы (можно писать)
            </label>
            <button onClick={() => setSettings(s => !s)} className="btn-ghost btn-sm" style={{ marginLeft: "auto", gap: 4 }}>
              <Key size={11} /> tgstat API {showSettings ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            </button>
          </div>

          {/* tgstat settings */}
          {showSettings && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", padding: "12px 14px", background: "var(--surface-1)", borderRadius: 10, border: "1px solid var(--line-2)" }}>
              <Key size={13} style={{ color: "var(--ink-4)", flexShrink: 0 }} />
              <input
                className="inp"
                style={{ flex: 1, fontFamily: "var(--mono)", fontSize: 12 }}
                placeholder="tgstat API токен (с tgstat.ru/api)"
                value={tgstatKey}
                onChange={e => saveTgstat(e.target.value)}
              />
              {tgstatKey && (
                <span style={{ fontSize: 11, color: "var(--emerald)", whiteSpace: "nowrap" }}>
                  <CheckCircle2 size={11} style={{ verticalAlign: "middle" }} /> Активен
                </span>
              )}
            </div>
          )}

          {/* Results */}
          {loading && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 90, borderRadius: 10 }} />
              ))}
            </div>
          )}

          {results && !loading && (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                  Найдено: <b style={{ color: "var(--ink-1)" }}>{results.length}</b>
                  {tgstatKey && <span style={{ marginLeft: 6, color: "var(--emerald)", fontSize: 11 }}>· с tgstat аналитикой</span>}
                </span>
                {available.length > 0 && (
                  <button onClick={toggleAll} className="btn-ghost btn-sm" style={{ fontSize: 11 }}>
                    {selected.size === available.length ? "Снять все" : `Выбрать все (${available.length})`}
                  </button>
                )}
              </div>

              {results.length === 0 ? (
                <div style={{ textAlign: "center", padding: "32px 0", color: "var(--ink-4)", fontSize: 13 }}>
                  Ничего не найдено — попробуй другое ключевое слово
                </div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(215px, 1fr))", gap: 8 }}>
                  {results.map(ch => {
                    const sel = selected.has(ch.url);
                    const grp = !ch.is_broadcast;
                    return (
                      <div
                        key={ch.url}
                        onClick={() => !ch.already_added && toggleCh(ch.url)}
                        style={{
                          padding: "12px 14px",
                          borderRadius: 10,
                          border: `1.5px solid ${sel ? "var(--gold-hi)" : ch.already_added ? "var(--line-2)" : "var(--line-2)"}`,
                          background: sel ? "var(--gold-tint)" : ch.already_added ? "var(--surface-1)" : "var(--surface-1)",
                          cursor: ch.already_added ? "default" : "pointer",
                          opacity: ch.already_added ? 0.55 : 1,
                          transition: "border-color 0.12s, background 0.12s",
                          display: "flex", flexDirection: "column", gap: 6,
                          position: "relative",
                        }}
                      >
                        {/* Type + source badge */}
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <span style={{
                            display: "inline-flex", alignItems: "center", gap: 3,
                            padding: "2px 6px", borderRadius: 4, fontSize: 9.5, fontWeight: 700,
                            background: grp ? "var(--emerald-tint)" : "var(--surface-2)",
                            border: `1px solid ${grp ? "var(--emerald-edge)" : "var(--line-2)"}`,
                            color: grp ? "var(--emerald)" : "var(--ink-4)",
                          }}>
                            {grp ? <MessageSquare size={9} /> : <Megaphone size={9} />}
                            {grp ? "ГРУППА" : "КАНАЛ"}
                          </span>
                          {ch.source === "tgstat" && (
                            <span style={{ fontSize: 9.5, color: "var(--ink-5)", fontWeight: 600 }}>tgstat</span>
                          )}
                          {ch.already_added && (
                            <span style={{ fontSize: 9.5, color: "var(--ink-4)", marginLeft: "auto" }}>уже есть</span>
                          )}
                          {sel && !ch.already_added && (
                            <span style={{ marginLeft: "auto" }}>
                              <CheckCircle2 size={13} style={{ color: "var(--gold-hi)" }} />
                            </span>
                          )}
                        </div>

                        {/* Title */}
                        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink-1)", lineHeight: 1.3, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                          {ch.title}
                        </div>

                        {/* Stats row */}
                        <div style={{ display: "flex", gap: 10, fontSize: 11.5, color: "var(--ink-4)", flexWrap: "wrap" }}>
                          {ch.members != null && (
                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                              <Users size={10} /> {fmtNum(ch.members)}
                            </span>
                          )}
                          {ch.avg_reach != null && (
                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                              <TrendingUp size={10} /> {fmtNum(ch.avg_reach)} охват
                            </span>
                          )}
                          {ch.er != null && (
                            <span style={{ display: "flex", alignItems: "center", gap: 3, color: "var(--gold-hi)" }}>
                              ER {(ch.er * 100).toFixed(1)}%
                            </span>
                          )}
                          {ch.country && (
                            <span style={{ display: "flex", alignItems: "center", gap: 3 }}>
                              <Globe size={10} /> {ch.country}
                            </span>
                          )}
                        </div>

                        {/* Description */}
                        {ch.description && (
                          <div style={{ fontSize: 11, color: "var(--ink-5)", lineHeight: 1.45, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                            {ch.description}
                          </div>
                        )}

                        {/* url */}
                        <div style={{ fontSize: 10.5, fontFamily: "var(--mono)", color: "var(--ink-5)", marginTop: 2 }}>
                          {ch.url}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="modal-footer">
          <button className="btn-ghost" onClick={onClose}>Закрыть</button>
          <div style={{ flex: 1 }} />
          {selected.size > 0 && (
            <button className="btn-primary" onClick={handleAdd} disabled={adding}>
              {adding ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} /> : <Plus size={14} />}
              Добавить {selected.size} канал{selected.size % 10 === 1 && selected.size !== 11 ? "" : selected.size % 10 < 5 && (selected.size < 10 || selected.size > 20) ? "а" : "ов"}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

/* ─── Main page ──────────────────────────────────────────────────────────── */

function formatK(n) {
  if (n == null) return null;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(".0","") + "M";
  if (n >= 1000) return (n / 1000).toFixed(1).replace(".0","") + "k";
  return String(n);
}

export default function ChannelsPage() {
  const [channels, setChannels] = useState([]);
  const [loading, setLoading]   = useState(true);
  const [url, setUrl]           = useState("");
  const [adding, setAdding]     = useState(false);
  const [showImport, setImport]   = useState(false);
  const [showParser, setShowParser] = useState(false);
  const [query, setQuery]       = useState("");
  const [sortMode, setSortMode]       = useState("default");
  const [filterStatus, setFilterStatus] = useState("all");

  const SORT_MODES  = ["default", "az", "za", "active"];
  const SORT_LABELS = { default: "По умолчанию", az: "A → Z", za: "Z → A", active: "Активные первыми" };
  const FILTER_MODES  = ["all", "active", "paused"];
  const FILTER_LABELS = { all: "Все", active: "Активные", paused: "На паузе" };
  const cycleSort   = () => setSortMode(m => SORT_MODES[(SORT_MODES.indexOf(m) + 1) % SORT_MODES.length]);
  const cycleFilter = () => setFilterStatus(f => FILTER_MODES[(FILTER_MODES.indexOf(f) + 1) % FILTER_MODES.length]);

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

  let filtered = query
    ? channels.filter(c => (c.url + " " + (c.title || "")).toLowerCase().includes(query.toLowerCase()))
    : [...channels];
  if (filterStatus === "active") filtered = filtered.filter(c => c.is_active);
  if (filterStatus === "paused") filtered = filtered.filter(c => !c.is_active);
  if (sortMode === "az")     filtered = [...filtered].sort((a, b) => (a.title || a.url).localeCompare(b.title || b.url));
  if (sortMode === "za")     filtered = [...filtered].sort((a, b) => (b.title || b.url).localeCompare(a.title || a.url));
  if (sortMode === "active") filtered = [...filtered].sort((a, b) => (b.is_active ? 1 : 0) - (a.is_active ? 1 : 0));

  return (
    <div className="animate-fade-in" style={{ display: "flex", flexDirection: "column", gap: 24 }}>
      {/* Header */}
      <div className="page-head">
        <div>
          <h1 className="page-title">Каналы</h1>
          <p className="page-sub">
            {channels.length > 0
              ? `${channels.length} каналов · ${active} активны${inactive > 0 ? ` · ${inactive} на паузе` : ""}`
              : "Добавьте каналы для рассылки"}
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost" onClick={() => setShowParser(true)}><Telescope size={14} /> Парсер каналов</button>
          <button className="btn-ghost" onClick={() => setImport(true)}><FileText size={14} /> Импорт CSV</button>
        </div>
      </div>

      {/* Quick add */}
      <div className="card card-no-hover">
        <div style={{ padding: "16px 22px" }}>
          <div style={{ display: "flex", gap: 10, alignItems: "stretch" }}>
            <div style={{ position: "relative", flex: 1 }}>
              <Link2 size={14} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
              <input
                className="inp"
                style={{ paddingLeft: 36, fontFamily: "var(--mono)", fontSize: 12.5 }}
                placeholder="@channel  ·  t.me/channel  ·  https://t.me/+invite"
                value={url}
                onChange={e => setUrl(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleAdd()}
              />
            </div>
            <button onClick={handleAdd} disabled={adding || !url.trim()} className="btn-primary">
              {adding
                ? <RefreshCw size={13} style={{ animation: "spin 0.7s linear infinite" }} />
                : <Plus size={14} />}
              {adding ? "Добавление…" : "Добавить"}
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--ink-4)", marginTop: 8 }}>
            Принимает @username, t.me/username или полный URL приглашения
          </div>
        </div>
      </div>

      {/* Search row */}
      {channels.length > 0 && (
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div style={{ position: "relative", flex: 1, maxWidth: 360 }}>
            <Search size={13} style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "var(--ink-4)", pointerEvents: "none" }} />
            <input className="inp" style={{ paddingLeft: 36 }} placeholder="Поиск по каналам…" value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <span style={{ fontSize: 12, color: "var(--ink-4)" }}>
            {filtered.length} результат{filtered.length === 1 ? "" : filtered.length < 5 ? "а" : "ов"}
          </span>
          <div style={{ flex: 1 }} />
          <button
            className="btn-ghost btn-sm"
            onClick={cycleSort}
            style={sortMode !== "default" ? { color: "var(--gold-hi)", borderColor: "var(--gold-edge)" } : {}}
            title="Нажмите для смены режима сортировки"
          >
            <ArrowDownUp size={12} /> {SORT_LABELS[sortMode]}
          </button>
          <button
            className="btn-ghost btn-sm"
            onClick={cycleFilter}
            style={filterStatus !== "all" ? { color: "var(--gold-hi)", borderColor: "var(--gold-edge)" } : {}}
            title="Нажмите для смены фильтра"
          >
            <SlidersHorizontal size={12} /> {FILTER_LABELS[filterStatus]}
          </button>
        </div>
      )}

      {/* Channel list */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 70, borderRadius: "var(--r-lg)" }} />
          ))}
        </div>
      ) : channels.length === 0 ? (
        <div className="card card-no-hover">
          <div className="empty">
            <div className="empty-icon"><Hash size={22} /></div>
            <span className="empty-title">Пока ни одного канала</span>
            <span className="empty-sub">
              Добавьте канал выше или загрузите список из .csv/.txt файла
            </span>
            <div style={{ marginTop: 14 }}>
              <button className="btn-ghost" onClick={() => setImport(true)}><Upload size={13} /> Загрузить список</button>
            </div>
          </div>
        </div>
      ) : (
        <div className="card card-no-hover" style={{ overflow: "hidden" }}>
          {filtered.map((ch, i) => (
            <div
              key={ch.id}
              style={{
                display: "flex", alignItems: "center", gap: 14,
                padding: "14px 22px",
                borderBottom: i < filtered.length - 1 ? "1px solid var(--line-1)" : "none",
                opacity: ch.is_active ? 1 : 0.65,
                transition: "background 120ms, opacity 200ms",
              }}
            >
              <div style={{
                width: 36, height: 36, borderRadius: 9,
                background: ch.is_active
                  ? "linear-gradient(180deg, var(--emerald-tint), rgba(142,201,154,0.02))"
                  : "var(--surface-2)",
                border: `1px solid ${ch.is_active ? "var(--emerald-edge)" : "var(--line-2)"}`,
                color: ch.is_active ? "var(--emerald)" : "var(--ink-4)",
                display: "flex", alignItems: "center", justifyContent: "center",
                flexShrink: 0,
                boxShadow: ch.is_active ? "0 0 16px rgba(142,201,154,0.12)" : "none",
              }}>
                <Hash size={15} strokeWidth={1.8} />
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink-1)", marginBottom: 3, letterSpacing: "-0.008em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {ch.title || ch.url}
                </div>
                <div style={{ display: "flex", gap: 14, fontSize: 12, color: "var(--ink-4)", alignItems: "center" }}>
                  {ch.title && <span style={{ fontFamily: "var(--mono)" }}>{ch.url}</span>}
                  {ch.members != null && (
                    <span style={{ fontFamily: "var(--mono)" }}>{formatK(ch.members)} подписчиков</span>
                  )}
                </div>
              </div>

              <span className="badge" style={ch.is_active
                ? { background: "var(--emerald-tint)", color: "var(--emerald)", borderColor: "var(--emerald-edge)" }
                : { background: "var(--surface-1)", color: "var(--ink-4)", borderColor: "var(--line-2)" }
              }>
                <span className="badge-dot" />
                {ch.is_active ? "Активен" : "Пауза"}
              </span>

              <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                <button onClick={() => handleToggle(ch.id)} className="btn-icon" title={ch.is_active ? "Поставить на паузу" : "Активировать"}>
                  {ch.is_active ? <Pause size={13} /> : <Play size={13} />}
                </button>
                <a
                  href={ch.url.startsWith("@") ? `https://t.me/${ch.url.slice(1)}` : ch.url}
                  target="_blank"
                  rel="noopener"
                  className="btn-icon"
                  style={{ textDecoration: "none" }}
                  title="Открыть в Telegram"
                >
                  <ExternalLink size={13} />
                </a>
                <button
                  onClick={() => handleDelete(ch.id)}
                  className="btn-icon"
                  title="Удалить"
                  onMouseEnter={e => { e.currentTarget.style.background = "var(--coral-tint)"; e.currentTarget.style.color = "var(--coral)"; e.currentTarget.style.borderColor = "var(--coral-edge)"; }}
                  onMouseLeave={e => { e.currentTarget.style.background = ""; e.currentTarget.style.color = ""; e.currentTarget.style.borderColor = ""; }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showImport && (
        <ImportModal
          existingChannels={channels}
          onClose={() => setImport(false)}
          onImported={load}
        />
      )}
      {showParser && (
        <ParserModal
          onClose={() => setShowParser(false)}
          onAdded={load}
        />
      )}
    </div>
  );
}
