"""Browser end-to-end check of NOVA against the REAL backend (no mocks). Developer tool, not needed to run NOVA.

Chromium's fake microphone plays a synthesized recording from backend/tests/fixtures/audio ("Hey Nova. … I want to
build an app that helps people check whether their plants are healthy using their phone camera."). The test turns on
the wake phrase with the local PocketSphinx transcriber and checks that the wake phrase, the transcript and the idea
workflow appear in the UI. It also checks themes, reduced motion, image attachments, the mobile layout, a blocked
microphone, and saves screenshots.

Requirements: the frontend built (npm run build), the backend virtualenv, and Playwright for Python with Chromium:
    pip install playwright && python -m playwright install chromium
Usage (from the NOVA folder):
    python scripts/dev/browser_e2e.py [--chrome /path/to/chrome] [--backend-python backend/.venv/bin/python] [--out shots]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

NOVA = Path(__file__).resolve().parents[2]
ARGS = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ARGS.add_argument("--chrome", default=os.environ.get("NOVA_E2E_CHROME"), help="Chromium executable (default: Playwright's)")
ARGS.add_argument("--backend-python", default=sys.executable, help="Python with the backend requirements installed")
ARGS.add_argument("--out", default=str(NOVA / "e2e-shots"), help="folder for screenshots and results.json")
ARGS.add_argument("--port", type=int, default=8010)
OPTS = ARGS.parse_args()
HERE = Path(tempfile.mkdtemp(prefix="nova-e2e-files-"))
SHOTS = Path(OPTS.out)
SHOTS.mkdir(parents=True, exist_ok=True)
PY = OPTS.backend_python
CHROME = OPTS.chrome
PORT = OPTS.port
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, str, str]] = []


def record(step: str, ok: bool | None, detail: str = "") -> None:
    status = "PASS" if ok else ("UNVERIFIED" if ok is None else "FAIL")
    RESULTS.append((step, status, detail))
    print(f"[{status:10}] {step}" + (f" — {detail}" if detail else ""), flush=True)


def start_backend(data: Path) -> tuple[subprocess.Popen[str], dict[str, str]]:
    env = {**os.environ, "NOVA_DATA_DIR": str(data), "NOVA_PORT": str(PORT), "NOVA_HOST": "127.0.0.1",
           "NOVA_LOG_LEVEL": "WARNING", "PYTHONUNBUFFERED": "1", "NOVA_OLLAMA_BASE_URL": "http://127.0.0.1:9"}
    proc = subprocess.Popen([PY, "-m", "app.main"], cwd=NOVA / "backend", env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    info: dict[str, str] = {}

    def drain() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            m = re.search(r"setup code: (\d+)", line)
            if m:
                info["code"] = m.group(1)
            if "Traceback" in line or "ERROR" in line:
                print("backend:", line.rstrip(), flush=True)

    threading.Thread(target=drain, daemon=True).start()
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=1) as r:  # noqa: S310 - fixed local http URL
                if r.status == 200:
                    break
        except OSError:
            time.sleep(0.2)
    for _ in range(50):
        if "code" in info:
            break
        time.sleep(0.1)
    return proc, info


def wait_until(fn, timeout: float = 15) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001,S110 - element may be re-rendering
            pass
        time.sleep(0.15)
    return False


def question(page) -> str:
    q = page.locator(".focus-question")
    return q.first.inner_text() if q.count() else ""


def setup_owner(page, code: str) -> None:
    page.goto(BASE)
    page.get_by_label(re.compile("Setup code")).fill(code)
    page.get_by_label(re.compile("^Username")).fill("farel")
    page.get_by_label(re.compile("^Password")).fill("correct-horse-battery")
    page.get_by_label(re.compile("Confirm password")).fill("correct-horse-battery")
    page.get_by_role("button", name="Create owner account").click()
    page.get_by_role("navigation", name="Main").wait_for()


def login(page) -> None:
    page.goto(BASE)
    page.get_by_label("Username").fill("farel")
    page.get_by_label("Password").fill("correct-horse-battery")
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("navigation", name="Main").wait_for()


def padded_fixture() -> Path:
    """The plant request followed by 30 s of silence, so the fake microphone does not repeat it."""
    src = NOVA / "backend" / "tests" / "fixtures" / "audio" / "hey_nova_plants.wav"
    out = HERE / "plants_e2e.wav"
    with wave.open(str(src), "rb") as r:
        params, frames = r.getparams(), r.readframes(r.getnframes())
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(frames + b"\x00" * (params.sampwidth * params.nchannels * params.framerate * 30))
    return out


def launch(p, args: list[str]):
    return p.chromium.launch(executable_path=CHROME, args=args) if CHROME else p.chromium.launch(args=args)


def main() -> int:
    data = Path(tempfile.mkdtemp(prefix="nova-e2e-"))
    proc, info = start_backend(data)
    console: list[str] = []
    try:
        if "code" not in info:
            record("backend starts and prints a setup code", False)
            return 1
        record("backend starts and prints a setup code", True)
        wav = padded_fixture()
        with sync_playwright() as p:
            browser = launch(p, [
                "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav}%noloop", "--autoplay-policy=no-user-gesture-required",
            ])
            ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="dark")
            page = ctx.new_page()
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
            page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
            setup_owner(page, info["code"])
            record("first-run setup and sign-in in the browser", True)

            # ---- themes ---------------------------------------------------------------------------------
            page.wait_for_timeout(1200)
            theme = page.evaluate("document.documentElement.dataset.theme")
            record("system theme follows the OS (dark)", theme == "dark", f"data-theme={theme}")
            page.screenshot(path=str(SHOTS / "command-dark.png"))
            page.emulate_media(color_scheme="light")
            page.wait_for_timeout(500)
            theme = page.evaluate("document.documentElement.dataset.theme")
            record("system theme follows the OS (light)", theme == "light", f"data-theme={theme}")
            page.screenshot(path=str(SHOTS / "command-light.png"))
            bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
            record("light theme paints light surfaces", "236, 239, 243" in bg or "rgb(236" in bg, bg)

            # ---- wake phrase with the local transcriber ----------------------------------------------------
            page.get_by_role("button", name=re.compile("Microphone off")).click()
            dialog = page.get_by_role("dialog")
            dialog.wait_for()
            dialog.get_by_text(re.compile("PocketSphinx")).first.click()
            dialog.get_by_role("button", name="Turn on the microphone").click()
            t0 = time.time()
            page.get_by_role("button", name=re.compile("Listening for “hey nova” on this computer")).wait_for(timeout=10000)
            record("wake phrase armed after consent (local detector)", True, f"{time.time() - t0:.1f}s")
            seen: set[str] = set()
            idea_text = ""
            deadline = time.time() + 40
            while time.time() < deadline:
                state = page.locator(".state-name").inner_text()
                seen.add(state)
                q = page.locator(".focus-question")
                if q.count() and "understood your idea" in q.first.inner_text():
                    idea_text = q.first.inner_text()
                    break
                page.wait_for_timeout(100)
            record("wake phrase detected from real audio", "Wake phrase heard" in seen or "Listening" in seen,
                   ", ".join(sorted(seen)))
            record("spoken idea transcribed locally and opened as an idea workflow", bool(idea_text), idea_text[:160])
            page.screenshot(path=str(SHOTS / "idea-intent-light.png"))
            audit = page.evaluate("""async () => (await (await fetch('/api/audit?action=voice')).json()).map(e => e.action)""")
            record("microphone start is audited (no audio or transcript)", "voice.stream_started" in audit, ",".join(audit))

            # Answer the intent question and the next one by typing, then check the one-question-at-a-time flow.
            if idea_text:
                page.get_by_role("button", name="Yes, that's right").click()
                wait_until(lambda: question(page) and "understood" not in question(page))
                q2 = question(page)
                record("next question asks about intended users", "use" in q2.lower() or "who" in q2.lower(), q2[:120])
                page.get_by_role("textbox", name="Your answer").fill("Home gardeners and hobbyists with houseplants")
                page.get_by_role("button", name="Send").click()
                wait_until(lambda: question(page) not in ("", q2))
                q3 = question(page)
                record("then asks which plants are in scope", "plant" in q3.lower(), q3[:140])
                page.screenshot(path=str(SHOTS / "idea-question-light.png"))

            # Turn the microphone off from the rail.
            page.get_by_role("button", name=re.compile("Turn the microphone off")).click()
            page.get_by_role("button", name=re.compile("Microphone off")).wait_for(timeout=5000)
            record("microphone turned off from the rail", True)

            # ---- other screens in both themes ---------------------------------------------------------------
            page.goto(f"{BASE}/settings?tab=voice")
            page.get_by_role("heading", name="Wake phrase").wait_for()
            page.wait_for_timeout(600)
            wake_box = page.get_by_role("checkbox", name=re.compile("^Listen for “hey nova”"))
            record("wake checkbox reflects the saved choice and is usable", (not wake_box.is_checked()) and wake_box.is_enabled(),
                   f"checked={wake_box.is_checked()} enabled={wake_box.is_enabled()}")
            page.screenshot(path=str(SHOTS / "settings-voice-light.png"), full_page=True)
            page.goto(f"{BASE}/settings?tab=capabilities")
            page.get_by_text("Image and screenshot understanding").wait_for()
            caps = page.locator(".cap").count()
            record("capabilities reported from live checks", caps >= 10, f"{caps} capabilities")
            page.screenshot(path=str(SHOTS / "settings-capabilities-light.png"), full_page=True)
            page.goto(f"{BASE}/settings?tab=appearance")
            page.get_by_text("Reduce motion").click()
            page.wait_for_timeout(300)
            motion = page.evaluate("document.documentElement.dataset.motion")
            record("reduced-motion preference applies", motion == "reduce", f"data-motion={motion}")
            page.get_by_text("Dark", exact=True).click()
            page.wait_for_timeout(300)
            theme = page.evaluate("document.documentElement.dataset.theme")
            record("explicit dark theme overrides the light OS setting", theme == "dark", f"data-theme={theme}")
            page.screenshot(path=str(SHOTS / "settings-appearance-dark.png"), full_page=True)
            page.goto(BASE)
            page.wait_for_timeout(800)
            page.screenshot(path=str(SHOTS / "command-idea-dark.png"))
            page.goto(f"{BASE}/chat")
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS / "chat-dark.png"))
            # Image attachment: upload, preview (blob: URL allowed by the CSP) and honest failure without a model.
            png = HERE / "leaf.png"
            png.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                "1f15c4890000000d49444154789c6360f8cf000000020001e221bc330000000049454e44ae426082"))
            page.locator("input[type=file]").set_input_files(str(png))
            page.wait_for_timeout(1200)
            thumbs = page.locator(".attach-strip img").count()
            uploading = page.locator(".attach-strip .spinner").count()
            errors_shown = page.locator(".attach-error").count()
            natural = page.evaluate(
                "(() => { const i = document.querySelector('.attach-strip img'); return i ? i.naturalWidth : -1; })()")
            record("image attachment uploads and previews", thumbs == 1 and uploading == 0 and errors_shown == 0 and natural == 1,
                   f"thumbs={thumbs} uploading={uploading} errors={errors_shown} naturalWidth={natural}")
            page.get_by_label("Message NOVA").fill("What do you see on this leaf?")
            page.get_by_role("button", name="Send").click()
            page.wait_for_timeout(2500)
            sent_img = page.locator(".msg-images img").count()
            err = page.locator(".msg-nova .notice").first.inner_text() if page.locator(".msg-nova .notice").count() else ""
            record("message with image is sent; model failure is reported, not faked", sent_img == 1 and bool(err),
                   f"images={sent_img} error={err[:100]}")
            page.screenshot(path=str(SHOTS / "chat-image-dark.png"))
            # Persistence: reload keeps the theme (server + local cache)
            page.reload()
            page.wait_for_timeout(800)
            theme = page.evaluate("document.documentElement.dataset.theme")
            record("theme persists across reloads", theme == "dark", f"data-theme={theme}")

            # Mobile layout
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, color_scheme="light", device_scale_factor=2)
            mp = mobile.new_page()
            login(mp)
            mp.wait_for_timeout(1000)
            overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            record("mobile command center has no horizontal scroll", overflow <= 0, f"overflow={overflow}px")
            mp.screenshot(path=str(SHOTS / "mobile-command.png"))
            mp.get_by_role("button", name="Open navigation").click()
            mp.wait_for_timeout(300)
            mp.screenshot(path=str(SHOTS / "mobile-nav.png"))
            mobile.close()
            ctx.close()
            browser.close()

            # ---- microphone permission denied -----------------------------------------------------------
            denied = launch(p, ["--use-fake-device-for-media-stream", "--deny-permission-prompts"])
            dctx = denied.new_context(viewport={"width": 1280, "height": 860})
            dp = dctx.new_page()
            login(dp)
            dp.wait_for_timeout(1500)  # let the owner's saved preferences load (consent was given earlier)
            dp.get_by_role("button", name=re.compile("Microphone off")).click()
            dp.wait_for_timeout(500)
            if dp.get_by_role("dialog").count():  # preferences not loaded yet: consent again, same result expected
                dp.get_by_role("dialog").get_by_text(re.compile("PocketSphinx")).first.click()
                dp.get_by_role("button", name="Turn on the microphone").click()
            try:
                dp.get_by_role("button", name=re.compile("Microphone blocked|Microphone problem")).wait_for(timeout=10000)
                msg = dp.locator(".notice").first.inner_text()
                record("blocked microphone is explained with a way to recover",
                       "blocked" in msg.lower() or "allow" in msg.lower(), msg[:140])
            except Exception as exc:  # noqa: BLE001
                record("blocked microphone is explained with a way to recover", False, str(exc)[:160])
            dp.screenshot(path=str(SHOTS / "mic-denied.png"))
            denied.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    errors = [c for c in console if c.startswith(("error", "pageerror"))]
    record("no page errors in the browser console", not errors, "; ".join(errors)[:300])
    (SHOTS / "results.json").write_text(json.dumps(RESULTS, indent=1))
    print(f"Screenshots and results: {SHOTS}")
    return 0 if all(s != "FAIL" for _, s, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
