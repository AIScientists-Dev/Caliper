"""Caliper web app — a chat UI over the agent. Config-driven; no site specifics.

Everything is configured by environment variables, so the SAME code runs locally
(for testing) and on a lab server (deployment):

  CALIPER_WORKSPACE   confined write directory (all outputs/temp live here)
  CALIPER_DATA_ROOT   read-only root the data browser/search is limited to
  CALIPER_PACK        domain pack to load (default: bio)
  CALIPER_PROVIDER    llm provider (anthropic | openai | mock)
  ANTHROPIC_API_KEY   (server-side only; never sent to the browser)
  CALIPER_WEB_PASSWORD  if set, the UI requires this password (else dev-open)

Run:  uvicorn caliper.web.server:app   (or `python -m caliper.web.server`)
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
from collections import deque
from typing import List

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, Response,
                               StreamingResponse)

from ..core.agent import CaliperAgent
from ..core.executor import Executor
from ..core.remote_executor import RemoteExecutor
from ..core.registry import load_pack
from ..core import logstore
from ..config import get_workspace
from ..llm import make_llm
from ..trust.judge import Judge

HERE = os.path.dirname(__file__)
DATA_ROOT = os.path.realpath(os.environ.get("CALIPER_DATA_ROOT", os.getcwd()))
WORKSPACE = os.path.realpath(get_workspace() or os.path.join(os.getcwd(), "caliper_workspace"))
PACK = os.environ.get("CALIPER_PACK", "bio")
_SESSIONS: dict = {}        # token -> email (who is logged in)
_SESSIONS_FILE = os.path.join(WORKSPACE, ".auth_sessions.json")


def _load_sessions():
    """Keep users signed in across restarts/deploys (tokens persist to disk)."""
    try:
        with open(_SESSIONS_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            _SESSIONS.update(data)
    except (OSError, ValueError):
        pass


def _save_sessions():
    try:
        with open(_SESSIONS_FILE, "w") as f:
            json.dump(_SESSIONS, f)
        os.chmod(_SESSIONS_FILE, 0o600)
    except OSError:
        pass


_load_sessions()
_HISTORY: dict = {}         # session_id -> {"title":..., "ts":..., "messages":[...]}
ACCESS_LOG = deque(maxlen=1000)   # recent login events {ts, ip, email, event, ok}


def _load_access_log():
    """Reload persisted login events so the access log survives restarts/deploys."""
    try:
        with open(os.path.join(WORKSPACE, "access.log")) as f:
            for line in f.read().splitlines()[-ACCESS_LOG.maxlen:]:
                line = line.strip()
                if line:
                    ACCESS_LOG.append(json.loads(line))
    except (OSError, ValueError):
        pass


_load_access_log()
_FAILS: dict = {}           # ip -> (count, last_ts)   (brute-force lockout)
_LOCK_AFTER, _LOCK_WINDOW = 5, 300   # >=5 fails within 300s -> locked for 300s

LAB_NAME = os.environ.get("CALIPER_LAB_NAME", "Chong's team")
try:
    # CALIPER_USERS: JSON {email: password}. Use a DEDICATED app password per user —
    # never anyone's institutional/UMN password.
    _USERS = json.loads(os.environ.get("CALIPER_USERS", "") or "{}")
except json.JSONDecodeError:
    _USERS = {}
_SINGLE_PW = os.environ.get("CALIPER_WEB_PASSWORD")  # legacy single-password fallback

try:  # restore chat history from disk (no DB; survives restarts)
    _HISTORY.update(logstore.load_sessions(WORKSPACE))
except Exception:  # noqa: BLE001
    pass

# Where the lab's data lives (for the remote data browser); next to the remote workspace.
REMOTE_DATA_ROOT = (os.environ.get("CALIPER_REMOTE_DATA_ROOT")
                    or (os.path.dirname(os.environ["CALIPER_REMOTE_WORKSPACE"].rstrip("/"))
                        if os.environ.get("CALIPER_REMOTE_WORKSPACE") else ""))

_JOBS: dict = {}   # job_id -> {task, session, finalized, final}  (durable: survives restart)


def _jobs_dir():
    return os.path.join(WORKSPACE, ".jobs")


def _save_job(jid):
    try:
        os.makedirs(_jobs_dir(), exist_ok=True)
        json.dump(_JOBS[jid], open(os.path.join(_jobs_dir(), jid + ".json"), "w"))
    except Exception:  # noqa: BLE001
        pass


def _register_job(jid, task, sid):
    _JOBS[jid] = {"task": task, "session": sid, "finalized": False, "final": None}
    _save_job(jid)


try:
    if os.path.isdir(_jobs_dir()):
        for _fn in os.listdir(_jobs_dir()):
            if _fn.endswith(".json"):
                _JOBS[_fn[:-5]] = json.load(open(os.path.join(_jobs_dir(), _fn)))
except Exception:  # noqa: BLE001
    pass

app = FastAPI(title="Caliper")


def _auth_configured() -> bool:
    return bool(_USERS or _SINGLE_PW)


def _check_creds(email: str, password: str) -> bool:
    if _USERS:
        exp = _USERS.get((email or "").strip().lower())
        return exp is not None and secrets.compare_digest(str(password), str(exp))
    if _SINGLE_PW:
        return secrets.compare_digest(str(password), _SINGLE_PW)
    return False


def client_ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def log_access(ip: str, event: str, ok: bool, email: str = ""):
    entry = {"ts": int(time.time()), "ip": ip, "email": email, "event": event, "ok": ok}
    ACCESS_LOG.append(entry)
    try:
        with open(os.path.join(WORKSPACE, "access.log"), "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _locked(ip: str) -> bool:
    cnt, last = _FAILS.get(ip, (0, 0))
    return cnt >= _LOCK_AFTER and (time.time() - last) < _LOCK_WINDOW


# --- auth ------------------------------------------------------------------------
def require_auth(request: Request):
    if not _auth_configured():
        return  # dev-open
    if request.cookies.get("caliper_session") not in _SESSIONS:
        raise HTTPException(status_code=401, detail="login required")


@app.post("/api/login")
async def login(request: Request):
    ip = client_ip(request)
    if _locked(ip):
        log_access(ip, "login-locked", False)
        return JSONResponse({"ok": False, "error": "too many attempts; try later"}, status_code=429)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if _check_creds(email, body.get("password", "")):
        tok = secrets.token_urlsafe(24)
        _SESSIONS[tok] = email or "user"
        _save_sessions()
        _FAILS.pop(ip, None)
        log_access(ip, "login", True, email)
        secure = request.headers.get("x-forwarded-proto", "http") == "https"
        r = JSONResponse({"ok": True})
        r.set_cookie("caliper_session", tok, httponly=True, samesite="lax", secure=secure)
        return r
    cnt, _ = _FAILS.get(ip, (0, 0))
    _FAILS[ip] = (cnt + 1, time.time())
    log_access(ip, "login", False, email)
    return JSONResponse({"ok": False}, status_code=401)


@app.post("/api/logout")
def logout(request: Request):
    tok = request.cookies.get("caliper_session")
    if tok:
        who = _SESSIONS.pop(tok, "")
        _save_sessions()
        log_access(client_ip(request), "logout", True, who)
    r = JSONResponse({"ok": True})
    r.delete_cookie("caliper_session")
    return r


@app.get("/api/branding")
def branding():
    return {"auth": _auth_configured()}  # agnostic: no lab identity revealed pre-login


@app.get("/api/access-log")
def access_log(_=Depends(require_auth)):
    return list(ACCESS_LOG)[-300:][::-1]


@app.get("/api/whoami")
def whoami(request: Request, _=Depends(require_auth)):
    email = _SESSIONS.get(request.cookies.get("caliper_session"), "")
    return {"data_root": DATA_ROOT, "pack": PACK, "lab_name": LAB_NAME,
            "email": email, "auth_required": _auth_configured(),
            "remote": os.environ.get("CALIPER_REMOTE_HOST") or "this server"}


# --- read-only data browser/search (confined to DATA_ROOT) -----------------------
def _safe(path: str) -> str:
    p = os.path.realpath(os.path.join(DATA_ROOT, path or ""))
    if p != DATA_ROOT and not p.startswith(DATA_ROOT + os.sep):
        raise HTTPException(status_code=400, detail="outside data root")
    return p


@app.get("/api/browse")
def browse(path: str = "", _=Depends(require_auth)):
    ex = agent().executor
    if isinstance(ex, RemoteExecutor) and REMOTE_DATA_ROOT:  # browse the LAB filesystem
        root = REMOTE_DATA_ROOT
        full = os.path.normpath(os.path.join(root, path)) if path else root
        if not (full == root or full.startswith(root + "/")):
            full = root
        try:
            entries = ex.listdir(full)
        except Exception:
            raise HTTPException(status_code=404, detail="cannot list")
        return {"path": "" if full == root else os.path.relpath(full, root),
                "root": root, "entries": entries}
    p = _safe(path)
    if not os.path.isdir(p):
        raise HTTPException(status_code=404, detail="not a directory")
    entries = []
    for n in sorted(os.listdir(p)):
        fp = os.path.join(p, n)
        try:
            entries.append({"name": n, "dir": os.path.isdir(fp),
                            "size": os.path.getsize(fp) if os.path.isfile(fp) else None})
        except OSError:
            continue
    return {"path": os.path.relpath(p, DATA_ROOT), "root": DATA_ROOT, "entries": entries}


@app.get("/api/search")
def search(q: str, _=Depends(require_auth), limit: int = 100):
    ex = agent().executor
    if isinstance(ex, RemoteExecutor) and REMOTE_DATA_ROOT:  # search the LAB filesystem
        hits = [os.path.relpath(h, REMOTE_DATA_ROOT) for h in ex.find(REMOTE_DATA_ROOT, q, limit)]
        return {"hits": hits, "truncated": len(hits) >= limit}
    q = q.lower()
    hits = []
    for root, dirs, files in os.walk(DATA_ROOT):
        for n in files:
            if q in n.lower():
                hits.append(os.path.relpath(os.path.join(root, n), DATA_ROOT))
                if len(hits) >= limit:
                    return {"hits": hits, "truncated": True}
    return {"hits": hits, "truncated": False}


# --- agent (built once) ----------------------------------------------------------
def _build_agent() -> CaliperAgent:
    pack = load_pack(PACK)
    llm = make_llm()
    host = os.environ.get("CALIPER_REMOTE_HOST")
    if host:  # dispatch compute to the lab server
        ex = RemoteExecutor(
            host=host,
            user=os.environ.get("CALIPER_REMOTE_USER", "guest"),
            key_filename=os.environ.get("CALIPER_REMOTE_KEY") or None,
            password=os.environ.get("CALIPER_REMOTE_PASSWORD") or None,
            workspace=os.environ.get("CALIPER_REMOTE_WORKSPACE")
                      or os.environ.get("CALIPER_WORKSPACE", "."),
            python=os.environ.get("CALIPER_REMOTE_PYTHON", "python3"),
            path_prepend=os.environ.get("CALIPER_REMOTE_PATH", ""),
            bwrap=os.environ.get("CALIPER_REMOTE_BWRAP", ""),
        )
        # the lab data lives next to (the parent of) the remote workspace by default
        data_root = (os.environ.get("CALIPER_REMOTE_DATA_ROOT")
                     or os.path.dirname(ex.workspace.rstrip("/")) or ex.workspace)
    else:
        ex = Executor()
        data_root = DATA_ROOT
    from ..trust.feedback import FeedbackStore
    feedback = FeedbackStore(os.path.join(WORKSPACE, "feedback.jsonl"))
    return CaliperAgent(pack=pack, llm=llm, judge=Judge(llm), executor=ex,
                        feedback=feedback, data_root=data_root)


_AGENT = None


def _persist(sid: str, sess: dict, events: list):
    """Save chat history + experience to files (EC2), and mirror to the lab server."""
    try:
        logstore.save_session(WORKSPACE, sid, sess)
        exps = [e for e in events if e.get("type") == "experience"]
        for e in exps:
            logstore.append_experience(WORKSPACE, {"session": sid, **e})
        ex = agent().executor
        if isinstance(ex, RemoteExecutor):
            ex.write_workspace_file(f"sessions/{sid}.log", logstore.session_as_jsonl(sess))
            if exps:
                ex.write_workspace_file("experience/log.jsonl",
                                        "".join(json.dumps({"session": sid, **e}) + "\n" for e in exps),
                                        append=True)
    except Exception:  # noqa: BLE001  -- logging must never break a chat
        pass


def agent():
    global _AGENT
    if _AGENT is None:
        _AGENT = _build_agent()
    return _AGENT


# --- chat (streamed) -------------------------------------------------------------
@app.post("/api/chat")
async def chat(request: Request, _=Depends(require_auth)):
    body = await request.json()
    message = body.get("message", "")
    data_paths = body.get("data_paths", []) or []
    sid = body.get("session_id") or secrets.token_urlsafe(8)
    _remote = isinstance(agent().executor, RemoteExecutor) and REMOTE_DATA_ROOT
    def _resolve(p):
        if os.path.isabs(p):
            return p
        return os.path.join(REMOTE_DATA_ROOT, p) if _remote else _safe(p)
    data_files = [{"path": _resolve(p), "label": os.path.basename(p)} for p in data_paths]

    sess = _HISTORY.setdefault(sid, {"title": message[:60] or "New chat", "ts": int(time.time()),
                                     "messages": []})
    sess["messages"].append({"role": "user", "content": message})

    q: "queue.Queue" = queue.Queue()

    def worker():
        events = []
        try:
            allow_jobs = isinstance(agent().executor, RemoteExecutor)
            result = agent().run(message, data_files, allow_jobs=allow_jobs,
                                 on_event=lambda e: (events.append(e), q.put(e)))
            sess["messages"].append({"role": "assistant", "events": events,
                                     "answer": result.answer, "trust": result.trust,
                                     "decision": result.decision})
            if result.decision == "job" and result.provenance_path:
                _register_job(result.provenance_path, message, sid)  # long job -> poll to finish
            _persist(sid, sess, events)
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            q.put({"type": "done", "session_id": sid})
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
        while True:
            e = q.get()
            if e is None:
                break
            yield f"data: {json.dumps(e)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/job/{jid}")
def job_poll(jid: str, _=Depends(require_auth)):
    """Poll a detached job: state/progress/eta + log tail; finalizes the answer on done."""
    ex = agent().executor
    if not hasattr(ex, "job_status"):
        raise HTTPException(status_code=404)
    st = ex.job_status(jid)
    meta = _JOBS.get(jid)
    if meta and not meta.get("finalized") and st.get("state") in ("done", "failed"):
        res = agent().finalize(meta["task"], st.get("log_tail", ""), st.get("result"))
        meta["final"] = {"answer_text": res.answer_text, "answer": res.answer,
                         "trust": res.trust, "accepted": res.accepted, "decision": res.decision,
                         "calibrated": agent().feedback is not None and len(agent().feedback) > 0}
        meta["finalized"] = True
        _save_job(jid)
    if meta and meta.get("final"):
        st.update(meta["final"])
    return st


@app.get("/api/artifact")
def artifact(path: str, _=Depends(require_auth)):
    """Serve a figure/file produced by a run — confined to the workspace (local or lab)."""
    ex = agent().executor
    if isinstance(ex, RemoteExecutor):  # figure lives on the lab; fetch it over SSH
        rp = os.path.normpath(path)
        if not rp.startswith(ex.workspace.rstrip("/") + "/"):
            raise HTTPException(status_code=400, detail="outside workspace")
        try:
            data = ex.read_file(rp)
        except Exception:
            raise HTTPException(status_code=404)
        media = "image/png" if rp.endswith(".png") else "application/octet-stream"
        return Response(content=data, media_type=media)
    p = os.path.realpath(path)
    if p != WORKSPACE and not p.startswith(WORKSPACE + os.sep):
        raise HTTPException(status_code=400, detail="outside workspace")
    if not os.path.isfile(p):
        raise HTTPException(status_code=404)
    return FileResponse(p)


@app.get("/api/sessions")
def sessions(_=Depends(require_auth)):
    return [{"id": k, "title": v["title"], "ts": v["ts"]}
            for k, v in sorted(_HISTORY.items(), key=lambda kv: -kv[1]["ts"])]


@app.get("/api/session/{sid}")
def session(sid: str, _=Depends(require_auth)):
    if sid not in _HISTORY:
        raise HTTPException(status_code=404)
    return _HISTORY[sid]


@app.delete("/api/session/{sid}")
def delete_session(sid: str, _=Depends(require_auth)):
    _HISTORY.pop(sid, None)
    logstore.delete_session(WORKSPACE, sid)
    return {"ok": True}


@app.post("/api/feedback")
async def feedback(request: Request, _=Depends(require_auth)):
    """The mutualistic loop: a 👍/👎 on a result teaches the trust gate."""
    body = await request.json()
    try:
        agent().feedback.add(float(body.get("trust", 0.0)), bool(body.get("correct")),
                             meta={"session": body.get("session_id"),
                                   "comment": (body.get("comment") or "")[:2000]})
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "n_feedback": len(agent().feedback)}


# --- frontend --------------------------------------------------------------------
_FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 105 80"><g fill="#1c1a15">'
    '<path d="M20.5751 29.3744C26.4732 29.3744 31.2546 24.5896 31.2546 18.6872C31.2546 12.7848 26.4732 8 20.5751 8C14.6769 8 9.89551 12.7848 9.89551 18.6872C9.89551 24.5896 14.6769 29.3744 20.5751 29.3744Z"/>'
    '<path d="M41.8207 50.6356C47.7188 50.6356 52.5002 45.8508 52.5002 39.9484C52.5002 34.046 47.7188 29.2612 41.8207 29.2612C35.9225 29.2612 31.1411 34.046 31.1411 39.9484C31.1411 45.8508 35.9225 50.6356 41.8207 50.6356Z"/>'
    '<path d="M20.5751 71.9999C26.4732 71.9999 31.2546 67.2151 31.2546 61.3127C31.2546 55.4103 26.4732 50.6255 20.5751 50.6255C14.6769 50.6255 9.89551 55.4103 9.89551 61.3127C9.89551 67.2151 14.6769 71.9999 20.5751 71.9999Z"/>'
    '<path d="M73.8586 39.9517V50.6389H84.5382C78.6304 50.6389 73.8586 45.8637 73.8586 39.9517Z"/>'
    '<path d="M84.4244 50.6255H73.7449V61.3127C73.7449 55.4006 78.5166 50.6255 84.4244 50.6255Z"/>'
    '<path d="M84.4246 50.6389H95.1041V39.9517C95.1041 45.8637 90.3324 50.6389 84.4246 50.6389Z"/>'
    '<path d="M84.425 50.6255C90.3329 50.6255 95.1046 55.4006 95.1046 61.3127V50.6255H84.425Z"/>'
    '<path d="M31.2539 18.6885V29.3757H41.9335C36.0256 29.3757 31.2539 24.6005 31.2539 18.6885Z"/>'
    '<path d="M20.5747 29.2556H31.2543V18.5684C31.2543 24.4804 26.4826 29.2556 20.5747 29.2556Z"/>'
    '<path d="M41.8202 29.2612H31.1406V39.9484C31.1406 34.0364 35.9123 29.2612 41.8202 29.2612Z"/>'
    '<path d="M20.5747 29.2612C26.4826 29.2612 31.2543 34.0364 31.2543 39.9484V29.2612H20.5747Z"/>'
    '<path d="M63.1796 29.3744C69.0777 29.3744 73.8591 24.5896 73.8591 18.6872C73.8591 12.7848 69.0777 8 63.1796 8C57.2814 8 52.5 12.7848 52.5 18.6872C52.5 24.5896 57.2814 29.3744 63.1796 29.3744Z"/>'
    '<path d="M84.4244 50.6356C90.3226 50.6356 95.104 45.8508 95.104 39.9484C95.104 34.046 90.3226 29.2612 84.4244 29.2612C78.5263 29.2612 73.7449 34.046 73.7449 39.9484C73.7449 45.8508 78.5263 50.6356 84.4244 50.6356Z"/>'
    '<path d="M84.4244 71.9999C90.3226 71.9999 95.104 67.2151 95.104 61.3127C95.104 55.4103 90.3226 50.6255 84.4244 50.6255C78.5263 50.6255 73.7449 55.4103 73.7449 61.3127C73.7449 67.2151 78.5263 71.9999 84.4244 71.9999Z"/>'
    '<path d="M73.8589 18.6885V29.3757H84.5384C78.6306 29.3757 73.8589 24.6005 73.8589 18.6885Z"/>'
    '<path d="M63.1797 29.2556H73.8593V18.5684C73.8593 24.4804 69.0875 29.2556 63.1797 29.2556Z"/>'
    '<path d="M84.4247 29.2612H73.7451V39.9484C73.7451 34.0364 78.5168 29.2612 84.4247 29.2612Z"/>'
    '<path d="M63.1797 29.2612C69.0875 29.2612 73.8593 34.0364 73.8593 39.9484V29.2612H63.1797Z"/>'
    '</g></svg>')


@app.get("/favicon.svg")
def favicon():
    return Response(content=_FAVICON, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def home():
    # The marketing landing now lives on the homepage (morphmind.ai/products/caliper);
    # this host goes straight to the secure workspace (login screen).
    with open(os.path.join(HERE, "static", "index.html")) as f:
        return f.read()


@app.get("/app", response_class=HTMLResponse)
def app_page():
    with open(os.path.join(HERE, "static", "index.html")) as f:
        return f.read()


def run():  # console entry / `python -m caliper.web.server`
    import uvicorn
    uvicorn.run(app, host=os.environ.get("CALIPER_WEB_HOST", "127.0.0.1"),
                port=int(os.environ.get("CALIPER_WEB_PORT", "8000")))


if __name__ == "__main__":
    run()
