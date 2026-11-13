// The environment behind NOVA: a slowly moving electric-blue nebula with long luminous streamers crossing the
// screen. One full-screen quad, drawn first; no texture, no blur pass. Optional (the app can keep its own page
// background and a transparent canvas).

import * as THREE from "three";
import type { Frame, Layer } from "./holographic.types";

const VERT = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.9999, 1.0); }`;

const FRAG = /* glsl */ `
uniform float uTime; uniform float uIntensity; uniform vec2 uAspect;
uniform vec3 uDeep; uniform vec3 uMid; uniform vec3 uGlow;
varying vec2 vUv;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
  return v;
}
void main() {
  vec2 p = (vUv - 0.5) * uAspect;
  float t = uTime * 0.03;
  float n = fbm(p * 1.6 + vec2(t, -t * 0.6) + fbm(p * 2.2 - t) * 0.8);
  float vign = 1.0 - smoothstep(0.2, 1.1, length(p * vec2(0.75, 1.0)));
  vec3 col = mix(uDeep, uMid, clamp((n * 1.25 - 0.12) * uIntensity * (0.45 + 0.55 * vign), 0.0, 1.0));
  float s = 0.0;
  for (int i = 0; i < 4; i++) {
    float fi = float(i);
    // irregular, drifting curves: the noise bends them so they never read as clean sine waves
    float y = 0.26 * sin(p.x * (0.9 + fi * 0.41) + uTime * 0.04 * (1.0 + fi * 0.3) + fi * 2.1)
            + 0.18 * (fbm(vec2(p.x * 0.9 + fi * 3.7, uTime * 0.02)) - 0.5) + (fi - 1.5) * 0.17;
    float d = abs(p.y - y);
    float keepClear = smoothstep(0.08, 0.5, abs(p.x)); // the streamers thin out behind NOVA
    float along = smoothstep(0.35, 0.8, noise(vec2(p.x * 1.4 + fi * 7.0, uTime * 0.05))); // they fade in and out along their length
    s += (exp(-d * 220.0) + exp(-d * 35.0) * 0.12) * along * keepClear;
  }
  col += uGlow * s * 0.45 * uIntensity;
  gl_FragColor = vec4(col, 1.0);
}`;

const COLOURS = {
  dark: { deep: "#03061a", mid: "#2433c9", glow: "#9fb0ff" },
  light: { deep: "#f3f5fa", mid: "#d5dcf5", glow: "#4c5fd0" },
};

export class Backdrop implements Layer {
  readonly object: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;

  constructor() {
    this.object = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, depthTest: false, depthWrite: false,
      uniforms: { uTime: { value: 0 }, uIntensity: { value: 1 }, uAspect: { value: new THREE.Vector2(1, 1) },
        uDeep: { value: new THREE.Color() }, uMid: { value: new THREE.Color() }, uGlow: { value: new THREE.Color() } },
    }));
    this.object.frustumCulled = false;
    this.object.renderOrder = -10;
    this.object.material.userData.fixedBlending = true;
  }

  setAspect(aspect: number): void {
    this.object.material.uniforms.uAspect!.value.set(aspect, 1);
  }

  update(f: Frame): void {
    const u = this.object.material.uniforms;
    const c = COLOURS[f.light ? "light" : "dark"];
    u.uDeep!.value.set(c.deep);
    u.uMid!.value.set(c.mid);
    u.uGlow!.value.set(c.glow);
    u.uTime!.value = f.time;
    // The room dims while NOVA materialises, then brightens again around the formed avatar.
    u.uIntensity!.value = (0.5 + 0.5 * f.energy) * (1 - 0.6 * Math.sin(Math.PI * Math.min(1, f.morph)));
  }
}
