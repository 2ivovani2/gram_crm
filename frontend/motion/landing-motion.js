import Lenis from "lenis";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

const getHeaderHeight = () =>
  document.querySelector(".landing-header")?.getBoundingClientRect().height ?? 72;

export function initLandingMotion() {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduced) return () => {};

  document.documentElement.classList.add("js-enhanced");
  const lenis = new Lenis({ duration: 1.05, smoothWheel: true, wheelMultiplier: .85 });
  const raf = (time) => lenis.raf(time * 1000);
  gsap.ticker.add(raf);
  gsap.ticker.lagSmoothing(0);
  lenis.on("scroll", ScrollTrigger.update);

  const media = gsap.matchMedia();
  const context = gsap.context(() => {
    document.querySelectorAll("[data-reveal]").forEach((heading) => {
      gsap.from(heading.children, {
        yPercent: 108,
        duration: .85,
        ease: "power4.out",
        stagger: .08,
        scrollTrigger: { trigger: heading, start: "top 88%", once: true },
      });
    });

    const hero = document.querySelector("[data-hero-product]");
    if (hero) {
      const event = hero.querySelector('[data-hero-step="event"]');
      const engine = hero.querySelector('[data-hero-step="engine"]');
      const message = hero.querySelector('[data-hero-step="message"]');
      const crm = hero.querySelector('[data-hero-step="crm"]');
      const signal = hero.querySelector("[data-hero-signal]");
      const route = hero.querySelector("[data-hero-route]");
      const engineItems = hero.querySelectorAll("[data-engine-item]");
      const delivery = hero.querySelector("[data-delivery-state]");
      const replay = hero.querySelector("[data-hero-replay]");
      const timeline = gsap.timeline({ paused: true, defaults: { ease: "power3.out" } });

      gsap.set([event, engine, message, crm], { autoAlpha: 0 });
      gsap.set(engineItems, { opacity: .22, x: -8 });
      gsap.set(route, { scaleX: 0 });
      gsap.set(hero.querySelector(".hero-product__result-line"), { scaleX: 0 });
      delivery.textContent = "Processing";
      delivery.classList.remove("is-delivered");

      timeline
        .to(event, { autoAlpha: 1, duration: .48 })
        .to(engine, { autoAlpha: 1, duration: .42 }, ">+.18")
        .to(route, { scaleX: 1, duration: .54 }, "<")
        .fromTo(signal, { xPercent: -1200 }, { xPercent: 0, duration: .54 }, "<")
        .to(engineItems, { opacity: 1, x: 0, stagger: .22, duration: .28 })
        .to(message, { autoAlpha: 1, duration: .46 }, ">+.08")
        .call(() => { delivery.textContent = "Delivered"; delivery.classList.add("is-delivered"); })
        .to(crm, { autoAlpha: 1, duration: .4 }, ">+.32")
        .to(hero.querySelector(".hero-product__result-line"), { scaleX: 1, duration: .5 }, "<");

      timeline.play(0);
      replay?.addEventListener("click", () => {
        delivery.textContent = "Processing";
        delivery.classList.remove("is-delivered");
        timeline.restart();
      });
    }

    const sequence = document.querySelector(".flow-sequence");
    const pin = sequence?.querySelector("[data-flow-pin]");
    if (sequence && pin) media.add("(min-width: 1024px) and (min-height: 650px)", () => {
      sequence.classList.add("is-enhanced");
      const panels = [...pin.querySelectorAll("[data-flow-panel]")];
      const steps = [...pin.querySelectorAll("[data-flow-step]")];
      const current = pin.querySelector("[data-flow-current]");
      const status = pin.querySelector("[data-flow-status]");
      const progress = pin.querySelector("[data-flow-progress]");
      const statuses = ["Event received", "Trigger detected", "Access granted", "Personalized", "Delivered", "CRM synced"];
      let activeIndex = -1;

      const setStage = (index) => {
        if (index === activeIndex) return;
        activeIndex = index;
        panels.forEach((panel, panelIndex) => panel.classList.toggle("is-active", panelIndex === index));
        steps.forEach((step, stepIndex) => step.classList.toggle("is-active", stepIndex === index));
        if (current) current.textContent = String(index + 1).padStart(2, "0");
        if (status) status.textContent = statuses[index];
      };

      setStage(0);
      const trigger = ScrollTrigger.create({
        trigger: pin,
        pin,
        start: () => `top top+=${getHeaderHeight()}`,
        end: () => `+=${Math.max(innerHeight * 3.6, 1800)}`,
        scrub: .25,
        anticipatePin: 1,
        invalidateOnRefresh: true,
        onUpdate: ({ progress: value }) => {
          setStage(Math.min(panels.length - 1, Math.floor(value * panels.length)));
          gsap.set(progress, { scaleX: value });
        },
      });
      return () => {
        trigger.kill();
        sequence.classList.remove("is-enhanced");
        panels.forEach((panel) => panel.classList.remove("is-active"));
        steps.forEach((step) => step.classList.remove("is-active"));
      };
    });
  });

  let refreshTimer = 0;
  const refresh = () => {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => ScrollTrigger.refresh(), 120);
  };
  const header = document.querySelector(".landing-header");
  const headerObserver = header && "ResizeObserver" in window ? new ResizeObserver(refresh) : null;
  if (header) headerObserver?.observe(header);
  addEventListener("resize", refresh, { passive: true });
  addEventListener("orientationchange", refresh, { passive: true });
  document.fonts?.ready.then(refresh);

  return () => {
    clearTimeout(refreshTimer);
    headerObserver?.disconnect();
    removeEventListener("resize", refresh);
    removeEventListener("orientationchange", refresh);
    media.revert();
    context.revert();
    lenis.destroy();
    gsap.ticker.remove(raf);
  };
}
