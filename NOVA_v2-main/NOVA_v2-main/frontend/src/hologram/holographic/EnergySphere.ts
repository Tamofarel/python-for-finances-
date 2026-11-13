// The energy core: a dark lens with a crisp luminous rim (the rim is drawn by OrbitalRings), a white-hot centre and
// a blue bloom. The lens opens up as the avatar forms; the glowing centre stays, at the avatar's chest.
// Bloom is done with additive sprites, not post-processing: no full-screen blur pass.

import * as THREE from "three";
import type { Frame, Layer } from "./holographic.types";

export function glowTexture(stops: [number, number][] = [[0, 0.95], [0.22, 0.4], [1, 0]]): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  for (const [at, alpha] of stops) grad.addColorStop(at, `rgba(255,255,255,${alpha})`);
  g.fillStyle = grad;
  g.fillRect(0, 0, 128, 128);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

const LENS = { dark: new THREE.Color("#040a30"), light: new THREE.Color("#eef1fa") };

export const LENS_RADIUS = 0.9;

export class EnergySphere implements Layer {
  readonly object = new THREE.Group();
  private halo: THREE.Sprite;
  private glow: THREE.Sprite;
  private hot: THREE.Sprite;
  private lens: THREE.Mesh<THREE.CircleGeometry, THREE.MeshBasicMaterial>;

  constructor() {
    const tex = glowTexture();
    const sprite = (scale: number, order: number) => {
      const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
      s.scale.setScalar(scale);
      s.renderOrder = order;
      this.object.add(s);
      return s;
    };
    this.halo = sprite(6.5, 0.5); // behind the lens
    this.lens = new THREE.Mesh(new THREE.CircleGeometry(LENS_RADIUS, 96),
      new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false }));
    this.lens.material.userData.fixedBlending = true;
    this.lens.renderOrder = 1;
    this.lens.position.z = -0.05;
    this.object.add(this.lens);
    this.glow = sprite(1.6, 2);
    this.hot = sprite(0.5, 2);
  }

  update(f: Frame): void {
    const dim = f.light ? 0.5 : 1;
    const open = THREE.MathUtils.smoothstep(f.morph, 0.08, 0.4); // the lens opens as the avatar forms
    const breath = 1 + Math.sin(f.time * 0.9) * 0.02; // slow breathing: alive without demanding attention
    this.object.scale.setScalar(breath + f.react * 0.08);
    this.lens.visible = open < 0.99;
    this.lens.material.color.copy(f.light ? LENS.light : LENS.dark);
    this.lens.material.opacity = 0.9 * (1 - open);
    this.halo.material.color.copy(f.a);
    this.halo.material.opacity = (0.08 + f.energy * 0.22 + f.react * 0.12) * dim * (1 - open * 0.5);
    this.glow.material.color.copy(f.a);
    this.glow.material.opacity = (0.3 + f.energy * 0.5 + f.react * 0.3) * dim;
    this.glow.scale.setScalar(1.3 + f.energy * 0.5 + f.react * 0.8 + open * 0.8);
    this.hot.material.color.copy(f.hi);
    this.hot.material.opacity = Math.min(1, 0.5 + f.energy * 0.5 + f.react * 0.3);
    this.hot.scale.setScalar(0.4 + f.energy * 0.25 + f.react * 0.3 + open * 0.5);
  }
}
