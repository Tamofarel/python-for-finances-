// Microphone capture for local voice processing. The microphone is opened only when the owner turns voice on, and
// every track is stopped when it is turned off.

export type MicFailure = "denied" | "no_device" | "unsupported" | "busy" | "error";

export class MicError extends Error {
  constructor(public kind: MicFailure, message: string) {
    super(message);
  }
}

export function micErrorFrom(e: unknown): MicError {
  const name = (e as { name?: string })?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return new MicError("denied", "Microphone access is blocked. Allow it for this site in the browser's address bar, then try again.");
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return new MicError("no_device", "No microphone was found.");
  if (name === "NotReadableError" || name === "AbortError") {
    return new MicError("busy", "The microphone is in use by another application or could not be started.");
  }
  return new MicError("error", `The microphone could not be started (${name || "unknown error"}).`);
}

export class MicCapture {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private analyser: AnalyserNode | null = null;
  private data: Uint8Array<ArrayBuffer> | null = null;

  static supported(): boolean {
    return typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof AudioWorkletNode !== "undefined";
  }

  get active(): boolean {
    return this.stream !== null;
  }

  /** Browsers keep audio processing suspended until the page has had a click or key press. */
  get suspended(): boolean {
    return this.ctx?.state === "suspended";
  }

  async resume(): Promise<boolean> {
    try {
      await this.ctx?.resume();
    } catch {
      /* still blocked */
    }
    return !this.suspended;
  }

  /** Opens the microphone and calls onPcm with 16 kHz mono PCM16 chunks (~100 ms each). */
  async start(onPcm: (chunk: ArrayBuffer) => void): Promise<void> {
    if (!MicCapture.supported()) throw new MicError("unsupported", "This browser cannot capture microphone audio for local processing.");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch (e) {
      throw micErrorFrom(e);
    }
    try {
      this.ctx = new AudioContext();
      await this.ctx.audioWorklet.addModule("/audio/pcm-worklet.js");
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "nova-pcm");
      this.node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onPcm(e.data);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.data = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));
      source.connect(this.node);
      source.connect(this.analyser);
      if (this.ctx.state === "suspended") await this.ctx.resume();
    } catch (e) {
      this.stop();
      throw e instanceof MicError ? e : new MicError("error", `Audio processing could not start: ${(e as Error).message}`);
    }
  }

  /** Time-domain samples (0–255, 128 = silence) from the real microphone, or null when closed. */
  samples(): Uint8Array | null {
    if (!this.analyser || !this.data) return null;
    this.analyser.getByteTimeDomainData(this.data);
    return this.data;
  }

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

  stop(): void {
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close().catch(() => undefined);
    this.stream = null;
    this.ctx = null;
    this.node = null;
    this.analyser = null;
    this.data = null;
  }
}
