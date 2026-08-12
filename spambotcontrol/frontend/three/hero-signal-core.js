import * as THREE from "three";

export function createSignalCore(container) {
  const canvas = container.querySelector("canvas");
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, .1, 100);
  camera.position.set(0, 0, 7.15);
  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, innerWidth < 768 ? 1 : 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const group = new THREE.Group();
  scene.add(group);
  const core = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1.62, 5),
    new THREE.MeshPhysicalMaterial({ color: 0x3f7377, emissive: 0x123e42, emissiveIntensity: 1.12, roughness: .12, metalness: .16, transmission: .3, thickness: 2.1, transparent: true, opacity: .94, clearcoat: .68, clearcoatRoughness: .16 })
  );
  group.add(core);
  const wire = new THREE.Mesh(new THREE.IcosahedronGeometry(1.82, 2), new THREE.MeshBasicMaterial({ color: 0xa5edff, wireframe: true, transparent: true, opacity: .1 }));
  group.add(wire);
  [2.25, 2.86].forEach((radius, index) => {
    const ring = new THREE.Mesh(new THREE.TorusGeometry(radius, .012, 8, 128), new THREE.MeshBasicMaterial({ color: index === 1 ? 0x75e6bd : 0x62d5f3, transparent: true, opacity: .28 - index * .045 }));
    ring.rotation.set(index * .72 + .35, index * .55, index * .42);
    group.add(ring);
  });
  const signals = new THREE.Group();
  for (let index = 0; index < 6; index += 1) {
    const dot = new THREE.Mesh(new THREE.SphereGeometry(.045 + index % 2 * .02, 12, 12), new THREE.MeshBasicMaterial({ color: index % 3 === 0 ? 0x75e6bd : 0xa5edff }));
    dot.userData = { radius: 2.05 + (index % 3) * .55, speed: .16 + index * .014, phase: index * 1.14 };
    signals.add(dot);
  }
  group.add(signals);
  scene.add(new THREE.AmbientLight(0xc8f5ff, 1.55));
  const light = new THREE.PointLight(0xa5edff, 28, 18); light.position.set(3.2, 2.4, 5); scene.add(light);
  const mint = new THREE.PointLight(0x75e6bd, 15, 14); mint.position.set(-3, -2, 3); scene.add(mint);
  const rim = new THREE.DirectionalLight(0xffffff, 2.4); rim.position.set(-1, 3, 4); scene.add(rim);

  let pointerX = 0, pointerY = 0, visible = true, raf = 0;
  const resize = () => { const { width, height } = container.getBoundingClientRect(); renderer.setSize(width, height, false); camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); };
  const pointer = (event) => { const rect = container.getBoundingClientRect(); pointerX = ((event.clientX - rect.left) / rect.width - .5) * .32; pointerY = ((event.clientY - rect.top) / rect.height - .5) * .22; };
  const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; if (visible && !raf) tick(); }, { rootMargin: "120px" });
  observer.observe(container); resize(); addEventListener("resize", resize); container.addEventListener("pointermove", pointer, { passive: true });
  const clock = new THREE.Clock();
  function tick() {
    raf = 0; if (!visible || document.hidden) return;
    const time = clock.getElapsedTime();
    group.rotation.y += (pointerX + time * .055 - group.rotation.y) * .025;
    group.rotation.x += (-pointerY + Math.sin(time * .3) * .08 - group.rotation.x) * .025;
    wire.rotation.y = -time * .08;
    signals.children.forEach((dot) => { const a = time * dot.userData.speed + dot.userData.phase; dot.position.set(Math.cos(a) * dot.userData.radius, Math.sin(a * 1.35) * .8, Math.sin(a) * dot.userData.radius); });
    renderer.render(scene, camera); raf = requestAnimationFrame(tick);
  }
  const visibility = () => { if (!document.hidden && visible && !raf) tick(); };
  document.addEventListener("visibilitychange", visibility); tick(); container.classList.add("is-webgl");
  return () => { cancelAnimationFrame(raf); observer.disconnect(); removeEventListener("resize", resize); container.removeEventListener("pointermove", pointer); document.removeEventListener("visibilitychange", visibility); scene.traverse((object) => { object.geometry?.dispose(); if (Array.isArray(object.material)) object.material.forEach((m) => m.dispose()); else object.material?.dispose(); }); renderer.dispose(); };
}
