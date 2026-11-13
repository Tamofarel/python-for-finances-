// The humanoid form of the same intelligence: a dark silhouette outlined by luminous line art that draws itself
// once the particles have gathered into the figure, with energy running along the lines and an optional scanning band.
// Two meshes (silhouette, lines) and one sprite.

import * as THREE from "three";
import { glowTexture } from "./EnergySphere";
import { FIGURE_BOUNDS, FIGURE_STROKES, SILHOUETTE, strokePolylines } from "./figure";
import type { Frame, Layer } from "./holographic.types";

const VERT = /* glsl */ `
attribute float aSide; attribute float aU; attribute float aStart; attribute float aSpan; attribute float aGlow;
varying float vSide; varying float vU; varying float vStart; varying float vSpan; varying float vY; varying float vGlow;
void main() {
  vSide = aSide; vU = aU; vStart = aStart; vSpan = aSpan; vY = position.y; vGlow = aGlow;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const FRAG = /* glsl */ `
uniform vec3 uA; uniform vec3 uB; uniform vec3 uHi;
uniform float uReveal; uniform float uTime; uniform float uScan; uniform float uScanY; uniform float uOpacity; uniform float uReact;
varying float vSide; varying float vU; varying float vStart; varying float vSpan; varying float vY; varying float vGlow;
void main() {
  float core = 1.0 - abs(vSide);
  float at = vStart + vU * vSpan;                        // when this point of the stroke is reached
  float drawn = smoothstep(at, at + 0.06, uReveal);
  float tip = drawn * (1.0 - smoothstep(at + 0.04, at + 0.2, uReveal)); // the bright leading edge while it draws
  float dissolve = smoothstep(${FIGURE_BOUNDS.minY.toFixed(2)}, -2.6, vY); // the figure fades out at the bottom
  float flow = 0.75 + 0.25 * sin(vU * 18.0 - uTime * 2.2);
  float scan = uScan * exp(-pow((vY - uScanY) * 3.0, 2.0));
  float line = pow(core, 8.0);
  float halo = pow(core, 1.8) * 0.4;
  vec3 col = mix(mix(uA, uB, vU * 0.6), uHi, min(1.0, line * 1.1 * vGlow + tip * 0.6 + scan * 0.7));
  float a = (line * 1.2 + halo) * vGlow * drawn * dissolve * (flow + tip + scan + uReact * 0.35) * uOpacity;
  gl_FragColor = vec4(col, a);
}`;

function buildRibbons(segments: number): THREE.BufferGeometry {
  const pos: number[] = [], side: number[] = [], u: number[] = [], start: number[] = [], span: number[] = [], glow: number[] = [];
  const index: number[] = [];
  for (const stroke of FIGURE_STROKES) {
    for (const line of strokePolylines(stroke, segments)) {
      const base = pos.length / 3;
      const z = (Math.random() - 0.5) * 0.1; // slight depth so the figure is not a flat decal
      line.forEach((p, i) => {
        const a = line[Math.max(0, i - 1)]!, b = line[Math.min(line.length - 1, i + 1)]!;
        const tx = b.x - a.x, ty = b.y - a.y, len = Math.hypot(tx, ty) || 1;
        const t = i / (line.length - 1);
        const w = stroke.width * 2.2 * (0.3 + 0.7 * Math.sin(Math.PI * t) ** 0.6); // tapered ends
        for (const s of [-1, 1]) {
          pos.push(p.x + (-ty / len) * w * s, p.y + (tx / len) * w * s, z);
          side.push(s); u.push(t); start.push(stroke.order); span.push(stroke.span); glow.push(stroke.glow);
        }
        if (i < line.length - 1) index.push(base + i * 2, base + i * 2 + 1, base + i * 2 + 2, base + i * 2 + 1, base + i * 2 + 3, base + i * 2 + 2);
      });
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("aSide", new THREE.Float32BufferAttribute(side, 1));
  g.setAttribute("aU", new THREE.Float32BufferAttribute(u, 1));
  g.setAttribute("aStart", new THREE.Float32BufferAttribute(start, 1));
  g.setAttribute("aSpan", new THREE.Float32BufferAttribute(span, 1));
  g.setAttribute("aGlow", new THREE.Float32BufferAttribute(glow, 1));
  g.setIndex(index);
  return g;
}

function silhouette(): THREE.ShapeGeometry {
  const right = SILHOUETTE.map(([x, y]) => new THREE.Vector2(x, y));
  const left = right.slice(0, -1).reverse().map((p) => new THREE.Vector2(-p.x, p.y));
  return new THREE.ShapeGeometry(new THREE.Shape([...right, ...left]));
}

const FILL = { dark: new THREE.Color("#050a2c"), light: new THREE.Color("#f1f3fa") };

export class HolographicAvatar implements Layer {
  readonly object = new THREE.Group();
  private strokes: THREE.Mesh<THREE.BufferGeometry, THREE.ShaderMaterial>;
  private fill: THREE.Mesh<THREE.ShapeGeometry, THREE.MeshBasicMaterial>;
  private aura: THREE.Sprite;
  enabled = true; // false once another form (the portrait) has taken over

  constructor(lowPower: boolean) {
    this.aura = new THREE.Sprite(new THREE.SpriteMaterial({ map: glowTexture([[0, 0.5], [0.5, 0.16], [1, 0]]), transparent: true, depthWrite: false, opacity: 0 }));
    this.aura.scale.set(5, 7, 1);
    this.aura.position.set(0, -0.2, -0.4);
    this.aura.renderOrder = 0.5;
    this.fill = new THREE.Mesh(silhouette(), new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, opacity: 0 }));
    this.fill.material.userData.fixedBlending = true;
    this.fill.position.z = -0.2;
    this.fill.renderOrder = 1;
    this.strokes = new THREE.Mesh(buildRibbons(lowPower ? 32 : 56), new THREE.ShaderMaterial({
      vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, side: THREE.DoubleSide,
      uniforms: {
        uA: { value: new THREE.Color() }, uB: { value: new THREE.Color() }, uHi: { value: new THREE.Color() },
        uReveal: { value: 0 }, uTime: { value: 0 }, uScan: { value: 0 }, uScanY: { value: 0 }, uOpacity: { value: 0 }, uReact: { value: 0 },
      },
    }));
    this.strokes.renderOrder = 3;
    this.object.add(this.aura, this.fill, this.strokes);
  }

  update(f: Frame): void {
    this.object.visible = this.enabled && f.morph > 0.02;
    if (!this.object.visible) return;
    const u = this.strokes.material.uniforms;
    u.uA!.value.copy(f.a);
    u.uB!.value.copy(f.b);
    u.uHi!.value.copy(f.hi);
    u.uReveal!.value = THREE.MathUtils.clamp((f.morph - 0.55) / 0.45, 0, 1) * 1.1; // lines draw once the particles have gathered
    u.uTime!.value = f.flowPhase * 2;
    u.uScan!.value = f.scan;
    u.uScanY!.value = THREE.MathUtils.lerp(FIGURE_BOUNDS.minY, FIGURE_BOUNDS.maxY, (f.time * 0.22) % 1);
    u.uOpacity!.value = (0.6 + f.energy * 0.6) * (f.light ? 0.9 : 1);
    u.uReact!.value = f.react;
    this.fill.material.color.copy(f.light ? FILL.light : FILL.dark);
    this.fill.material.opacity = THREE.MathUtils.smoothstep(f.morph, 0.6, 1) * 0.82;
    this.aura.material.color.copy(f.a);
    this.aura.material.opacity = THREE.MathUtils.smoothstep(f.morph, 0.3, 1) * (0.12 + f.energy * 0.18 + f.react * 0.08) * (f.light ? 0.4 : 1);
    this.object.rotation.y = Math.sin(f.time * 0.3) * 0.05; // a slow, slight turn: presence, not animation for its own sake
    this.object.position.y = Math.sin(f.time * 0.5) * 0.02;
  }
}
