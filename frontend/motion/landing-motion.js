import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

export function initLandingMotion() {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return () => {};

  document.documentElement.classList.add("js-enhanced");
  const lenis = new Lenis({ duration: 1, smoothWheel: true, wheelMultiplier: .82 });
  const raf = (time) => lenis.raf(time * 1000);
  gsap.ticker.add(raf);
  gsap.ticker.lagSmoothing(0);
  lenis.on("scroll", ScrollTrigger.update);

  const context = gsap.context(() => {
    gsap.utils.toArray("[data-reveal]").forEach((heading) => {
      gsap.from(heading, {
        y: 34,
        autoAlpha: 0,
        duration: .78,
        ease: "power3.out",
        scrollTrigger: { trigger: heading, start: "top 88%", once: true },
      });
    });

    const story = document.querySelector("[data-welcome-story]");
    if (story) {
      const event = story.querySelector("[data-welcome-event]");
      const signal = story.querySelector("[data-welcome-signal]");
      const pulse = story.querySelector("[data-welcome-pulse]");
      const message = story.querySelector("[data-welcome-message]");
      const name = story.querySelector("[data-welcome-name]");
      const delivered = story.querySelector("[data-welcome-delivered]");
      gsap.set([event, message, delivered], { autoAlpha: 0 });
      gsap.set(signal, { scaleY: 0, transformOrigin: "top" });
      gsap.set(pulse, { y: -18, autoAlpha: 0 });
      gsap.timeline({ defaults: { ease: "power3.out" }, delay: .2 })
        .fromTo(story, { y: 28, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: .68 })
        .to(event, { autoAlpha: 1, duration: .35 })
        .to(signal, { scaleY: 1, duration: .42 }, ">+.08")
        .to(pulse, { y: 20, autoAlpha: 1, duration: .42 }, "<")
        .fromTo(message, { y: 22, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: .48 }, ">-.08")
        .fromTo(name, { backgroundColor: "transparent" }, { backgroundColor: "rgba(221,244,91,.18)", duration: .3 }, "<+.12")
        .to(delivered, { autoAlpha: 1, duration: .35 }, ">+.12");
    }

    gsap.utils.toArray("[data-story-visual]").forEach((visual) => {
      gsap.from(visual, {
        y: 36,
        autoAlpha: 0,
        duration: .72,
        ease: "power3.out",
        scrollTrigger: { trigger: visual, start: "top 86%", once: true },
      });
    });

    [".step-grid", ".template-grid", ".lesson-grid"].forEach((selector) => {
      const group = document.querySelector(selector);
      if (!group) return;
      gsap.from(group.children, {
        y: 24,
        autoAlpha: 0,
        stagger: .08,
        duration: .55,
        ease: "power2.out",
        scrollTrigger: { trigger: group, start: "top 88%", once: true },
      });
    });
  });

  let refreshTimer = 0;
  const refresh = () => {
    clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(() => ScrollTrigger.refresh(), 120);
  };
  window.addEventListener("resize", refresh, { passive: true });
  window.addEventListener("load", refresh, { once: true });
  return () => {
    clearTimeout(refreshTimer);
    window.removeEventListener("resize", refresh);
    context.revert();
    lenis.destroy();
    gsap.ticker.remove(raf);
    document.documentElement.classList.remove("js-enhanced");
  };
}
