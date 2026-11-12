// Microphone level meter for the waveform. Opened only while push-to-talk is active, closed right after.

export type MicPermission = "granted" | "denied" | "prompt" | "unknown" | "unsupported";

export async function microphonePermission(): Promise<MicPermission> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) return "unsupported";
  try {
    const status = await navigator.permissions?.query({ name: "microphone" as PermissionName });
    return (status?.state as MicPermission | undefined) ?? "unknown";
  } catch {
    return "unknown"; // Firefox/Safari may not support querying the microphone permission
  }
}

export class MicLevel {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private data: Uint8Array<ArrayBuffer> | null = null;

  async open(): Promise<boolean> {
    if (!navigator.mediaDevices?.getUserMedia) return false;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return false;
      this.ctx = new Ctx();
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      source.connect(this.analyser);
      this.data = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));
      return true;
    } catch {
      this.close();
      return false;
    }
  }

  /** Time-domain samples (0–255, 128 = silence) from the real microphone, or null when closed. */
  samples(): Uint8Array | null {
    if (!this.analyser || !this.data) return null;
    this.analyser.getByteTimeDomainData(this.data);
    return this.data;
  }

  /** Root-mean-square level in 0..1. */
  level(): number {
    const d = this.samples();
    if (!d) return 0;
    let sum = 0;
    for (let i = 0; i < d.length; i++) {
      const v = (d[i]! - 128) / 128;
      sum += v * v;
    }
    return Math.min(1, Math.sqrt(sum / d.length) * 3);
  }

  close(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close().catch(() => undefined);
    this.stream = null;
    this.ctx = null;
    this.analyser = null;
    this.data = null;
  }
}
