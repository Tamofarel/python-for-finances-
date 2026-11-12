// Three.js holographic core. Loaded lazily, only when WebGL is available.
// Visual changes are driven by `setState` (real assistant state), `setLevelSource` (real microphone level) and
// `pulse` (real speech-synthesis word boundaries). Nothing here simulates activity.

import * as THREE from "three";
import type { AssistantState } from "../assistant/machine";

type Hue = "signal" | "aura" | "info" | "warn" | "danger" | "muted";
export type CoreTheme = "dark" | "light";

// Colours per theme. On the light theme the core is drawn with normal blending and deeper inks, because additive
// glow disappears on a light surface.
export const PALETTES: Record<CoreTheme, Record<Hue, string>> = {
  dark: { signal: "#6fd3ea", aura: "#a59ff7", info: "#6aa9f5", warn: "#f2c46b", danger: "#f28b8b", muted: "#56627d" },
  light: { signal: "#0b7d98", aura: "#5b50c9", info: "#2d6fc0", warn: "#a86b00", danger: "#b4362a", muted: "#9aa3b3" },
};

interface Profile {
  a: Hue;
  b: Hue;
  spin: number; // ring rotation speed (rad/s)
  arc: number; // arc sweep speed
  energy: number; // glow intensity
  drift: number; // particle drift speed
  scan: number; // 0..1 visibility of the scanning sweep (research)
  lattice: number; // 0..1 visibility of the design lattice
}

const P = (a: Hue, b: Hue, spin: number, arc: number, energy: number, drift: number, scan = 0, lattice = 0): Profile =>
  ({ a, b, spin, arc, energy, drift, scan, lattice });

// Calm by default: motion only rises when NOVA is actually doing something.
export const PROFILES: Record<AssistantState, Profile> = {
  standby: P("signal", "aura", 0.03, 0.05, 0.28, 0.008),
  wake_detected: P("signal", "signal", 0.08, 0.3, 0.75, 0.02),
  listening: P("signal", "signal", 0.07, 0.2, 0.62, 0.016),
  transcribing: P("signal", "aura", 0.12, 0.7, 0.55, 0.025),
  understanding: P("aura", "signal", 0.16, 0.9, 0.55, 0.03),
  thinking: P("aura", "signal", 0.24, 1.3, 0.55, 0.04),
  responding: P("aura", "signal", 0.18, 0.9, 0.6, 0.03),
  speaking: P("aura", "aura", 0.12, 0.45, 0.65, 0.025),
  researching: P("info", "signal", 0.14, 0.4, 0.5, 0.06, 1, 0),
  designing: P("aura", "signal", 0.12, 0.35, 0.5, 0.03, 0, 1),
  awaiting_user: P("signal", "aura", 0.03, 0.08, 0.42, 0.008),
  awaiting_approval: P("warn", "signal", 0.03, 0.08, 0.45, 0.006),
  executing: P("info", "warn", 0.22, 1.0, 0.55, 0.04),
  interrupted: P("muted", "muted", 0, 0, 0.18, 0),
  cancelled: P("muted", "muted", 0, 0, 0.18, 0),
  error: P("danger", "muted", 0.015, 0, 0.32, 0.003),
};

function circle(radius: number, segments = 160): THREE.BufferGeometry {
  const pts = new THREE.EllipseCurve(0, 0, radius, radius, 0, Math.PI * 2).getPoints(segments);
  return new THREE.BufferGeometry().setFromPoints(pts.map((p) => new THREE.Vector3(p.x, p.y, 0)));
}

