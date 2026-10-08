"""`jobradar inbox` - the tracker as a small window on your computer.

A tiny local web server (127.0.0.1 only, stdlib) plus a browser window in
"app" mode (Edge / Chrome without tabs and address bar), so it feels like a
pop-up. The page shows the funnel (2 / 7 / 30 days), a calendar of the last
week's jobs and one-key actions. Changes go straight into data/jobradar.db.

Safety: binds to 127.0.0.1, checks the Host header (DNS rebinding) and needs a
per-process token on every write; the token is only inside the page, which
other sites cannot read. The page shows names of your contacts, so it stays
local - never publish it.

The server stops by itself a few minutes after the window is closed.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jobradar.browser import any_chromium, chrome_exe
from jobradar.store import Store
from jobradar.tracker import STATE_HE, STATES, Tracker

log = logging.getLogger(__name__)
_PAGE = Path(__file__).with_name("inbox.html")


def _browser_app_cmd(url: str, width: int, height: int) -> list[str] | None:
    """Chrome (else Edge) in --app mode (a bare window, like a pop-up)."""
    exe = any_chromium()
    return [str(exe), f"--app={url}", f"--window-size={width},{height}"] if exe else None


def open_window(url: str, width: int = 1280, height: int = 880) -> None:
    cmd = _browser_app_cmd(url, width, height)
    if cmd:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def open_in_chrome(url: str) -> None:
    """Open a job link in a normal Chrome tab (default browser if Chrome is not installed)."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("only http(s) links can be opened")
    exe = chrome_exe()
    if exe:
        subprocess.Popen([str(exe), url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == "jobradar-inbox"
    except Exception:  # noqa: BLE001
        return False


class InboxServer:
    def __init__(self, cfg, port: int, idle_minutes: float = 5):
        self.cfg = cfg
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.idle = idle_minutes * 60
        self.last_seen = time.time()
        self.db_path = cfg.path("paths.db")
        from jobradar.favorites import Favorites
        self.is_favorite = Favorites(cfg).is_favorite
        self.cv_status: dict[int, dict] = {}   # job id -> queued / running / need_posting / error
        self.cv_lock = threading.Lock()        # one CV at a time: each one spends Pro quota
        self.busy = 0                          # CVs in flight keep the server alive
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())

    # ------------------------------------------------------------- data
    def data(self) -> dict:
        from jobradar.cv_builder import CVBuilder
        store = Store(self.db_path)
        try:
            t = Tracker(store)
            t.sync()
            out = {"funnels": t.funnels(), "open": t.open_counts(), "items": t.items(7, self.is_favorite),
                   "states": STATE_HE, "order": STATES}
        finally:
            store.close()
        with_cv = CVBuilder(self.cfg).cv_index()
        for it in out["items"]:
            it["has_cv"] = it["id"] in with_cv
            it["cv_running"] = self.cv_status.get(it["id"], {}).get("state") in ("queued", "running")
        return out

    # --------------------------------------------------------------- CV
    def cv_state(self, job_id: int) -> dict:
        from jobradar.cv_builder import CVBuilder
        st = self.cv_status.get(job_id) or {}
        if st.get("state") in ("queued", "running"):
            return st
        meta = CVBuilder(self.cfg).find(job_id)
        if meta:
            return {"state": "done", "meta": meta, "last_error": st.get("error")}
        return st or {"state": "none"}

    def start_cv(self, job_id: int, posting: str | None) -> dict:
        if self.cv_status.get(job_id, {}).get("state") in ("queued", "running"):
            return self.cv_status[job_id]
        self.cv_status[job_id] = {"state": "queued", "step": "queued"}
        self.busy += 1
        threading.Thread(target=self._run_cv, args=(job_id, posting), daemon=False).start()
        return self.cv_status[job_id]

    def _run_cv(self, job_id: int, posting: str | None) -> None:
        from jobradar.cv_builder import CVBuilder, NeedPosting
        from jobradar.llm import UsageLimitReached
        st = self.cv_status[job_id]

        def progress(step: str) -> None:
            st["step"] = step
            self.last_seen = time.time()

        try:
            with self.cv_lock:
                st["state"] = "running"
                CVBuilder(self.cfg, progress=progress).build(job_id, posting)
            self.cv_status.pop(job_id, None)
        except NeedPosting:
            self.cv_status[job_id] = {"state": "need_posting"}
        except UsageLimitReached:
            self.cv_status[job_id] = {"state": "error", "error": "מכסת ה-Pro נגמרה לחלון הזמן הנוכחי. נסה שוב אחר כך."}
        except Exception as e:  # noqa: BLE001 - shown in the window
            log.exception("cv for #%s failed", job_id)
            self.cv_status[job_id] = {"state": "error", "error": str(e)[:400]}
        finally:
            self.busy -= 1
            self.last_seen = time.time()

    def set_state(self, job_id: int, state: str, note: str | None) -> dict:
        store = Store(self.db_path)
        try:
            return Tracker(store).set_state(job_id, state, note)
        finally:
            store.close()

    # ---------------------------------------------------------- handler
    def _handler(self):
        server = self
        allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

        class H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep the console quiet
                log.debug("inbox: " + fmt, *args)

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

            def _host_ok(self) -> bool:
                if self.headers.get("Host") not in allowed_hosts:
                    self._json({"error": "bad host"}, 403)
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if not self._host_ok():
                    return
                server.last_seen = time.time()
                path = self.path.split("?")[0]
                if path == "/":
                    page = _PAGE.read_text(encoding="utf-8").replace("__TOKEN__", server.token)
                    self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
                elif path == "/api/hello":
                    self._json({"app": "jobradar-inbox"})
                elif path == "/api/data":
                    self._json(server.data())
                elif path == "/api/ping":
                    self._json({"ok": True})
                elif path == "/api/cv":
                    q = dict(p.split("=", 1) for p in self.path.partition("?")[2].split("&") if "=" in p)
                    self._json(server.cv_state(int(q.get("id", 0))))
                elif path.startswith("/cv/") and path.endswith(".html") and path[4:-5].isdigit():
                    from jobradar.cv_builder import CVBuilder
                    b = CVBuilder(server.cfg)
                    meta = b.find(int(path[4:-5]))
                    if not meta:
                        self._json({"error": "no CV"}, 404)
                        return
                    body = b.cv_html(meta["base"]).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Security-Policy", "frame-ancestors 'self'")  # only our own window
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json({"error": "not found"}, 404)

            def do_POST(self):  # noqa: N802
                if not self._host_ok():
                    return
                if not secrets.compare_digest(self.headers.get("X-Token", ""), server.token):
                    self._json({"error": "bad token"}, 403)
                    return
                server.last_seen = time.time()
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                    if self.path == "/api/state":
                        row = server.set_state(int(body["id"]), str(body["state"]), body.get("note"))
                        self._json({"ok": True, "row": row})
                    elif self.path == "/api/open":
                        # the URL comes from the DB, never from the request
                        store = Store(server.db_path)
                        try:
                            job = store.get_job(int(body["id"]))
                        finally:
                            store.close()
                        if not job or not job["url"]:
                            self._json({"error": "no link for this job"}, 404)
                        else:
                            open_in_chrome(job["url"])
                            self._json({"ok": True})
                    elif self.path == "/api/cv":
                        posting = body.get("posting")
                        self._json(server.start_cv(int(body["id"]), str(posting) if posting else None))
                    elif self.path == "/api/cv/open":
                        from jobradar.cv_builder import CVBuilder, open_local
                        open_local(CVBuilder(server.cfg).open_path(int(body["id"]), str(body["what"])))
                        self._json({"ok": True})
                    else:
                        self._json({"error": "not found"}, 404)
                except (ValueError, KeyError) as e:
                    self._json({"error": str(e)}, 400)

        return H

    # -------------------------------------------------------------- run
    def _watch_idle(self) -> None:
        while True:
            time.sleep(15)
            if not self.busy and time.time() - self.last_seen > self.idle:
                log.info("inbox: window closed, stopping")
                self.httpd.shutdown()
                return

    def serve(self) -> None:
        threading.Thread(target=self._watch_idle, daemon=True).start()
        self.httpd.serve_forever()


def run_inbox(cfg, open_it: bool = True, port: int | None = None) -> str:
    port = int(port or cfg.get("tracker.port", 8765))
    url = f"http://127.0.0.1:{port}/"
    w, h = int(cfg.get("tracker.window_width", 1280)), int(cfg.get("tracker.window_height", 880))
    if already_running(port):
        if open_it:
            open_window(url, w, h)
        return url
    srv = InboxServer(cfg, port, float(cfg.get("tracker.idle_minutes", 5)))
    if open_it:
        threading.Timer(0.3, open_window, args=(url, w, h)).start()
    srv.serve()
    return url
