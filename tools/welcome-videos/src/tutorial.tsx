import React from "react";
import {
  AbsoluteFill,
  AnimatedImage,
  Easing,
  Img,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { Tutorial } from "./data";

const acid = "#c4ff26";
const cyan = "#52dce8";
const paper = "#f4f3ea";
const muted = "#929990";
const font = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

const fade = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, end], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.16, 1, 0.3, 1),
  });

const Shell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ background: "#070908", color: paper, fontFamily: font, overflow: "hidden" }}>
    <div style={{ position: "absolute", width: 620, height: 620, borderRadius: 620, background: "rgba(196,255,38,.08)", filter: "blur(90px)", top: -260, right: -300 }} />
    <div style={{ position: "absolute", width: 420, height: 420, borderRadius: 420, background: "rgba(82,220,232,.07)", filter: "blur(80px)", bottom: -180, left: -180 }} />
    {children}
  </AbsoluteFill>
);

const Brand: React.FC<{ step?: string }> = ({ step }) => (
  <div style={{ position: "absolute", left: 54, right: 54, top: 42, display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 18, fontWeight: 800, letterSpacing: ".08em" }}>
    <span><b style={{ color: acid }}>G</b> GRAMLY</span>
    {step && <span style={{ color: muted, fontSize: 14 }}>{step}</span>}
  </div>
);

const Phone: React.FC<{ screen: Tutorial["scenes"][number]["screen"]; progress: number }> = ({ screen, progress }) => {
  const rows: Record<typeof screen, React.ReactNode> = {
    connect: <>
      <PanelTitle kicker="ПЕРВЫЙ ЗАПУСК" title="Подключения" />
      <StatusRow n="1" title="Owner-бот" text="Gramly открыт" done />
      <StatusRow n="2" title="@gramly_demo_bot" text="Токен проверен · webhook online" done={progress > .3} />
      <StatusRow n="3" title="Тестовый канал" text="Права администратора подтверждены" done={progress > .66} />
      <Action label={progress > .66 ? "Подключение готово" : "Проверить подключение"} />
    </>,
    welcome: <>
      <PanelTitle kicker="РЕДАКТОР" title="Первое приветствие" />
      <Toolbar />
      <div style={{ padding: 20, border: "1px solid #354034", borderRadius: 14, background: "#151a15", fontSize: 18, lineHeight: 1.55 }}>
        Привет, <b style={{ color: acid }}>{"{name}"}</b>!<br />Рады видеть вас в канале.
        <div style={{ borderLeft: `4px solid ${acid}`, background: "rgba(196,255,38,.08)", padding: "9px 12px", marginTop: 14, borderRadius: "0 9px 9px 0" }}>Начните с закреплённого поста.</div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", color: muted, marginTop: 16, fontSize: 14 }}><span>Черновик сохранён</span><span>1 / 1</span></div>
      <Action label={progress > .65 ? "Запустить на канале" : "Далее: предпросмотр"} />
    </>,
    creative: <>
      <PanelTitle kicker="СООБЩЕНИЕ 1" title="Креатив" />
      <div style={{ height: 190, borderRadius: 16, border: "1px solid #344033", background: "linear-gradient(145deg,#1f3417,#0c100d)", padding: 22, display: "flex", flexDirection: "column", justifyContent: "flex-end" }}><small style={{ color: cyan, letterSpacing: ".12em" }}>WELCOME KIT</small><strong style={{ fontSize: 31, marginTop: 6 }}>Всё важное — здесь</strong></div>
      <div style={{ marginTop: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <ColorButton color={acid} label="Начать" dark />
        <ColorButton color={cyan} label="Подробнее" dark />
        <ColorButton color="#e05d59" label="Отзывы" />
        <ColorButton color="#4d7de0" label="Поддержка" />
      </div>
      <label style={{ display: "flex", justifyContent: "space-between", marginTop: 20, padding: 16, background: "#151a15", borderRadius: 12 }}><span>Показывать превью ссылки</span><i style={{ width: 48, height: 26, borderRadius: 20, background: progress > .55 ? "#3b433b" : acid, padding: 4 }}><b style={{ display: "block", width: 18, height: 18, borderRadius: 18, background: "white", translate: progress > .55 ? "0" : "22px 0" }} /></i></label>
    </>,
    template: <>
      <PanelTitle kicker="КАТАЛОГ" title="Шаблоны" />
      <div style={{ display: "grid", gap: 12 }}>
        <TemplateCard title="Тёплое знакомство" tag="3 сообщения" selected={progress < .42} />
        <TemplateCard title="Навигация по каналу" tag="2 сообщения" selected={progress >= .42} />
      </div>
      <div style={{ marginTop: 18, padding: 18, border: `1px solid ${acid}`, borderRadius: 14 }}><small style={{ color: acid }}>НЕЗАВИСИМАЯ КОПИЯ</small><p style={{ color: muted, lineHeight: 1.5 }}>После установки откроется ваш черновик. Исходный шаблон останется без изменений.</p></div>
      <Action label={progress > .6 ? "Настроить черновик" : "Использовать шаблон"} />
    </>,
    audience: <>
      <PanelTitle kicker="БАЗА" title="Активные подписчики" />
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}><Pill label="Все 248" active /><Pill label="Канал 1" /><Pill label="Активные" /></div>
      <Contact name="Анна" source="Приветствие · 12:48" />
      <Contact name="Максим" source="Кнопка в посте · 12:31" />
      <Contact name="Илья" source="Приветствие · вчера" />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 18 }}><Metric label="Получателей" value="248" /><Metric label="Согласие" value="100%" /></div>
      <Action label={progress > .56 ? "Запустить рассылку" : "Создать рассылку"} />
    </>,
  };
  return <div style={{ position: "absolute", left: 58, right: 58, top: 105, bottom: 55, border: "1px solid #303730", borderRadius: 34, background: "rgba(14,18,15,.96)", padding: "44px 30px", boxShadow: "0 35px 90px #000" }}>{rows[screen]}</div>;
};

