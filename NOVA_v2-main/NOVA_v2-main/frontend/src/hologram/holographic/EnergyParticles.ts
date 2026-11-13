// Energy particles. Most of them live compressed inside the core as a dense, slowly turning star; as the avatar
// forms they unwind in a spiral out of the core and settle on the figure like luminous sand. The rest drift around as
// fine dust. All motion is in the vertex shader: one draw call, no per-frame CPU work.

import * as THREE from "three";
import { sampleFigure } from "./figure";
import type { Frame, Layer } from "./holographic.types";

const VERT = /* glsl */ `
attribute vec3 aFigure; attribute vec4 aSeed; // x: random, y: size, z: forms the figure (0/1), w: phase
uniform float uTime; uniform float uDrift; uniform float uMorph; uniform float uSize; uniform float uFlow; uniform float uReact;
uniform vec3 uCore; uniform float uCoreScale; uniform float uFormedFade;
varying float vAlpha; varying float vTint; varying float vFormed;
vec2 rot(vec2 v, float a) { float c = cos(a), s = sin(a); return vec2(c * v.x - s * v.y, s * v.x + c * v.y); }
void main() {
  vec3 q; float m = 0.0; float size = 1.0;
  if (aSeed.z > 0.5) {
    vec3 h = position;
    h.xz = rot(h.xz, uDrift * 2.0 + aSeed.w);
    h.xy = rot(h.xy, uDrift * 1.3);
    vec3 home = uCore + h * uCoreScale * (1.0 + uFlow * 0.2 * sin(uTime * 2.0 + aSeed.w));
    float delay = aSeed.x * 0.2;
    m = smoothstep(delay, delay + 0.45, uMorph); // every particle has arrived by morph 0.65
    vec3 d = aFigure - uCore;
    float turns = (1.0 - m) * (6.0 + aSeed.x * 4.0); // a galaxy-like vortex out of the core
    vec3 spiral = uCore + vec3(rot(d.xy, turns) * m, d.z * m);
    q = mix(home, spiral, smoothstep(0.0, 0.12, m));
    q += 0.02 * m * vec3(sin(uTime * 1.3 + aSeed.w), cos(uTime * 1.1 + aSeed.w * 1.7), 0.0);
    size = mix(0.5, 1.0, m);
    vAlpha = mix(0.22, 0.75, m) * (1.0 - uFormedFade * smoothstep(0.85, 1.0, m)); // they step back once the form is there
  } else {
    float ang = uDrift * (0.5 + aSeed.x);
    float c = cos(ang), s = sin(ang);
    q = vec3(position.x * c - position.z * s, position.y + sin(uTime * 0.4 + aSeed.w) * 0.06, position.x * s + position.z * c);
    q *= 1.0 - uFlow * 0.08 * sin(uTime * 1.7 + aSeed.w);
    vAlpha = 0.35;
  }
  vec4 mv = modelViewMatrix * vec4(q, 1.0);
  gl_PointSize = uSize * aSeed.y * size * (1.0 + uReact * 0.6) / -mv.z;
  vAlpha *= (0.5 + 0.5 * aSeed.x) * (0.7 + 0.3 * sin(uTime * (0.6 + aSeed.x) + aSeed.w * 6.0));
  vTint = aSeed.x; vFormed = m;
  gl_Position = projectionMatrix * mv;
}`;

const FRAG = /* glsl */ `
uniform vec3 uA; uniform vec3 uB; uniform vec3 uHi; uniform float uOpacity;
varying float vAlpha; varying float vTint; varying float vFormed;
void main() {
  float a = smoothstep(0.5, 0.0, length(gl_PointCoord - 0.5));
  a *= a;
  vec3 col = mix(mix(uB, uA, vTint), uHi, 0.45 + 0.4 * a);
  gl_FragColor = vec4(col, a * vAlpha * uOpacity);
}`;

/** Gaussian-like offsets inside the core: dense at the centre, thinning towards the lens rim. */
function coreStar(count: number): Float32Array {
  const out = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = 0.5 * Math.pow(Math.random(), 1.7);
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    out.set([r * Math.sin(ph) * Math.cos(th), r * Math.sin(ph) * Math.sin(th), r * Math.cos(ph) * 0.4], i * 3);
  }
  return out;
}

export class EnergyParticles implements Layer {
  readonly object: THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;
  readonly count: number;

  constructor(count: number) {
    this.count = count;
    const star = coreStar(count);
    const pos = new Float32Array(count * 3);
    const seed = new Float32Array(count * 4);
    for (let i = 0; i < count; i++) {
      const forms = Math.random() < 0.78;
      if (forms) pos.set(star.subarray(i * 3, i * 3 + 3), i * 3);
      else {
        const r = 1.6 + Math.random() * 2.6;
        const th = Math.random() * Math.PI * 2;
        const ph = Math.acos(2 * Math.random() - 1);
        pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.sin(ph) * Math.sin(th) * 0.6, r * Math.cos(ph) * 0.6], i * 3);
      }
      seed.set([Math.random(), 0.5 + Math.random(), forms ? 1 : 0, Math.random() * Math.PI * 2], i * 4);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aFigure", new THREE.BufferAttribute(sampleFigure(count), 3));
    g.setAttribute("aSeed", new THREE.BufferAttribute(seed, 4));
    this.object = new THREE.Points(g, new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
      uniforms: {
        uTime: { value: 0 }, uDrift: { value: 0 }, uMorph: { value: 0 }, uSize: { value: 30 }, uFlow: { value: 0 }, uReact: { value: 0 },
        uCore: { value: new THREE.Vector3() }, uCoreScale: { value: 1 }, uFormedFade: { value: 0 }, uOpacity: { value: 0.5 },
        uA: { value: new THREE.Color() }, uB: { value: new THREE.Color() }, uHi: { value: new THREE.Color() },
      },
    }));
    this.object.frustumCulled = false;
    this.object.renderOrder = 2;
  }

  /** Replace where the particles settle (e.g. once a portrait has loaded). `fade` dims settled particles (0..1). */
  setTargets(targets: Float32Array, fade: number): void {
    const attr = this.object.geometry.getAttribute("aFigure") as THREE.BufferAttribute;
    attr.set(targets.subarray(0, attr.array.length));
    attr.needsUpdate = true;
    this.object.material.uniforms.uFormedFade!.value = fade;
  }

  /** `bufferHeight` is the drawing-buffer height in pixels; keeps particles the same apparent size at any resolution. */
  setViewport(bufferHeight: number, fovDeg: number): void {
    this.object.material.uniforms.uSize!.value = (0.035 * bufferHeight) / (2 * Math.tan((fovDeg * Math.PI) / 360));
  }

  update(f: Frame, core?: THREE.Vector3, coreScale = 1): void {
    const u = this.object.material.uniforms;
    u.uTime!.value = f.time;
    u.uDrift!.value = f.flowPhase * 0.25;
    u.uMorph!.value = f.morph;
    u.uFlow!.value = f.flow;
    u.uReact!.value = f.react;
    if (core) u.uCore!.value.copy(core);
    u.uCoreScale!.value = coreScale;
    u.uOpacity!.value = (0.4 + f.energy * 0.6) * (f.light ? 0.8 : 1);
    u.uA!.value.copy(f.a);
    u.uB!.value.copy(f.b);
    u.uHi!.value.copy(f.hi);
  }
}
