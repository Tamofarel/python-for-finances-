// Holographic Core 2.0: the energy core and the humanoid avatar are two forms of the same intelligence.
// This class composes the layers and eases every visual value towards the profile of the real assistant state.
// Inputs are real: `setState` (assistant state machine), `setLevelSource` (microphone level) and `pulse`
// (speech-synthesis word boundaries). Nothing here simulates activity.
// The public surface matches the 0.3 `CoreScene`, so the React wrapper can swap one for the other.

import * as THREE from "three";
import type { AssistantState } from "../../assistant/machine";
import { Backdrop } from "./Backdrop";
import { EnergyParticles } from "./EnergyParticles";
import { EnergySphere } from "./EnergySphere";
import { HolographicAvatar } from "./HolographicAvatar";
import { OrbitalRings } from "./OrbitalRings";
import { PortraitAvatar, type PortraitSpec } from "./PortraitAvatar";
import { CHEST_Y, MAX_DPR, PALETTES, PARTICLES, PROFILES } from "./holographic.constants";
import type { Frame, HoloTheme, VisualProfile } from "./holographic.types";

export interface SceneOptions {
  reducedMotion: boolean;
  lowPower: boolean;
  theme: HoloTheme;
  backdrop?: boolean; // draw the nebula environment (the canvas is then opaque)
  portrait?: PortraitSpec; // the avatar's portrait form; the line-art figure is used until (unless) it loads
}

export interface SceneStats {
  fps: number;
  frameMs: number; // average time between frames
  worstMs: number; // slowest frame of the last sample window
  drawCalls: number;
  particles: number;
  pixelRatio: number;
  degraded: boolean; // the scene lowered its own resolution because frames were slow
}

const FOV = 42;
const STILL_TIME = 2.4; // the moment rendered when motion is reduced

