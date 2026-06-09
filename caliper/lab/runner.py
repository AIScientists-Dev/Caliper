"""Caliper lab-side job runner — runs inside the lab pack (container or conda env).

Executes the dispatched `step.py` detached, tees its output to `log`, parses
`CALIPER_PROGRESS:{frac,eta}` / `CALIPER_RESULT:{...}` lines, and keeps `status.json`
current so the control plane can poll progress. Deliberately trivial: it holds NO
trust/calibration logic — that all lives in the control plane. Safe to be public.
"""
import json
import os
import re
import subprocess
import sys
import time

JOB = os.environ.get("CALIPER_JOB_DIR") or os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(JOB, "status.json")
LOG = os.path.join(JOB, "log")


def load():
    try:
        return json.load(open(STATUS))
    except Exception:
        return {}


def save(**kw):
    s = load()
    s.update(kw)
    s["updated"] = int(time.time())
    json.dump(s, open(STATUS, "w"))


def main():
    prog = re.compile(r"CALIPER_PROGRESS:(\{.*\})")
    resre = re.compile(r"CALIPER_RESULT:(\{.*\})")
    save(state="running")
    result = None
    with open(LOG, "w") as lg:
        p = subprocess.Popen([sys.executable, "step.py"], cwd=JOB, env=dict(os.environ),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in p.stdout:
            lg.write(line)
            lg.flush()
            m = prog.search(line)
            if m:
                try:
                    d = json.loads(m.group(1))
                    save(progress=float(d.get("frac", 0)), eta_seconds=d.get("eta"))
                except Exception:
                    pass
            m = resre.search(line)
            if m:
                try:
                    result = json.loads(m.group(1))
                except Exception:
                    pass
        rc = p.wait()
    save(state="done" if rc == 0 else "failed",
         progress=1.0 if rc == 0 else load().get("progress", 0.0),
         result=result, returncode=rc)


if __name__ == "__main__":
    main()
