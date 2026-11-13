// The portrait form of NOVA: an image that materialises out of the energy core (a dissolve that radiates from the
// core, with a luminous edge), with a hologram treatment and lights that answer real signals:
//  - the mouth opens on real speech-synthesis word boundaries (the lower lip slides down in the shader),
//  - the eyes and the forehead jewel brighten with the real microphone level / spoken words and while thinking.
// Nothing here is driven by a synthetic "audio" curve.
//
// The image is an asset passed in by URL, with anchor points in image pixels. Until it has loaded (or if it fails)
// the scene keeps the line-art avatar.

import * as THREE from "three";
import { glowTexture } from "./EnergySphere";
import type { Frame, Layer } from "./holographic.types";

export interface PortraitSpec {
  url: string;
  width: number; // image size in pixels
  height: number;
  eyes: [number, number][];
  jewel?: [number, number];
  earrings?: [number, number][];
  core: [number, number]; // where the energy core sits once the portrait has formed
  mouth: { cx: number; y: number; halfWidth: number; maxOpen: number }; // lower-lip line, in pixels
}

/** Anchors of the reference portrait used in the lab (frontend/hologram-assets/portrait.jpg, not distributed). */
export const LAB_PORTRAIT: PortraitSpec = {
  url: "/hologram-assets/portrait.jpg",
  width: 736,
  height: 1273,
  eyes: [[221, 482], [538, 474]],
  jewel: [384, 310],
  earrings: [[83, 735], [670, 720]],
  core: [377, 1195],
  mouth: { cx: 381, y: 776, halfWidth: 92, maxOpen: 17 },
};

const PLANE_H = 5.9;

const VERT = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;

const FRAG = /* glsl */ `
uniform sampler2D uMap; uniform float uReveal; uniform float uTime; uniform float uMouth; uniform float uSat; uniform float uDim;
uniform float uScan; uniform float uScanY; uniform float uOpacity; uniform vec3 uEdge; uniform vec4 uMouthBox; uniform vec2 uCore;
varying vec2 vUv;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p), u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 4; i++) { v += a * noise(p); p *= 2.1; a *= 0.5; } return v; }
void main() {
  float ix = vUv.x, iy = 1.0 - vUv.y;                  // image coordinates, y down
  // mouth: the lower lip slides down, pinned at the corners; the chin below stretches back into place
  float u = (ix - uMouthBox.x) / uMouthBox.z;
  float shape = u * u < 1.0 ? pow(1.0 - u * u, 0.8) : 0.0;
  float below = iy - uMouthBox.y;
  float shift = uMouth * uMouthBox.w * shape * (1.0 - smoothstep(uMouthBox.w * 1.6, uMouthBox.w * 4.5, below));
  vec3 col;
  if (below > 0.0 && below < shift) {
    float k = below / max(shift, 1e-5);
    col = mix(vec3(0.10, 0.02, 0.035), vec3(0.29, 0.08, 0.12), k);
    col = mix(col, vec3(0.70, 0.73, 0.76), step(k, 0.3) * step(abs(u), 0.6) * 0.8); // a hint of teeth
  } else {
    col = texture2D(uMap, vec2(ix, 1.0 - (below > 0.0 ? iy - shift : iy))).rgb;
  }
  float grey = dot(col, vec3(0.299, 0.587, 0.114));
  col = mix(vec3(grey), col, uSat) * uDim;
  col *= 0.955 + 0.045 * sin(iy * 760.0 + uTime * 3.0);   // faint scanlines
  col += uEdge * uScan * exp(-pow((iy - uScanY) * 14.0, 2.0)) * 0.35;
  // fade into the environment at the edges
  float mask = (1.0 - smoothstep(0.62, 1.0, length((vec2(ix, iy) - vec2(0.5, 0.47)) * vec2(1.9, 1.15))))
             * smoothstep(0.0, 0.05, iy) * smoothstep(1.0, 0.93, iy);
  // materialise outwards from the core
  float n = 0.5 * fbm(vUv * vec2(7.0, 12.0)) + 0.5 * length((vec2(ix, iy) - uCore) * vec2(0.7, 1.0));
  float thr = uReveal * 1.25 - 0.08;
  float vis = smoothstep(thr, thr - 0.05, n);
  float edge = exp(-pow((n - thr) * 30.0, 2.0)) * (1.0 - smoothstep(0.92, 1.0, uReveal));
  col = mix(col, uEdge * 1.5, edge * 0.85);
  gl_FragColor = vec4(col, max(vis, edge) * mask * uOpacity);
}`;

const CYAN = new THREE.Color("#5ff3ff");

export class PortraitAvatar implements Layer {
  readonly object = new THREE.Group();
  readonly core: THREE.Vector2;
  ready = false;
  private plane: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private eyes: THREE.Sprite[] = [];
  private jewel: THREE.Sprite | null = null;
  private earrings: THREE.Sprite[] = [];
  private tint = new THREE.Color();
  private planeW: number;

