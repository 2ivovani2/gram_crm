import "../styles/landing.css";
import { initCommonUI } from "../ui/common.js";
import { initLandingMotion } from "../motion/landing-motion.js";

initCommonUI();
const nav = document.querySelector("[data-landing-nav]");
const toggle = document.querySelector("[data-nav-toggle]");
const links = document.querySelector("[data-nav-links]");
const syncNav = () => nav?.classList.toggle("is-scrolled", scrollY > 18);
syncNav();
addEventListener("scroll", syncNav, { passive: true });
toggle?.addEventListener("click", () => {
  const open = links.classList.toggle("is-open");
  toggle.setAttribute("aria-expanded", String(open));
});
links?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
  links.classList.remove("is-open");
  toggle?.setAttribute("aria-expanded", "false");
}));

const library = document.querySelector("[data-learning-library]");
const player = library?.querySelector("[data-learning-player]");
library?.querySelectorAll("[data-learning-lesson]").forEach((lesson) => {
  lesson.addEventListener("click", () => {
    if (!player || lesson.classList.contains("is-active")) return;
    player.pause();
    player.querySelector("source").src = lesson.dataset.src;
    player.poster = lesson.dataset.poster;
    player.setAttribute("aria-label", lesson.dataset.title);
    library.querySelector("[data-learning-number]").textContent = `${lesson.dataset.number} · УРОК`;
    library.querySelector("[data-learning-title]").textContent = lesson.dataset.title;
    library.querySelector("[data-learning-duration]").textContent = `${lesson.dataset.duration} сек.`;
    library.querySelectorAll("[data-learning-lesson]").forEach(item => item.classList.toggle("is-active", item === lesson));
    player.load();
  });
});

const cleanupMotion = initLandingMotion();
addEventListener("pagehide", () => {
  cleanupMotion();
  removeEventListener("scroll", syncNav);
}, { once: true });
