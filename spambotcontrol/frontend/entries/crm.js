import "../styles/crm.css";
import { initCommonUI } from "../ui/common.js";

initCommonUI();
const sidebar = document.querySelector("#sidebar");
const overlay = document.querySelector("#sidebar-overlay");
const sidebarToggle = document.querySelector("#sidebar-toggle");
let sidebarReturnFocus = null;
const getSidebarFocusable = () => [...(sidebar?.querySelectorAll('a[href],button:not([disabled]),select,input:not([type="hidden"])') || [])].filter((item) => item.getClientRects().length);
const openSidebar = () => {
  sidebarReturnFocus = sidebarToggle;
  sidebar?.classList.add("open");
  overlay?.classList.add("open");
  sidebarToggle?.setAttribute("aria-expanded", "true");
  document.body.classList.add("sidebar-is-open");
  getSidebarFocusable()[0]?.focus();
};
const closeSidebar = ({ restoreFocus = true } = {}) => {
  sidebar?.classList.remove("open");
  overlay?.classList.remove("open");
  sidebarToggle?.setAttribute("aria-expanded", "false");
  document.body.classList.remove("sidebar-is-open");
  if (restoreFocus) sidebarReturnFocus?.focus?.();
};
sidebarToggle?.addEventListener("click", openSidebar);
overlay?.addEventListener("click", () => closeSidebar());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && sidebar?.classList.contains("open")) closeSidebar();
  if (event.key !== "Tab" || !sidebar?.classList.contains("open") || innerWidth >= 768) return;
  const focusable = getSidebarFocusable();
  if (!focusable.length) return;
  const first = focusable[0], last = focusable.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
matchMedia("(min-width: 768px)").addEventListener("change", (event) => { if (event.matches) closeSidebar({ restoreFocus: false }); });

const wsTrigger=document.querySelector("#ws-trigger"), wsDropdown=document.querySelector("#ws-dropdown");
wsTrigger?.addEventListener("click",()=>{const open=wsDropdown?.classList.toggle("open");wsTrigger.setAttribute("aria-expanded",String(Boolean(open)))});
document.addEventListener("click",(event)=>{if(wsDropdown&&!wsDropdown.contains(event.target)&&!wsTrigger?.contains(event.target)){wsDropdown.classList.remove("open");wsTrigger?.setAttribute("aria-expanded","false")}});

const progress=document.querySelector("#page-progress");
const syncProgress=()=>{if(!progress)return;const max=document.documentElement.scrollHeight-innerHeight;progress.style.width=max>0?`${Math.min(scrollY/max*100,100)}%`:"0%"};
syncProgress();addEventListener("scroll",syncProgress,{passive:true});
document.querySelectorAll("tr.clickable-row[data-href]").forEach((row)=>row.addEventListener("click",(event)=>{if(!event.target.closest("a,button,input,select,form"))location.href=row.dataset.href}));
document.querySelectorAll("table").forEach((table) => {
  const labels = [...table.querySelectorAll("thead th")].map((cell) => cell.textContent.trim());
  table.querySelectorAll("tbody tr").forEach((row) => [...row.children].forEach((cell, index) => {
    if (!cell.dataset.label && labels[index]) cell.dataset.label = labels[index];
  }));
});
addEventListener("pageshow",()=>document.querySelectorAll("[aria-busy='true']").forEach((el)=>el.removeAttribute("aria-busy")));

const galleryDialog = document.querySelector("[data-report-gallery]");
const galleryItems = [...document.querySelectorAll("[data-report-gallery-item]")];
if (galleryDialog && galleryItems.length) {
  const image = galleryDialog.querySelector("[data-gallery-image]");
  const name = galleryDialog.querySelector("[data-gallery-name]");
  const download = galleryDialog.querySelector("[data-gallery-download]");
  let activeIndex = 0;
  const renderGalleryItem = (index) => {
    activeIndex = (index + galleryItems.length) % galleryItems.length;
    const item = galleryItems[activeIndex];
    image.src = item.dataset.src;
    image.alt = item.dataset.name || "Изображение отчёта";
    name.textContent = item.dataset.name || "Изображение";
    download.href = item.dataset.download;
  };
  galleryItems.forEach((item, index) => item.addEventListener("click", () => {
    renderGalleryItem(index);
    galleryDialog.showModal();
  }));
  galleryDialog.querySelector("[data-gallery-prev]")?.addEventListener("click", () => renderGalleryItem(activeIndex - 1));
  galleryDialog.querySelector("[data-gallery-next]")?.addEventListener("click", () => renderGalleryItem(activeIndex + 1));
  galleryDialog.querySelector("[data-gallery-close]")?.addEventListener("click", () => galleryDialog.close());
  galleryDialog.addEventListener("click", (event) => { if (event.target === galleryDialog) galleryDialog.close(); });
  galleryDialog.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") renderGalleryItem(activeIndex - 1);
    if (event.key === "ArrowRight") renderGalleryItem(activeIndex + 1);
  });
  galleryDialog.addEventListener("close", () => { image.removeAttribute("src"); });
}