function ticks(radius: number, count: number, majorEvery: number, minor: number, major: number): THREE.BufferGeometry {
  const pos: number[] = [];
  for (let i = 0; i < count; i++) {
    const t = (i / count) * Math.PI * 2;
    const len = i % majorEvery === 0 ? major : minor;
    const c = Math.cos(t);
    const s = Math.sin(t);
    pos.push(c * radius, s * radius, 0, c * (radius + len), s * (radius + len), 0);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  return g;
}

function glowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,0.9)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.35)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export class CoreScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  private clock = new THREE.Clock();
  private frame = 0;
  private disposed = false;
  private theme: CoreTheme;
  private target: Profile = PROFILES.standby;
  private cur: { spin: number; arc: number; energy: number; drift: number; scan: number; lattice: number; ca: THREE.Color; cb: THREE.Color };
  private additive: THREE.Material[] = [];
  private level = 0;
  private levelSource: (() => number) | null = null;
  private pulseEnergy = 0;
  private tmpA = new THREE.Color();
  private tmpB = new THREE.Color();

  private core: THREE.LineSegments;
  private glow: THREE.Sprite;
  private rings: THREE.Object3D[] = [];
  private ringMats: THREE.LineBasicMaterial[] = [];
  private arcs: THREE.Mesh[] = [];
  private arcMat: THREE.MeshBasicMaterial;
  private scan: THREE.Mesh;
  private lattice: THREE.LineSegments;
  private particles: THREE.Points;
  private faceRing: THREE.Group;

  constructor(canvas: HTMLCanvasElement, private opts: { reducedMotion: boolean; lowPower: boolean; theme: CoreTheme }) {
    this.theme = opts.theme;
    const pal = PALETTES[this.theme];
    const { a, b, ...rest } = PROFILES.standby;
    this.cur = { ...rest, ca: new THREE.Color(pal[a]), cb: new THREE.Color(pal[b]) };
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: !opts.lowPower, alpha: true, powerPreference: "low-power" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, opts.lowPower ? 1.25 : 1.75));
    this.renderer.setClearColor(0x000000, 0);
    this.camera.position.set(0, 0, 8.2);

    const add = (o: THREE.Object3D) => this.scene.add(o);

    // Core: faceted wire icosahedron
    const coreMat = new THREE.LineBasicMaterial({ color: this.cur.ca, transparent: true, opacity: 0.55 });
    this.core = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.IcosahedronGeometry(0.78, 1)), coreMat);
    add(this.core);

    this.glow = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture(), color: this.cur.ca, transparent: true,
      depthWrite: false, opacity: 0.6 }));
    this.additive.push(this.glow.material);
    this.glow.scale.setScalar(2.4);
    add(this.glow);

    // Face-on ring with fine technical markings (minor ticks every 5°, major every 30°)
    this.faceRing = new THREE.Group();
    const faceMat = new THREE.LineBasicMaterial({ color: this.cur.ca, transparent: true, opacity: 0.7 });
    this.ringMats.push(faceMat);
    this.faceRing.add(new THREE.LineLoop(circle(1.32), faceMat));
    this.faceRing.add(new THREE.LineSegments(ticks(1.36, 72, 6, 0.05, 0.13), faceMat));
    add(this.faceRing);
    this.rings.push(this.faceRing);

    // Tilted orbit rings
    const tilted: [number, number, number, number][] = [[1.75, 1.15, 0.2, 0.45], [2.15, -0.5, 1.05, 0.35], [2.6, 0.25, -0.6, 0.22]];
    for (const [r, rx, ry, op] of tilted) {
      const m = new THREE.LineBasicMaterial({ color: this.cur.cb, transparent: true, opacity: op });
      this.ringMats.push(m);
      const ring = new THREE.LineLoop(circle(r), m);
      ring.rotation.set(rx, ry, 0);
      add(ring);
      this.rings.push(ring);
    }
    const outerTicks = new THREE.LineSegments(ticks(2.95, 120, 10, 0.03, 0.09),
      new THREE.LineBasicMaterial({ color: this.cur.cb, transparent: true, opacity: 0.25 }));
    this.ringMats.push(outerTicks.material as THREE.LineBasicMaterial);
    add(outerTicks);
    this.rings.push(outerTicks);

    // Arcs that sweep while NOVA works
    this.arcMat = new THREE.MeshBasicMaterial({ color: this.cur.cb, transparent: true, opacity: 0.8, side: THREE.DoubleSide,
      depthWrite: false });
    this.additive.push(this.arcMat);
    for (const [inner, len] of [[1.48, 1.1], [1.48, 0.45]] as const) {
      const arc = new THREE.Mesh(new THREE.RingGeometry(inner, inner + 0.045, 48, 1, 0, len), this.arcMat);
      this.arcs.push(arc);
      add(arc);
    }
    this.arcs[1]!.rotation.z = Math.PI;

    // Research scan sweep
    this.scan = new THREE.Mesh(new THREE.RingGeometry(0.2, 2.9, 64, 1, 0, 0.35),
      new THREE.MeshBasicMaterial({ color: pal.info, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false }));
    this.additive.push(this.scan.material as THREE.Material);
    add(this.scan);

    // Design lattice
    this.lattice = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.OctahedronGeometry(1.15, 0)),
      new THREE.LineBasicMaterial({ color: this.cur.cb, transparent: true, opacity: 0 }));
    add(this.lattice);

    // Particle field
    const n = opts.lowPower ? 160 : 360;
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 2.7 + Math.random() * 1.3;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.sin(ph) * Math.sin(th) * 0.55, r * Math.cos(ph) * 0.6], i * 3);
    }
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    this.particles = new THREE.Points(pg, new THREE.PointsMaterial({ color: this.cur.cb, size: 0.026, transparent: true,
      opacity: 0.4, depthWrite: false }));
    this.additive.push(this.particles.material as THREE.Material);
    add(this.particles);

    this.applyBlending();
    if (opts.reducedMotion) this.snap();
    else this.loop();
  }

  /** Switch between the dark (additive glow) and light (normal blending, deeper inks) renderings. */
  setTheme(theme: CoreTheme): void {
    if (theme === this.theme) return;
    this.theme = theme;
    (this.scan.material as THREE.MeshBasicMaterial).color.set(PALETTES[theme].info);
    this.applyBlending();
    this.snap();
  }

  /** Follow the owner's motion preference at runtime: reduced motion renders still frames on state changes only. */
  setReducedMotion(reduce: boolean): void {
    if (reduce === this.opts.reducedMotion) return;
    this.opts = { ...this.opts, reducedMotion: reduce };
    cancelAnimationFrame(this.frame);
    if (reduce) this.snap();
    else {
      this.clock.getDelta();
      this.loop();
    }
  }

  private applyBlending(): void {
    const blending = this.theme === "dark" ? THREE.AdditiveBlending : THREE.NormalBlending;
    for (const m of this.additive) {
      m.blending = blending;
      m.needsUpdate = true;
    }
  }

  /** Jump to the target profile without animation (reduced motion, theme change). */
  private snap(): void {
    const pal = PALETTES[this.theme];
    const { a, b, ...rest } = this.target;
    Object.assign(this.cur, rest);
    this.cur.ca.set(pal[a]);
    this.cur.cb.set(pal[b]);
    this.apply(0);
    this.render();
  }

  setState(state: AssistantState): void {
    this.target = PROFILES[state];
    if (this.opts.reducedMotion) this.snap();
  }

  setLevelSource(src: (() => number) | null): void {
    this.levelSource = src;
  }

  pulse(): void {
    this.pulseEnergy = Math.min(1, this.pulseEnergy + 0.45);
  }

  resize(w: number, h: number): void {
    if (w === 0 || h === 0) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.position.z = w / h < 0.9 ? 10.5 : 8.2;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose();
      const mat = m.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
      else mat?.dispose();
    });
    this.renderer.dispose();
  }

  private loop = (): void => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.loop);
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const k = 1 - Math.exp(-dt * 3.5);
    const c = this.cur;
    for (const key of ["spin", "arc", "energy", "drift", "scan", "lattice"] as const) c[key] += (this.target[key] - c[key]) * k;
    const pal = PALETTES[this.theme];
    c.ca.lerp(this.tmpA.set(pal[this.target.a]), k);
    c.cb.lerp(this.tmpB.set(pal[this.target.b]), k);
    const raw = this.levelSource ? this.levelSource() : 0;
    this.level += (raw - this.level) * Math.min(1, dt * 14);
    this.pulseEnergy *= Math.exp(-dt * 5);
    this.apply(dt);
    this.render();
  };

  private apply(dt: number): void {
    const c = this.cur;
    const react = Math.max(this.level, this.pulseEnergy);
    this.core.rotation.y += dt * (0.15 + c.spin * 0.8);
    this.core.rotation.x += dt * c.spin * 0.3;
    (this.core.material as THREE.LineBasicMaterial).color.copy(c.ca);
    this.core.scale.setScalar(1 + react * 0.12);
    const gm = this.glow.material as THREE.SpriteMaterial;
    gm.color.copy(c.ca);
    const light = this.theme === "light";
    gm.opacity = (light ? 0.1 : 0.22) + c.energy * (light ? 0.22 : 0.45) + react * 0.22;
    this.glow.scale.setScalar(2.1 + c.energy * 0.8 + react * 1.3);
    this.faceRing.rotation.z -= dt * c.spin * 0.4;
    this.faceRing.scale.setScalar(1 + this.level * 0.18);
    this.rings.forEach((r, i) => {
      if (i > 0) r.rotation.z += dt * c.spin * (i % 2 ? 1 : -0.7);
    });
    this.ringMats.forEach((m, i) => m.color.copy(i === 0 ? c.ca : c.cb));
    this.arcs.forEach((a, i) => (a.rotation.z += dt * c.arc * (i ? -1.6 : 1)));
    this.arcMat.color.copy(c.cb);
    this.arcMat.opacity = Math.min(0.85, 0.12 + c.arc * 0.45);
    const sm = this.scan.material as THREE.MeshBasicMaterial;
    sm.opacity = c.scan * 0.22;
    this.scan.rotation.z -= dt * 1.8 * c.scan;
    const lm = this.lattice.material as THREE.LineBasicMaterial;
    lm.opacity = c.lattice * 0.5;
    lm.color.copy(c.cb);
    this.lattice.rotation.y -= dt * 0.4 * c.lattice;
    this.lattice.rotation.x += dt * 0.25 * c.lattice;
    this.particles.rotation.y += dt * c.drift;
    (this.particles.material as THREE.PointsMaterial).color.copy(c.cb);
  }

  private render(): void {
    if (!this.disposed) this.renderer.render(this.scene, this.camera);
  }
}
