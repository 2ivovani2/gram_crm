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

const cleanupMotion = initLandingMotion();
addEventListener("pagehide", () => {
  cleanupMotion();
  removeEventListener("scroll", syncNav);
}, { once: true });
