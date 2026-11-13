// Orbital structures around the core: the lens rim, a few faint measured circles, and the large luminous loops
// that weave around it (white-hot centre line, soft blue halo). Each ring is a ribbon shaped in the vertex shader,
// so wobble and voice reaction cost no CPU. The loops withdraw into the core as the avatar forms.

import * as THREE from "three";
import { LENS_RADIUS } from "./EnergySphere";
import type { Frame, Layer } from "./holographic.types";

interface RingSpec {
  r: number;
  width: number;
  tilt?: [number, number];
  wobble?: number; // radial displacement (fraction of r)
  k?: number; // number of lobes
  depth?: number; // out-of-plane displacement (fraction of r)
  speed: number; // how fast the shape travels (multiplies the flow phase)
  spin: number; // rotation direction and weight (multiplies the spin phase)
  dash?: number; // brightness variations along the ring (0 = even)
  hot: number; // 0..1 white-hot centre line
  opacity: number;
  hue: "a" | "b";
  keep: number; // how much of it remains once the avatar has formed
}

const RINGS: RingSpec[] = [
  { r: LENS_RADIUS, width: 0.045, speed: 0.3, spin: 0, hot: 1, opacity: 1, hue: "a", keep: 0.35 }, // lens rim
  { r: 0.58, width: 0.012, speed: 0.4, spin: -0.4, dash: 48, hot: 0, opacity: 0.35, hue: "b", keep: 0 },
  { r: 2.2, width: 0.01, speed: 0.6, spin: -0.2, dash: 90, hot: 0, opacity: 0.22, hue: "b", keep: 0 },
  // luminous loops
  { r: 1.32, width: 0.07, wobble: 0.24, k: 3, depth: 0.25, speed: 0.6, spin: 0.3, dash: 1, hot: 1, opacity: 1, hue: "a", keep: 0.12 },
  { r: 1.36, width: 0.065, wobble: 0.27, k: 2, depth: 0.35, speed: -0.5, spin: -0.4, dash: 1, hot: 1, opacity: 0.9, hue: "a", keep: 0.12 },
  { r: 1.28, width: 0.06, wobble: 0.3, k: 4, depth: 0.3, speed: 0.4, spin: 0.5, dash: 2, hot: 0.9, opacity: 0.8, hue: "b", keep: 0.1 },
  { r: 1.45, width: 0.06, wobble: 0.2, k: 3, depth: 0.4, speed: -0.7, spin: 0.2, dash: 1, hot: 0.9, opacity: 0.75, hue: "b", keep: 0.1 },
  { r: 1.25, width: 0.05, wobble: 0.34, k: 2, depth: 0.2, speed: 0.8, spin: -0.6, dash: 2, hot: 0.8, opacity: 0.6, hue: "a", keep: 0.08 },
  { r: 1.5, width: 0.05, tilt: [1.0, 0.25], wobble: 0.22, k: 3, depth: 0.3, speed: 0.35, spin: 0.35, dash: 1, hot: 0.8, opacity: 0.55, hue: "b", keep: 0.06 },
  { r: 1.4, width: 0.05, tilt: [-0.6, 0.9], wobble: 0.25, k: 2, depth: 0.3, speed: -0.4, spin: -0.3, dash: 1, hot: 0.8, opacity: 0.5, hue: "a", keep: 0.06 },
];

const VERT = /* glsl */ `
attribute float aAngle; attribute float aSide;
uniform float uR; uniform float uWidth; uniform float uWobble; uniform float uK; uniform float uDepth;
uniform float uPhase; uniform float uLevel;
varying float vSide; varying float vAngle;
void main() {
  float w = sin(aAngle * uK + uPhase) * 0.6 + sin(aAngle * (uK + 2.0) - uPhase * 1.7) * 0.4;
  float voice = uLevel * 0.12 * sin(aAngle * 6.0 + uPhase * 3.0);
  float r = uR * (1.0 + uWobble * w + voice) + aSide * uWidth;
  vec3 p = vec3(cos(aAngle) * r, sin(aAngle) * r, uDepth * uR * sin(aAngle * max(uK - 1.0, 1.0) + uPhase * 0.8));
  vSide = aSide; vAngle = aAngle;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
}`;