  constructor(private spec: PortraitSpec, onReady: (targets: (count: number) => Float32Array) => void, onError: () => void) {
    this.planeW = (PLANE_H * spec.width) / spec.height;
    this.core = this.toScene(spec.core);
    const m = spec.mouth;
    this.plane = new THREE.Mesh(new THREE.PlaneGeometry(this.planeW, PLANE_H), new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false,
      uniforms: {
        uMap: { value: null }, uReveal: { value: 0 }, uTime: { value: 0 }, uMouth: { value: 0 }, uSat: { value: 1 }, uDim: { value: 1 },
        uScan: { value: 0 }, uScanY: { value: 0 }, uOpacity: { value: 1 }, uEdge: { value: new THREE.Color() },
        uMouthBox: { value: new THREE.Vector4(m.cx / spec.width, m.y / spec.height, m.halfWidth / spec.width, m.maxOpen / spec.height) },
        uCore: { value: new THREE.Vector2(spec.core[0] / spec.width, spec.core[1] / spec.height) },
      },
    }));
    this.plane.material.userData.fixedBlending = true; // a picture: always normal blending
    this.plane.renderOrder = 1;
    this.plane.position.z = -0.25;
    this.object.add(this.plane);

    const tex = glowTexture();
    const sprite = (p: [number, number], scale: number) => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, opacity: 0 }));
      const q = this.toScene(p);
      s.position.set(q.x, q.y, 0);
      s.scale.setScalar(scale);
      s.renderOrder = 3;
      this.object.add(s);
      return s;
    };
    this.eyes = spec.eyes.map((p) => sprite(p, 0.5));
    if (spec.jewel) this.jewel = sprite(spec.jewel, 0.95);
    this.earrings = (spec.earrings ?? []).map((p) => sprite(p, 0.4));
    this.object.visible = false;

    new THREE.TextureLoader().load(spec.url, (texture) => {
      // The shader writes the image's sRGB values straight out (no linear round trip), so no colour-space tag here.
      this.plane.material.uniforms.uMap!.value = texture;
      this.ready = true;
      onReady((count) => this.sampleTargets(texture.image as HTMLImageElement, count));
    }, undefined, onError);
  }

  /** Image pixels to scene units. */
  private toScene([px, py]: [number, number]): THREE.Vector2 {
    return new THREE.Vector2((px / this.spec.width - 0.5) * this.planeW, (0.5 - py / this.spec.height) * PLANE_H);
  }

  /** Where the energy particles settle: weighted towards the bright parts of the image, away from its faded edges. */
  private sampleTargets(image: HTMLImageElement, count: number): Float32Array {
    const w = 92, h = Math.round((92 * this.spec.height) / this.spec.width);
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const g = c.getContext("2d")!;
    g.drawImage(image, 0, 0, w, h);
    const px = g.getImageData(0, 0, w, h).data;
    const out = new Float32Array(count * 3);
    for (let i = 0, guard = 0; i < count && guard < count * 60; guard++) {
      const x = Math.random(), y = Math.random();
      const o = (Math.floor(y * h) * w + Math.floor(x * w)) * 4;
      const lum = (0.299 * px[o]! + 0.587 * px[o + 1]! + 0.114 * px[o + 2]!) / 255;
      const edge = Math.min(1, x / 0.16, (1 - x) / 0.16, y / 0.07, (1 - y) / 0.1);
      if (Math.random() > lum * lum * Math.max(0, edge)) continue;
      const q = this.toScene([x * this.spec.width, y * this.spec.height]);
      out.set([q.x, q.y, (Math.random() - 0.5) * 0.2], i * 3);
      i++;
    }
    return out;
  }

  update(f: Frame): void {
    this.object.visible = this.ready && f.morph > 0.02;
    if (!this.object.visible) return;
    const u = this.plane.material.uniforms;
    const formed = THREE.MathUtils.smoothstep(f.morph, 0.75, 1);
    this.tint.copy(CYAN).lerp(f.a, 0.35);
    u.uReveal!.value = THREE.MathUtils.clamp((f.morph - 0.45) / 0.5, 0, 1);
    u.uTime!.value = f.time;
    u.uMouth!.value = Math.min(1, f.voice * 1.3); // real word boundaries only
    // error and interruption read as a dimmed, desaturated presence
    const quiet = f.a.getHSL({ h: 0, s: 0, l: 0 }).s < 0.3 || f.energy < 0.3;
    u.uSat!.value = quiet ? 0.35 : 1;
    u.uDim!.value = 0.85 + f.energy * 0.25;
    u.uScan!.value = f.scan;
    u.uScanY!.value = (f.time * 0.22) % 1;
    u.uEdge!.value.copy(this.tint);
    const dim = f.light ? 0.4 : 1;
    const eye = (0.15 + 0.35 * f.flow + 0.55 * f.react) * formed * dim;
    this.eyes.forEach((s) => {
      s.material.color.copy(this.tint);
      s.material.opacity = eye;
    });
    if (this.jewel) {
      this.jewel.material.color.copy(this.tint);
      this.jewel.material.opacity = (0.2 + 0.35 * f.energy + 0.6 * f.react) * formed * dim;
      this.jewel.scale.setScalar(0.85 + f.react * 0.45);
    }
    this.earrings.forEach((s, i) => {
      s.material.color.copy(this.tint);
      s.material.opacity = (0.14 + 0.08 * Math.sin(f.time * 1.4 + i * 2)) * formed * dim;
    });
    this.object.position.y = Math.sin(f.time * 0.5) * 0.015;
  }
}
