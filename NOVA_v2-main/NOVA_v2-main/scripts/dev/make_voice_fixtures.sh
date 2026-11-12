#!/usr/bin/env bash
# Regenerates the speech fixtures used by backend/tests/test_voice.py and the browser voice test.
# Requires Festival with the HTS voice "cmu_us_slt_arctic_hts" and ffmpeg (Debian/Ubuntu:
# apt-get install festival festvox-us-slt-hts ffmpeg). These are test tools only; NOVA does not need them.
# Output: 16 kHz mono PCM16 WAV files. A short silence is added before and after each utterance.
set -euo pipefail
OUT="$(cd "$(dirname "$0")/../.." && pwd)/backend/tests/fixtures/audio"
mkdir -p "$OUT"
say() {  # name, text...
  local name="$1"; shift
  local tmp; tmp="$(mktemp -d)"
  local i=0
  for part in "$@"; do
    if [[ "$part" == "<pause>" ]]; then
      ffmpeg -loglevel error -f lavfi -i anullsrc=r=16000:cl=mono -t 0.8 -ar 16000 -ac 1 -sample_fmt s16 "$tmp/$i.wav"
    else
      echo "$part" | text2wave -eval "(voice_cmu_us_slt_arctic_hts)" -o "$tmp/raw$i.wav"
      ffmpeg -loglevel error -i "$tmp/raw$i.wav" -ar 16000 -ac 1 -sample_fmt s16 "$tmp/$i.wav"
    fi
    echo "file '$tmp/$i.wav'" >> "$tmp/list.txt"
    i=$((i + 1))
  done
  ffmpeg -loglevel error -f concat -safe 0 -i "$tmp/list.txt" -af "adelay=500|500,apad=pad_dur=1.2" \
    -ar 16000 -ac 1 -sample_fmt s16 -y "$OUT/$name.wav"
  rm -rf "$tmp"
}
say hey_nova_weather "Hey Nova." "<pause>" "What is the weather like today?"
say hey_nova_plants "Hey Nova." "<pause>" "I want to build an app that helps people check whether their plants are healthy using their phone camera."
say nova_projects "Nova, open my projects."
say neg_garden "Please remind me to water the plants in the garden tomorrow morning."
say neg_supernova "The supernova was visible for weeks, and I know a lot of people in Nevada who saw it."
say neg_notes "We should go over the notes and then have some coffee."
echo "Wrote fixtures to $OUT"