const FRAG = /* glsl */ `
uniform vec3 uColor; uniform vec3 uHi; uniform float uOpacity; uniform float uDash; uniform float uHot; uniform float uPhase;
varying float vSide; varying float vAngle;
void main() {
  float core = 1.0 - abs(vSide);
  float line = pow(core, 9.0) * uHot;
  float halo = pow(core, 2.0) * 0.4 + pow(core, 5.0) * 0.4 * (1.0 - uHot);
  float along = uDash > 0.0 ? 0.35 + 0.65 * (0.5 + 0.5 * sin(vAngle * uDash + uPhase)) : 1.0;
  vec3 col = mix(uColor, uHi, min(1.0, line * 1.2));
  gl_FragColor = vec4(col, (line + halo) * uOpacity * along);
}`;

function ribbonCircle(segments: number): THREE.BufferGeometry {
  const angle = new Float32Array((segments + 1) * 2);
  const side = new Float32Array((segments + 1) * 2);
  const index: number[] = [];
  for (let i = 0; i <= segments; i++) {
    angle[i * 2] = angle[i * 2 + 1] = (i / segments) * Math.PI * 2;
    side[i * 2] = -1;
    side[i * 2 + 1] = 1;
    if (i < segments) index.push(i * 2, i * 2 + 1, i * 2 + 2, i * 2 + 1, i * 2 + 3, i * 2 + 2);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array((segments + 1) * 6), 3)); // shaped in the shader
  g.setAttribute("aAngle", new THREE.BufferAttribute(angle, 1));
  g.setAttribute("aSide", new THREE.BufferAttribute(side, 1));
  g.setIndex(index);
  return g;
}

export class OrbitalRings implements Layer {
  readonly object = new THREE.Group();
  private rings: { spec: RingSpec; mesh: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial> }[] = [];

  constructor(lowPower: boolean) {
    const geometry = ribbonCircle(lowPower ? 160 : 260);
    RINGS.forEach((spec, i) => {
      const mesh = new THREE.Mesh(geometry, new THREE.ShaderMaterial({
        vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide,
        uniforms: {
          uR: { value: spec.r }, uWidth: { value: spec.width }, uWobble: { value: 0 }, uK: { value: spec.k ?? 0 },
          uDepth: { value: spec.depth ?? 0 }, uPhase: { value: i * 1.7 }, uLevel: { value: 0 }, uDash: { value: spec.dash ?? 0 },
          uHot: { value: spec.hot }, uOpacity: { value: 0 }, uColor: { value: new THREE.Color() }, uHi: { value: new THREE.Color() },
        },
      }));
      mesh.frustumCulled = false; // positions are computed in the shader
      mesh.renderOrder = 2;
      const holder = new THREE.Group();
      if (spec.tilt) holder.rotation.set(spec.tilt[0], spec.tilt[1], 0);
      holder.add(mesh);
      this.object.add(holder);
      this.rings.push({ spec, mesh });
    });
  }

  update(f: Frame): void {
    const formed = THREE.MathUtils.smoothstep(f.morph, 0.08, 0.45);
    this.rings.forEach(({ spec, mesh }, i) => {
      const u = mesh.material.uniforms;
      const presence = THREE.MathUtils.lerp(1, spec.keep, formed);
      mesh.visible = presence > 0.01;
      mesh.rotation.z = f.spinPhase * spec.spin + i;
      u.uPhase!.value = f.flowPhase * spec.speed * 2 + i * 1.7;
      u.uWobble!.value = (spec.wobble ?? 0) * (0.6 + f.flow * 0.6);
      u.uLevel!.value = f.react; // the loops answer the real microphone level / spoken word boundaries
      u.uOpacity!.value = spec.opacity * presence * (0.35 + f.energy * 0.8) * (f.light ? 0.9 : 1);
      u.uColor!.value.copy(spec.hue === "a" ? f.a : f.b);
      u.uHi!.value.copy(f.hi);
    });
  }
}
