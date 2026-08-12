import "../styles/crm.css";
import { initCommonUI } from "../ui/common.js";

initCommonUI();
const sidebar = document.querySelector("#sidebar");
const overlay = document.querySelector("#sidebar-overlay");
const openSidebar = () => { sidebar?.classList.add("open"); overlay?.classList.add("open"); document.body.style.overflow="hidden"; };
const closeSidebar = () => { sidebar?.classList.remove("open"); overlay?.classList.remove("open"); document.body.style.overflow=""; };
document.querySelector("#sidebar-toggle")?.addEventListener("click", openSidebar);
overlay?.addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => { if(event.key === "Escape") closeSidebar(); });

const wsTrigger=document.querySelector("#ws-trigger"), wsDropdown=document.querySelector("#ws-dropdown");
wsTrigger?.addEventListener("click",()=>{const open=wsDropdown?.classList.toggle("open");wsTrigger.setAttribute("aria-expanded",String(Boolean(open)))});
document.addEventListener("click",(event)=>{if(wsDropdown&&!wsDropdown.contains(event.target)&&!wsTrigger?.contains(event.target)){wsDropdown.classList.remove("open");wsTrigger?.setAttribute("aria-expanded","false")}});

const progress=document.querySelector("#page-progress");
const syncProgress=()=>{if(!progress)return;const max=document.documentElement.scrollHeight-innerHeight;progress.style.width=max>0?`${Math.min(scrollY/max*100,100)}%`:"0%"};
syncProgress();addEventListener("scroll",syncProgress,{passive:true});
document.querySelectorAll("tr.clickable-row[data-href]").forEach((row)=>row.addEventListener("click",(event)=>{if(!event.target.closest("a,button,input,select,form"))location.href=row.dataset.href}));
addEventListener("pageshow",()=>document.querySelectorAll("[aria-busy='true']").forEach((el)=>el.removeAttribute("aria-busy")));
