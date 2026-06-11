#!/usr/bin/env python3
"""Live smoke-test of Caliper's figure-quality fix.

Logs into caliper.morphmind.ai, runs a real DE plot prompt, downloads the
produced PNG and confirms it is a real, non-empty publication-quality figure.

Credentials are read from the environment (never hardcoded):
  CALIPER_EMAIL     (default: jie@morphmind.ai)
  CALIPER_WEB_PASSWORD   (required) — store in ~/.secrets/api_keys.env
"""
import json, os, time, struct, sys
import urllib.request as u, urllib.parse as up

BASE = os.environ.get("CALIPER_BASE", "https://caliper.morphmind.ai")
EMAIL = os.environ.get("CALIPER_EMAIL", "jie@morphmind.ai")
PW = os.environ.get("CALIPER_WEB_PASSWORD")
if not PW:
    sys.exit("CALIPER_WEB_PASSWORD not set — source it from ~/.secrets/api_keys.env")


def post(path, payload, cookie=None, timeout=200):
    h = {"Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    return u.urlopen(u.Request(BASE + path, data=json.dumps(payload).encode(), headers=h),
                     timeout=timeout)


def get(path, cookie=None, timeout=60):
    return u.urlopen(u.Request(BASE + path, headers={"Cookie": cookie} if cookie else {}),
                     timeout=timeout)


def png_dims(b):
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", b[16:24])
    return w, h


# 1) login
resp = post("/api/login", {"email": EMAIL, "password": PW}, timeout=30)
cookie = resp.headers.get("Set-Cookie", "").split(";")[0]
print("login:", "ok" if cookie else "FAILED")
if not cookie:
    sys.exit("login failed")

# 2) chat (SSE)
msg = ("Run differential expression between ON and OFF from the Salmon quant.sf files "
       "and show a clean bar plot of the top up/down-regulated genes.")
r = post("/api/chat", {"message": msg, "data_paths": []}, cookie=cookie, timeout=240)
figs, ans_text, jid, trust, decision = [], None, None, None, None
while True:
    raw = r.readline()
    if not raw:
        break
    ln = raw.decode(errors="replace").strip()
    if not ln.startswith("data: "):
        continue
    e = json.loads(ln[6:]); t = e.get("type")
    if t == "exec":
        print("  exec ok=", e.get("ok"))
    elif t == "status":
        print("  status:", e.get("text"))
    elif t == "job":
        jid = e.get("job_id"); print("  job:", jid)
    elif t == "trust":
        trust = e.get("trust")
    elif t == "decision":
        decision = e.get("decision")
        a = e.get("answer") or {}
        figs = (a.get("figures") if isinstance(a, dict) else None) or []
        ans_text = e.get("answer_text")
    elif t == "error":
        print("  ERROR:", e.get("message"))
    elif t == "done":
        break

# 3) poll detached job
if jid and not figs:
    print("polling job…")
    for _ in range(36):
        time.sleep(5)
        st = json.loads(get("/api/job/" + jid, cookie=cookie, timeout=40).read())
        if st.get("state") in ("done", "failed"):
            a = st.get("answer") or {}
            figs = (a.get("figures") if isinstance(a, dict) else None) or []
            ans_text = st.get("answer_text")
            print("job", st.get("state"))
            break

print("\ndecision:", decision, "| trust:", trust)
print("figures:", figs)
print("answer:", (ans_text or "")[:300])

# 4) fetch + validate
ok = False
for i, f in enumerate(figs):
    d = get("/api/artifact?path=" + up.quote(f), cookie=cookie, timeout=60).read()
    dims = png_dims(d)
    out = f"/tmp/live_fig_{i}.png"
    open(out, "wb").write(d)
    print(f"\nfetched {f}: {len(d)} bytes, dims={dims} -> {out}")
    if len(d) > 3000 and dims and dims[0] > 100 and dims[1] > 100:
        ok = True

print("\nVERDICT:", "PASS — real non-empty figure" if ok else "FAIL — empty/placeholder/no figure")
sys.exit(0 if ok else 1)