const PanelTitle = ({ kicker, title }: { kicker: string; title: string }) => <div style={{ marginBottom: 24 }}><small style={{ color: acid, letterSpacing: ".16em", fontWeight: 800 }}>{kicker}</small><h3 style={{ fontSize: 34, margin: "8px 0 0", letterSpacing: "-.04em" }}>{title}</h3></div>;
const Action = ({ label }: { label: string }) => <div style={{ marginTop: 22, padding: 17, borderRadius: 12, background: acid, color: "#081007", textAlign: "center", fontWeight: 800 }}>{label}</div>;
const StatusRow = ({ n, title, text, done = false }: { n: string; title: string; text: string; done?: boolean }) => <div style={{ display: "grid", gridTemplateColumns: "44px 1fr auto", alignItems: "center", gap: 12, padding: "18px 0", borderBottom: "1px solid #2a302a" }}><span style={{ width: 40, height: 40, borderRadius: 40, display: "grid", placeItems: "center", background: done ? acid : "#222822", color: done ? "#071006" : muted, fontWeight: 900 }}>{done ? "✓" : n}</span><span><b style={{ display: "block", fontSize: 17 }}>{title}</b><small style={{ color: muted, display: "block", marginTop: 4 }}>{text}</small></span><em style={{ color: done ? acid : muted, fontStyle: "normal", fontSize: 12 }}>{done ? "ГОТОВО" : "ДАЛЬШЕ"}</em></div>;
const Toolbar = () => <div style={{ display: "flex", gap: 7, marginBottom: 12 }}>{["B", "I", "U", "❝", "<>"].map((x, i) => <span key={x} style={{ border: `1px solid ${i === 3 ? acid : "#384038"}`, color: i === 3 ? acid : paper, width: 45, height: 38, borderRadius: 8, display: "grid", placeItems: "center", fontWeight: 800 }}>{x}</span>)}</div>;
const ColorButton = ({ color, label, dark = false }: { color: string; label: string; dark?: boolean }) => <div style={{ padding: 12, borderRadius: 9, textAlign: "center", fontWeight: 800, fontSize: 14, color: dark ? "#081007" : "white", background: color }}>{label}</div>;
const TemplateCard = ({ title, tag, selected }: { title: string; tag: string; selected: boolean }) => <div style={{ padding: 18, borderRadius: 14, border: `1px solid ${selected ? acid : "#323a32"}`, background: selected ? "rgba(196,255,38,.07)" : "#141814" }}><small style={{ color: selected ? acid : cyan }}>ПРИВЕТСТВИЕ</small><h4 style={{ margin: "10px 0 6px", fontSize: 20 }}>{title}</h4><span style={{ color: muted, fontSize: 13 }}>{tag} · Premium-эмодзи</span></div>;
const Pill = ({ label, active = false }: { label: string; active?: boolean }) => <span style={{ padding: "8px 10px", borderRadius: 20, border: `1px solid ${active ? acid : "#303830"}`, color: active ? acid : muted, fontSize: 12 }}>{label}</span>;
const Contact = ({ name, source }: { name: string; source: string }) => <div style={{ display: "grid", gridTemplateColumns: "42px 1fr auto", gap: 12, alignItems: "center", padding: "14px 0", borderBottom: "1px solid #293029" }}><span style={{ width: 40, height: 40, borderRadius: 40, background: "#d9ff70", color: "#071006", display: "grid", placeItems: "center", fontWeight: 900 }}>{name[0]}</span><span><b style={{ display: "block" }}>{name}</b><small style={{ color: muted }}>{source}</small></span><em style={{ color: acid, fontStyle: "normal", fontSize: 11 }}>АКТИВЕН</em></div>;
const Metric = ({ label, value }: { label: string; value: string }) => <div style={{ padding: 15, border: "1px solid #303830", borderRadius: 12 }}><small style={{ color: muted }}>{label}</small><b style={{ display: "block", fontSize: 28, marginTop: 5 }}>{value}</b></div>;

