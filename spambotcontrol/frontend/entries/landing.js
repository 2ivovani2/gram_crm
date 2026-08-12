import "../styles/landing.css";
import { initCommonUI } from "../ui/common.js";
import { initLandingMotion } from "../motion/landing-motion.js";

initCommonUI();
const nav = document.querySelector("[data-landing-nav]");
const toggle = document.querySelector("[data-nav-toggle]");
const links = document.querySelector("[data-nav-links]");
const syncNav = () => nav?.classList.toggle("is-scrolled", scrollY > 18);
syncNav(); addEventListener("scroll", syncNav, { passive: true });
toggle?.addEventListener("click", () => { const open = links.classList.toggle("is-open"); toggle.setAttribute("aria-expanded", String(open)); });
links?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => { links.classList.remove("is-open"); toggle?.setAttribute("aria-expanded", "false"); }));

if (matchMedia("(hover:hover) and (pointer:fine)").matches) {
  document.querySelectorAll("[data-magnetic]").forEach((button) => {
    button.addEventListener("pointermove", (event) => { const rect=button.getBoundingClientRect(); button.style.transform=`translate(${(event.clientX-rect.left-rect.width/2)*.12}px,${(event.clientY-rect.top-rect.height/2)*.12}px)`; });
    button.addEventListener("pointerleave", () => { button.style.transform=""; });
  });
}

const cleanupMotion = initLandingMotion();
let cleanupThree = () => {};
const hero = document.querySelector("[data-hero-visual]");
if (hero && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
  const load = () => import("../three/hero-signal-core.js").then(({ createSignalCore }) => { cleanupThree = createSignalCore(hero); }).catch(() => hero.classList.add("is-fallback"));
  "requestIdleCallback" in window ? requestIdleCallback(load, { timeout: 1200 }) : setTimeout(load, 250);
}
addEventListener("pagehide", () => { cleanupMotion(); cleanupThree(); }, { once: true });
