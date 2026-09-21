import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

export function initLandingMotion() {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return () => {};

  document.documentElement.classList.add("js-enhanced");
  const lenis = new Lenis({ duration: .95, smoothWheel: true, wheelMultiplier: .82 });
  const raf = (time) => lenis.raf(time * 1000);
  gsap.ticker.add(raf);
  gsap.ticker.lagSmoothing(0);
  lenis.on("scroll", ScrollTrigger.update);

  const context = gsap.context(() => {
    gsap.utils.toArray("[data-reveal]").forEach((heading) => gsap.from(heading, {
      y: 34, autoAlpha: 0, duration: .78, ease: "power3.out",
      scrollTrigger: { trigger: heading, start: "top 88%", once: true },
    }));

    const story = document.querySelector("[data-welcome-story]");
    if (story) {
      const cards = story.querySelectorAll("[data-float-card]");
      const mascot = story.querySelector(".mascot-core");
      const rings = story.querySelectorAll(".orbit-ring");
      const message = story.querySelector("[data-welcome-message]");
      gsap.set([cards, mascot, rings, message], { autoAlpha: 0 });
      gsap.timeline({ defaults: { ease: "power3.out" }, delay: .15 })
        .fromTo(story, { y: 24, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: .55 })
        .to(rings, { autoAlpha: 1, scale: 1, duration: .55, stagger: .08 })
        .fromTo(mascot, { scale: .7 }, { scale: 1, autoAlpha: 1, duration: .48 }, "<+.12")
        .fromTo(cards, { y: 20 }, { y: 0, autoAlpha: 1, duration: .38, stagger: .09 }, "<+.15")
        .fromTo(message, { y: 18 }, { y: 0, autoAlpha: 1, duration: .45 }, ">-.04");
      gsap.to(".core-pulse", { scale: 1.12, opacity: .2, repeat: -1, yoyo: true, duration: 1.6, ease: "sine.inOut" });
      cards.forEach((card, index) => gsap.to(card, { y: index % 2 ? 7 : -7, repeat: -1, yoyo: true, duration: 2.8 + index * .22, ease: "sine.inOut" }));
    }

    gsap.utils.toArray("[data-story-visual]").forEach((visual) => gsap.from(visual, {
      y: 38, autoAlpha: 0, duration: .72, ease: "power3.out",
      scrollTrigger: { trigger: visual, start: "top 86%", once: true },
    }));
    [".launch-grid", ".template-cards", ".lesson-grid"].forEach((selector) => {
      const group = document.querySelector(selector);
      if (!group) return;
      gsap.from(group.children, { y: 28, autoAlpha: 0, stagger: .09, duration: .55, ease: "power2.out", scrollTrigger: { trigger: group, start: "top 88%", once: true } });
    });
  });

  let timer = 0;
  const refresh = () => { clearTimeout(timer); timer = window.setTimeout(() => ScrollTrigger.refresh(), 120); };
  window.addEventListener("resize", refresh, { passive: true });
  window.addEventListener("load", refresh, { once: true });
  return () => { clearTimeout(timer); window.removeEventListener("resize", refresh); context.revert(); lenis.destroy(); gsap.ticker.remove(raf); document.documentElement.classList.remove("js-enhanced"); };
}
