#!/usr/bin/env python3
"""Run the plant-health example through NOVA's generic idea-to-product workflow (no special-cased code).

    # against your running NOVA (prompts for the password):
    python scripts/demo_plant_health.py --url http://127.0.0.1:8000 --user farel
    # or in a throw-away backend + data folder:
    python scripts/demo_plant_health.py --temp

Answers come from docs/examples/plant-health-answers.json, keyed by question topic. They are example owner
decisions; edit the file to try other answers. Questions NOVA asks about other topics are skipped.
Stages run with the AI provider when it answers a live check, otherwise as drafts assembled from the answers.
The proposal is NOT approved unless you pass --approve (approval is your decision).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--user")
    ap.add_argument("--temp", action="store_true", help="start a temporary backend with its own data folder")
    ap.add_argument("--answers", default=str(ROOT / "docs" / "examples" / "plant-health-answers.json"))
    ap.add_argument("--mode", default="auto", choices=["auto", "ai", "template"])
    ap.add_argument("--approve", action="store_true")
    args = ap.parse_args()
    spec = json.loads(Path(args.answers).read_text(encoding="utf-8"))

    backend = None
    if args.temp:
        from smoke_test import Backend  # same helper the smoke test uses
        backend = Backend(Path(tempfile.mkdtemp(prefix="nova-demo-")), 8781)
        backend.start()
        args.url = backend.url
    c = httpx.Client(base_url=args.url, headers={"X-NOVA-Client": "web"}, timeout=900)
    try:
        if args.temp:
            import time
            for _ in range(50):
                if backend.setup_code:
                    break
                time.sleep(0.1)
            r = c.post("/api/auth/setup",
                       json={"username": "demo", "password": "demo-password-123", "setup_code": backend.setup_code})
        else:
            user = args.user or input("NOVA username: ")
            r = c.post("/api/auth/login", json={"username": user, "password": getpass.getpass("Password: ")})
        r.raise_for_status()
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]

        wf = c.post("/api/workflows",
                    json={"idea": spec["idea"], "template": spec["template"], "title": spec.get("title")}).json()
        print(f"Workflow #{wf['id']} created in project #{wf['project_id']} ({wf['template_label']}).")
        for _ in range(20):
            q = next((q for q in wf["questions"] if q["status"] == "open"), None)
            if q is None:
                break
            ans = spec["answers"].get(q["topic"])
            print(f"\nQ ({q['source']}, {q['topic']}): {q['question']}")
            if ans:
                print(f"A: {ans}")
                wf = c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/answer", params={"mode": args.mode},
                            json={"answer": ans}).json()
            else:
                print("A: (no example answer for this topic, skipped)")
                wf = c.post(f"/api/workflows/{wf['id']}/questions/{q['id']}/skip", params={"mode": args.mode}).json()
        wf = c.post(f"/api/workflows/{wf['id']}/clarify/complete").json()
        while wf["next_stage"]:
            stage = wf["next_stage"]
            print(f"\nRunning stage: {stage} …", flush=True)
            wf = c.post(f"/api/workflows/{wf['id']}/continue", params={"wait": True}, json={"mode": args.mode}).json()
            st = next(s for s in wf["stages"] if s["id"] == stage)
            print(f"  {st['status']} ({st.get('mode') or '-'}){' — ' + st['error'] if st.get('error') else ''}")
            if st["status"] not in ("done", "skipped"):
                return 1
        print("\nResearch:")
        for f in wf["research"]:
            print(f"  [{f['status']}] {f['question']}")
        print("\nDocuments (editable in the project's Design tab):")
        for s in wf["stages"]:
            for a in s["artifacts"]:
                print(f"  #{a['id']} {a['title']} — {'AI draft' if a['created_by'] == 'ai' else 'assembled from answers'}")
        print(f"\nStatus: {wf['status']}. Proposal artifact #{wf['proposal']['artifact_id'] if wf['proposal'] else '-'}")
        if args.approve and wf["proposal"]:
            wf = c.post(f"/api/workflows/{wf['id']}/decision",
                        json={"decision": "approve", "proposal_sha256": wf["proposal"]["sha256"]}).json()
            print(f"Approved. {len(wf['work_items'])} tasks created:")
            for i in wf["work_items"]:
                print(f"  - {i['title']}")
        else:
            print(f"Open {args.url}/workflows/{wf['id']} to review and decide.")
        return 0
    finally:
        if backend:
            backend.stop()


if __name__ == "__main__":
    sys.exit(main())
