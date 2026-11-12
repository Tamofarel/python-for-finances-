// NOVA microphone worklet: converts the browser's audio (usually 48 kHz float) into 16 kHz mono 16-bit PCM and posts
// it to the page in ~100 ms chunks. Nothing is stored; the page sends the chunks to NOVA's local backend only while
// the microphone is on.
class PcmWorklet extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.pos = 0;
    this.acc = 0;
    this.count = 0;
    this.out = new Int16Array(1600);
    this.n = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i++) {
      // Box-filter downsampling: average the input samples that fall into each 16 kHz output sample.
      this.acc += channel[i];
      this.count += 1;
      this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.acc / this.count));
        this.out[this.n++] = v < 0 ? v * 0x8000 : v * 0x7fff;
        this.acc = 0;
        this.count = 0;
        if (this.n === this.out.length) {
          const chunk = this.out.slice(0);
          this.port.postMessage(chunk.buffer, [chunk.buffer]);
          this.n = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("nova-pcm", PcmWorklet);
