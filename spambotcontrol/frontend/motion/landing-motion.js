import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

export function initLandingMotion() {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return () => {};
  const lenis = new Lenis({ duration: 1.05, smoothWheel: true, wheelMultiplier: .85 });
  const raf = (time) => lenis.raf(time * 1000);
  gsap.ticker.add(raf); gsap.ticker.lagSmoothing(0); lenis.on("scroll", ScrollTrigger.update);
  const context = gsap.context(() => {
    gsap.utils.toArray("[data-reveal]").forEach((block) => gsap.from(block.children, { yPercent: 108, duration: .9, ease: "power4.out", stagger: .08, scrollTrigger: { trigger: block, start: "top 88%", once: true } }));
    gsap.utils.toArray("[data-story-visual]").forEach((visual) => gsap.from(visual, { y: 35, rotateX: 3, opacity: .35, duration: .9, ease: "power3.out", scrollTrigger: { trigger: visual, start: "top 80%", once: true } }));
    const flow = document.querySelector("[data-welcome-flow]");
    if (flow && innerWidth >= 768) {
      const steps = [...flow.querySelectorAll("[data-flow-step]")];
      const nodes = [...flow.querySelectorAll("[data-flow-node]")];
      const fill = flow.querySelector("[data-flow-fill]");
      const setStage = (index) => { steps.forEach((el,i)=>el.classList.toggle("is-active",i===index)); nodes.forEach((el,i)=>{el.classList.toggle("is-active",i===index);el.classList.toggle("is-past",i<index)}); };
      setStage(0);
      ScrollTrigger.create({ trigger: flow, start: "top top+=90", end: "+=1250", pin: flow.querySelector(".welcome-flow__layout"), scrub: .28, onUpdate: (self) => { const index = Math.min(5, Math.floor(self.progress * 6)); setStage(index); gsap.set(fill,{height:`${self.progress*100}%`}); } });
    }
  });
  return () => { context.revert(); lenis.destroy(); gsap.ticker.remove(raf); ScrollTrigger.getAll().forEach((trigger) => trigger.kill()); };
}