export class HolographicScene {
  private renderer: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100);
  private lastTick = 0;
  private raf = 0;
  private disposed = false;
  private opts: SceneOptions;
  private target: VisualProfile = PROFILES.standby;
  private frame: Frame;
  private levelSource: (() => number) | null = null;
  private pulseEnergy = 0;
  private tmp = new THREE.Color();
  private size = { w: 0, h: 0 };
  private focus: { left: number; top: number; width: number; height: number } | null = null;
  private pixelRatio: number;
  private degraded = false;
  private sample = { frames: 0, total: 0, worst: 0, slow: 0 };
  private last: SceneStats;

  private coreGroup = new THREE.Group();
  private sphere = new EnergySphere();
  private rings: OrbitalRings;
  private particles: EnergyParticles;
  private avatar: HolographicAvatar;
  private backdrop: Backdrop | null;
  private portrait: PortraitAvatar | null = null;
  private coreAnchor = new THREE.Vector2(0, CHEST_Y);
  private coreSettledScale = 0.46;

  constructor(canvas: HTMLCanvasElement, opts: SceneOptions) {
    this.opts = opts;
    const pal = PALETTES[opts.theme];
    const p = this.target;
    this.frame = {
      time: STILL_TIME, spinPhase: 0, flowPhase: 0, energy: p.energy, expand: p.expand, morph: p.morph, flow: p.flow, scan: p.scan,
      level: 0, react: 0, voice: 0, a: new THREE.Color(pal[p.a]), b: new THREE.Color(pal[p.b]), hi: new THREE.Color(pal.hi), light: opts.theme === "light",
    };
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: !opts.lowPower, alpha: true, powerPreference: "low-power" });
    this.pixelRatio = Math.min(window.devicePixelRatio || 1, opts.lowPower ? MAX_DPR.low : MAX_DPR.full);
    this.renderer.setPixelRatio(this.pixelRatio);
    this.renderer.setClearColor(0x000000, 0);
    this.camera.position.set(0, 0, 8.2);

    this.rings = new OrbitalRings(opts.lowPower);
    this.particles = new EnergyParticles(opts.lowPower ? PARTICLES.low : PARTICLES.full);
    this.avatar = new HolographicAvatar(opts.lowPower);
    this.backdrop = opts.backdrop ? new Backdrop() : null;
    this.coreGroup.add(this.sphere.object, this.rings.object);
    if (this.backdrop) this.scene.add(this.backdrop.object);
    if (opts.portrait) {
      const portrait = new PortraitAvatar(opts.portrait, (targets) => {
        if (this.disposed) return;
        this.avatar.enabled = false;
        this.coreAnchor.copy(portrait.core);
        this.coreSettledScale = 0.36;
        this.particles.setTargets(targets(this.particles.count), 0.92);
        this.applyBlending();
        this.draw();
      }, () => this.scene.remove(portrait.object)); // keep the line-art figure if the image cannot load
      this.portrait = portrait;
      this.scene.add(portrait.object);
    }
    this.scene.add(this.particles.object, this.avatar.object, this.coreGroup);
    this.last = { fps: 0, frameMs: 0, worstMs: 0, drawCalls: 0, particles: this.particles.count, pixelRatio: this.pixelRatio, degraded: false };

    this.applyBlending();
    if (opts.reducedMotion) this.snap();
    else this.loop();
  }

  setState(state: AssistantState): void {
    this.target = PROFILES[state];
    if (this.opts.reducedMotion) this.snap();
  }

  /** Dark: additive light. Light: normal blending with deeper inks (additive glow disappears on white). */
  setTheme(theme: HoloTheme): void {
    if (theme === this.opts.theme) return;
    this.opts = { ...this.opts, theme };
    this.frame.light = theme === "light";
    this.applyBlending();
    this.snapColours();
    this.draw();
  }

  /** Reduced motion renders one still frame per state change; the avatar still forms, without the animation. */
  setReducedMotion(reduce: boolean): void {
    if (reduce === this.opts.reducedMotion) return;
    this.opts = { ...this.opts, reducedMotion: reduce };
    cancelAnimationFrame(this.raf);
    if (reduce) this.snap();
    else {
      this.lastTick = 0;
      this.loop();
    }
  }

  setLevelSource(src: (() => number) | null): void {
    this.levelSource = src;
  }

  /** One real word boundary of speech output. */
  pulse(): void {
    this.pulseEnergy = Math.min(1, this.pulseEnergy + 0.45);
  }

  resize(w: number, h: number): void {
    if (w === 0 || h === 0) return;
    this.size = { w, h };
    this.renderer.setSize(w, h, false);
    this.frameCamera();
    this.backdrop?.setAspect(w / h);
    this.particles.setViewport(h * this.pixelRatio, FOV);
    this.draw();
  }

  /** The part of the canvas (in CSS pixels) the hologram should fill and centre on. The rest of the canvas still
   *  shows the environment, so the hologram can sit above the captions while the background covers the page. */
  setFocus(left: number, top: number, width: number, height: number): void {
    this.focus = { left, top, width, height };
    this.frameCamera();
    this.draw();
  }

  private frameCamera(): void {
    const { w, h } = this.size;
    if (!w || !h) return;
    const f = this.focus ?? { left: 0, top: 0, width: w, height: h };
    const fh = THREE.MathUtils.clamp(f.height, 120, h), fw = THREE.MathUtils.clamp(f.width, 160, w);
    const t = Math.tan((FOV * Math.PI) / 360);
    // At distance z one CSS pixel spans 2·z·tan(fov/2)/h scene units, horizontally and vertically.
    // 6.4 units (the full figure) must fit the focus height and 4.2 units (the core's loops) its width.
    this.camera.aspect = w / h;
    this.camera.position.z = Math.max((6.4 * h) / (2 * t * fh), (4.2 * h) / (2 * t * fw));
    this.camera.setViewOffset(w, h, w / 2 - (f.left + fw / 2), h / 2 - (f.top + fh / 2), w, h);
    this.camera.updateProjectionMatrix();
  }

  stats(): SceneStats {
    return this.last;
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    const textures = new Set<THREE.Texture>();
    this.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      m.geometry?.dispose();
      for (const mat of ([] as THREE.Material[]).concat(m.material ?? [])) {
        const map = (mat as THREE.SpriteMaterial).map;
        if (map) textures.add(map);
        mat.dispose();
      }
    });
    textures.forEach((t) => t.dispose());
    this.renderer.dispose();
  }

  private applyBlending(): void {
    const blending = this.frame.light ? THREE.NormalBlending : THREE.AdditiveBlending;
    this.scene.traverse((o) => {
      for (const mat of ([] as THREE.Material[]).concat((o as THREE.Mesh).material ?? [])) {
        if (mat.userData.fixedBlending) continue; // opaque-looking surfaces (lens, silhouette, backdrop)
        mat.blending = blending;
        mat.needsUpdate = true;
      }
    });
  }

  private snapColours(): void {
    const pal = PALETTES[this.opts.theme];
    this.frame.a.set(pal[this.target.a]);
    this.frame.b.set(pal[this.target.b]);
    this.frame.hi.set(pal.hi);
  }

  /** Jump to the target profile without animation. */
  private snap(): void {
    const f = this.frame;
    const t = this.target;
    Object.assign(f, { energy: t.energy, expand: t.expand, flow: t.flow, scan: t.scan, morph: t.morph });
    f.time = STILL_TIME;
    f.level = f.react = f.voice = 0;
    this.snapColours();
    this.draw();
  }

  private loop = (): void => {
    if (this.disposed) return;
    this.raf = requestAnimationFrame(this.loop);
    const now = performance.now();
    const raw = this.lastTick ? (now - this.lastTick) / 1000 : 0.016;
    this.lastTick = now;
    const dt = Math.min(raw, 0.05);
    const f = this.frame;
    const t = this.target;
    const k = 1 - Math.exp(-dt * 3.5);
    f.energy += (t.energy - f.energy) * k;
    f.expand += (t.expand - f.expand) * k;
    f.flow += (t.flow - f.flow) * k;
    f.scan += (t.scan - f.scan) * k;
    // The transformation runs at a measured pace (≈3.5 s to materialise, ≈1.5 s to return) so it reads as a
    // process; it slows down as it arrives.
    const dm = t.morph - f.morph;
    f.morph += Math.sign(dm) * Math.min(Math.abs(dm), dt * (dm > 0 ? 0.3 : 0.7) * (0.35 + Math.min(1, Math.abs(dm) * 4)));
    const pal = PALETTES[this.opts.theme];
    f.a.lerp(this.tmp.set(pal[t.a]), k);
    f.b.lerp(this.tmp.set(pal[t.b]), k);
    f.time += dt;
    f.spinPhase += dt * t.spin;
    f.flowPhase += dt * (0.25 + f.flow);
    const level = this.levelSource ? this.levelSource() : 0;
    f.level += (level - f.level) * Math.min(1, dt * 14);
    this.pulseEnergy *= Math.exp(-dt * 5);
    f.react = Math.max(f.level, this.pulseEnergy);
    f.voice = this.pulseEnergy;
    this.draw();
    this.measure(raw * 1000);
  };

  private draw(): void {
    if (this.disposed) return;
    const f = this.frame;
    // As the avatar forms, the core settles at its chest and its orbits tighten around it: still one being.
    const settle = THREE.MathUtils.smoothstep(f.morph, 0.3, 1);
    this.coreGroup.position.set(this.coreAnchor.x * settle, this.coreAnchor.y * settle, 0);
    this.coreGroup.scale.setScalar(THREE.MathUtils.lerp(f.expand, this.coreSettledScale, settle));
    this.backdrop?.update(f);
    this.sphere.update(f);
    this.rings.update(f);
    this.avatar.update(f);
    this.portrait?.update(f);
    this.particles.update(f, this.coreGroup.position, this.coreGroup.scale.x);
    this.renderer.render(this.scene, this.camera);
  }

  /** Real frame timing. If frames stay slow, lower the resolution once rather than stutter. */
  private measure(ms: number): void {
    const s = this.sample;
    s.frames += 1;
    s.total += ms;
    s.worst = Math.max(s.worst, ms);
    if (s.total < 1000) return;
    const frameMs = s.total / s.frames;
    s.slow = frameMs > 26 ? s.slow + 1 : 0;
    if (s.slow >= 3 && !this.degraded && this.pixelRatio > 1) {
      this.degraded = true;
      this.pixelRatio = 1;
      this.renderer.setPixelRatio(1);
      this.resize(this.size.w, this.size.h);
    }
    this.last = { fps: Math.round(1000 / frameMs), frameMs: Math.round(frameMs * 10) / 10, worstMs: Math.round(s.worst * 10) / 10,
      drawCalls: this.renderer.info.render.calls, particles: this.particles.count, pixelRatio: this.pixelRatio, degraded: this.degraded };
    this.sample = { frames: 0, total: 0, worst: 0, slow: s.slow };
  }
}