const Intro: React.FC<{ tutorial: Tutorial }> = ({ tutorial }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return <Shell><Brand step={`${tutorial.number} / 05`} /><div style={{ position: "absolute", left: 54, right: 54, top: 170, opacity: fade(frame, 0, .7 * fps), translate: `0 ${interpolate(frame, [0, .8 * fps], [34, 0], { extrapolateRight: "clamp" })}px` }}><div style={{ color: acid, fontWeight: 900, letterSpacing: ".16em", fontSize: 16 }}>ВИДЕОУРОК {tutorial.number}</div><h1 style={{ fontSize: 75, lineHeight: .98, letterSpacing: "-.065em", margin: "24px 0", maxWidth: 620 }}>{tutorial.title}</h1><p style={{ fontSize: 27, lineHeight: 1.45, color: muted, margin: 0 }}>{tutorial.subtitle}</p></div><AnimatedImage src={staticFile("cubigator-wave.webp")} style={{ position: "absolute", width: 340, height: 340, objectFit: "contain", left: 190, bottom: 100 }} /></Shell>;
};

const LessonScene: React.FC<{ tutorial: Tutorial; index: number }> = ({ tutorial, index }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const scene = tutorial.scenes[index];
  const progress = frame / durationInFrames;
  const copyOpacity = interpolate(frame, [0, 10, durationInFrames - 10, durationInFrames], [.72, 1, 1, .72], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <Shell><Brand step={`${index + 1} / ${tutorial.scenes.length}`} /><div style={{ opacity: copyOpacity }}><div style={{ position: "absolute", left: 54, right: 54, top: 105 }}><small style={{ color: acid, fontWeight: 900, letterSpacing: ".16em" }}>{scene.eyebrow}</small><h2 style={{ fontSize: 47, letterSpacing: "-.05em", margin: "10px 0 8px" }}>{scene.title}</h2><p style={{ color: muted, fontSize: 20, lineHeight: 1.45, margin: 0 }}>{scene.note}</p></div><div style={{ position: "absolute", left: 18, right: 18, top: 290, bottom: 0, scale: interpolate(frame, [0, 18], [.96, 1], { extrapolateRight: "clamp" }) }}><Phone screen={scene.screen} progress={progress} /></div></div></Shell>;
};

const Outro: React.FC<{ tutorial: Tutorial }> = ({ tutorial }) => {
  const frame = useCurrentFrame();
  return <Shell><Brand /><div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center", padding: 70, opacity: interpolate(frame, [0, 12], [.65, 1], { extrapolateRight: "clamp" }) }}><div><Img src={staticFile("cubigator-avatar.png")} style={{ width: 250, height: 250 }} /><small style={{ display: "block", color: acid, fontWeight: 900, letterSpacing: ".16em", marginTop: 25 }}>ГОТОВО</small><h2 style={{ fontSize: 58, letterSpacing: "-.055em", lineHeight: 1.03, margin: "18px 0" }}>{tutorial.title}</h2><p style={{ color: muted, fontSize: 23, lineHeight: 1.45 }}>Откройте Gramly и повторите шаги на своём боте.</p><div style={{ display: "inline-block", marginTop: 25, padding: "16px 25px", color: "#071006", background: acid, borderRadius: 12, fontWeight: 900 }}>Открыть Gramly</div></div></div></Shell>;
};

export const TutorialVideo: React.FC<{ tutorial: Tutorial }> = ({ tutorial }) => {
  const { fps } = useVideoConfig();
  const intro = 3 * fps;
  const outro = 3 * fps;
  const sceneLength = Math.floor((tutorial.duration * fps - intro - outro) / tutorial.scenes.length);
  return <AbsoluteFill>
    <Sequence durationInFrames={intro}><Intro tutorial={tutorial} /></Sequence>
    {tutorial.scenes.map((_, index) => <Sequence key={index} from={intro + index * sceneLength} durationInFrames={sceneLength}><LessonScene tutorial={tutorial} index={index} /></Sequence>)}
    <Sequence from={tutorial.duration * fps - outro} durationInFrames={outro}><Outro tutorial={tutorial} /></Sequence>
  </AbsoluteFill>;
};

export const TutorialPoster: React.FC<{ tutorial: Tutorial }> = ({ tutorial }) => <Shell><Brand step={`${tutorial.number} / 05`} /><div style={{ position: "absolute", left: 54, right: 54, top: 170 }}><div style={{ color: acid, fontWeight: 900, letterSpacing: ".16em", fontSize: 16 }}>ВИДЕОУРОК</div><h1 style={{ fontSize: 75, lineHeight: .98, letterSpacing: "-.065em", margin: "24px 0", maxWidth: 620 }}>{tutorial.title}</h1><p style={{ fontSize: 27, lineHeight: 1.45, color: muted }}>{tutorial.subtitle}</p></div><Img src={staticFile("cubigator-avatar.png")} style={{ position: "absolute", width: 350, height: 350, left: 185, bottom: 100 }} /></Shell>;
