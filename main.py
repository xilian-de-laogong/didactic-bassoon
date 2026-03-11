#!/usr/bin/env python3
"""
Keep-Alive — Session maintainer with per-site mode selection.

Each site in config.py declares its mode:
  - "requests" → lightweight HTTP (for server-rendered sites)
  - "playwright" → headless browser with JS execution (for SPAs)

Usage:
    python main.py                  # run all configured sites
    python main.py firebase         # run only one site
    python main.py --once           # single pass (good for scheduled tasks)
    python main.py --once firebase  # single pass, one site
"""

import json
import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
import threading
import time
import os
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

import config
from config import DATA_DIR, LOG_FILE, cookies_path, session_path

# ─── Logging ─────────────────────────────────────────────────────────
logger = logging.getLogger("keep_alive")
logger.setLevel(logging.DEBUG)

_fmt = logging.Formatter(
    "[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.INFO)
_ch.setFormatter(_fmt)
logger.addHandler(_ch)

try:
    # Rotate log file when it reaches 1 MB, keep 5 old copies.
    _fh = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=1024 * 1024,  # 1 MB
        backupCount=5,
        encoding="utf-8",
    )
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(_fmt)
    logger.addHandler(_fh)
except OSError:
    pass

# ─── Shutdown ────────────────────────────────────────────────────────
_shutdown = threading.Event()


def _handle_signal(sig, frame):
    logger.info("Received signal %s — shutting down gracefully...", sig)
    _shutdown.set()


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# --- Playwright Lock ---
playwright_lock = threading.Lock()


# ─── Dashboard (Flask) ─────────────────────────────────────────────────
# Embedded admin dashboard for managing sites (password-only login)
try:
    from flask import (
        Flask,
        request,
        session,
        redirect,
        url_for,
        render_template_string,
        abort,
        jsonify,
    )

    flask_available = True
except Exception:
    flask_available = False

# Simple unhashed password stored in main.py
ADMIN_PASSWORD = "924450817"

if flask_available:
    dashboard_app = Flask(__name__)
    # Use a fixed secret for sessions (not secure for production)
    dashboard_app.secret_key = "dev-secret-key"

    _LOGIN = """
<!doctype html>
<title>Login</title>
<h1>Admin Login</h1>
<form method=post>
  <label>Password: <input type=password name=password></label>
  <input type=submit value=Login>
</form>
"""

    _DASH = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Keep-Alive Dashboard</title>
  <style>
    body { font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Arial; margin: 0; padding: 0; background:#f6f8fa; color:#222 }
    .container { max-width:1100px; margin:24px auto; padding:16px }
    header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px }
    h1 { margin:0; font-size:20px }
    .grid { display:grid; grid-template-columns: 2fr 1fr; gap:16px }
    .card { background:#fff; border:1px solid #e1e4e8; border-radius:8px; padding:16px }
    table { width:100%; border-collapse:collapse }
    th, td { text-align:left; padding:8px; border-bottom:1px solid #eee }
    th { background:#fafbfc; font-weight:600 }
    .actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap }
    .actions a, .actions button { display:inline-block }
    .actions form.inline { margin:0 }
    form.inline { display:inline }
    label { display:block; margin:6px 0 }
    input[type=text], input[type=number], select, textarea { width:100%; padding:8px; border:1px solid #dfe3e8; border-radius:6px }
    .small { font-size:13px; color:#555 }
    .btn { background:#0366d6; color:#fff; padding:8px 12px; border-radius:6px; border:none; cursor:pointer }
    .btn.ghost { background:transparent; color:#0366d6; border:1px solid #d0d7de }
    .cookie-list { max-height:300px; overflow:auto }
    pre { background:#f3f4f6; padding:8px; border-radius:6px; overflow:auto }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Keep-Alive Dashboard</h1>
      <div>
        <a href="/logout" class="small">Logout</a>
      </div>
    </header>

    <div class="grid">
      <div class="card">
        <h2>Sites</h2>
        <table>
          <thead>
            <tr><th>Name</th><th>URL</th><th>Public URL</th><th>Cookie</th><th>Actions</th></tr>
          </thead>
          <tbody>
          {% for name, cfg in sites.items() %}
            <tr>
              <td>{{name}}</td>
              <td><a href="{{cfg.get('url','')}}" target="_blank">{{cfg.get('url','')}}</a></td>
              <td>{{cfg.get('public_url','')}}</td>
              <td>{{cfg.get('cookie_file','-')}}</td>
              <td class="actions">
                <a class="btn ghost" href="/sites/{{name}}/view">View</a>
                <a class="btn ghost" href="/sites/{{name}}/edit">Edit</a>
                <form method="post" action="/sites/{{name}}/delete" class="inline" onsubmit="return confirm('Delete site {{name}}?')">
                  <button class="btn ghost">Delete</button>
                </form>
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>

        <h3 style="margin-top:16px">Add Site</h3>
        <form method="post" action="/sites">
          <label>Name: <input name="name" required></label>
          <label>URL: <input name="url" required></label>
          <label>Public URL: <input name="public_url"></label>
          <label>Cookie file:
            <select name="cookie_file">
              <option value="">(none)</option>
              {% for c in cookies %}
                <option value="{{c}}">{{c}}</option>
              {% endfor %}
            </select>
          </label>
          <label>Refresh interval (s): <input type="number" name="refresh_interval" value="3600"></label>
          <label>Ping interval (s): <input type="number" name="public_ping_interval" value="10"></label>
          <label>Mode: <select name="mode"><option value="playwright">playwright</option><option value="requests">requests</option></select></label>
          <label>Keep tab (s): <input type="number" name="keep_tab" value="120"></label>
          <div style="margin-top:8px"><button class="btn">Add Site</button></div>
        </form>
      </div>

      <div class="card">
        <h2>Cookies</h2>
        <div class="cookie-list">
          <table>
            <thead><tr><th>Name</th><th>Actions</th></tr></thead>
            <tbody>
              {% for c in cookies %}
              <tr>
                <td>{{c}}</td>
                <td>
                  <a class="btn ghost" href="/cookies/{{c}}/view">View</a>
                  <form method="post" action="/cookies/{{c}}/delete" class="inline" onsubmit="return confirm('Delete cookie {{c}}?')">
                    <button class="btn ghost">Delete</button>
                  </form>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
        </div>

        <h3 style="margin-top:12px">Upload cookie file</h3>
        <form method="post" action="/cookies/upload" enctype="multipart/form-data">
          <label>Cookie name (identifier): <input name="cookie_name" required></label>
          <label>Choose JSON file: <input type="file" name="cookie_file" accept="application/json"></label>
          <div style="margin-top:8px"><button class="btn">Upload</button></div>
        </form>

        <h3 style="margin-top:12px">Paste cookie JSON</h3>
        <form method="post" action="/cookies/paste">
          <label>Cookie name: <input name="cookie_name" required></label>
          <label>Cookie JSON (array of cookies): <textarea name="cookie_json" rows="8"></textarea></label>
          <label>Session JSON (optional): <textarea name="session_json" rows="4"></textarea></label>
          <div style="margin-top:8px"><button class="btn">Save</button></div>
        </form>

      </div>
    </div>

  </div>
</body>
</html>
"""

    def login_required(f):
        from functools import wraps

        @wraps(f)
        def wrapped(*args, **kwargs):
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)

        return wrapped

    @dashboard_app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            pw = request.form.get("password", "")
            if pw == ADMIN_PASSWORD:
                session["logged_in"] = True
                return redirect(url_for("index"))
            else:
                return render_template_string(_LOGIN + '<p style="color:red">Invalid password</p>')
        return render_template_string(_LOGIN)

    @dashboard_app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @dashboard_app.route("/")
    @login_required
    def index():
        sites = config.load_sites()
        cookies = _list_cookie_files()
        return render_template_string(_DASH, sites=sites, cookies=cookies)

    @dashboard_app.route("/sites", methods=["POST"])
    @login_required
    def add_site():
        name = request.form.get("name")
        if not name:
            abort(400)
        sites = config.load_sites()
        if name in sites:
            abort(400, "Site already exists")
        try:
            cfg = {
                "url": request.form.get("url", ""),
                "public_url": request.form.get("public_url", ""),
                "cookie_file": request.form.get("cookie_file", ""),
                "refresh_interval": int(request.form.get("refresh_interval") or 3600),
                "public_ping_interval": int(request.form.get("public_ping_interval") or 10),
                "mode": request.form.get("mode", "playwright"),
                "keep_tab": int(request.form.get("keep_tab") or 120),
            }
        except Exception:
            abort(400, "Invalid input")
        sites[name] = cfg
        config.save_sites(sites)
        return redirect(url_for("index"))

    @dashboard_app.route("/sites/<name>/delete", methods=["POST"])
    @login_required
    def delete_site(name):
        sites = config.load_sites()
        if name in sites:
            del sites[name]
            config.save_sites(sites)
        return redirect(url_for("index"))

    @dashboard_app.route("/sites/<name>/view")
    @login_required
    def view_site(name):
        sites = config.load_sites()
        cfg = sites.get(name)
        if not cfg:
            abort(404)
        return render_template_string(
            '<h1>Site: {{name}}</h1><pre>{{cfg}}</pre><p><a href="/">Back</a> <a href="/sites/{{name}}/edit">Edit</a></p>',
            name=name,
            cfg=cfg,
        )

    @dashboard_app.route("/sites/<name>/edit", methods=["GET", "POST"])
    @login_required
    def edit_site(name):
        sites = config.load_sites()
        cfg = sites.get(name)
        if not cfg:
            abort(404)
        if request.method == "POST":
            try:
                cfg["url"] = request.form.get("url", "")
                cfg["public_url"] = request.form.get("public_url", "")
                cfg["cookie_file"] = request.form.get("cookie_file", "")
                cfg["refresh_interval"] = int(request.form.get("refresh_interval") or 3600)
                cfg["public_ping_interval"] = int(request.form.get("public_ping_interval") or 10)
                cfg["mode"] = request.form.get("mode", "playwright")
                cfg["keep_tab"] = int(request.form.get("keep_tab") or 120)
            except Exception:
                abort(400, "Invalid input")
            sites[name] = cfg
            config.save_sites(sites)
            return redirect(url_for("index"))

        # GET: render edit form
        cookies = _list_cookie_files()
        form = """<h1>Edit {{name}}</h1>
        <form method="post">
          <label>URL: <input name="url" value="{{cfg.get('url','')}}"></label>
          <label>Public URL: <input name="public_url" value="{{cfg.get('public_url','')}}"></label>
          <label>Cookie file: <select name="cookie_file">
            <option value="">(none)</option>
            {% for c in cookies %}
              <option value="{{c}}" {% if cfg.get('cookie_file')==c %}selected{% endif %}>{{c}}</option>
            {% endfor %}
          </select></label>
          <label>Refresh interval (s): <input name="refresh_interval" value="{{cfg.get('refresh_interval',3600)}}"></label>
          <label>Ping interval (s): <input name="public_ping_interval" value="{{cfg.get('public_ping_interval',10)}}"></label>
          <label>Mode: <select name="mode"><option value="playwright" {% if cfg.get('mode')=='playwright' %}selected{% endif %}>playwright</option><option value="requests" {% if cfg.get('mode')=='requests' %}selected{% endif %}>requests</option></select></label>
          <label>Keep tab (s): <input name="keep_tab" value="{{cfg.get('keep_tab',120)}}"></label>
          <div style="margin-top:8px"><button class="btn">Save</button></div>
        </form>
        <p><a href="/">Back</a></p>
        """
        return render_template_string(form, name=name, cfg=cfg, cookies=cookies)

    @dashboard_app.route("/reload", methods=["POST"])
    @login_required
    def reload_sites():
        # Force config to re-read sites.json
        config.SITES = config.load_sites()
        return jsonify({"ok": True})

    # -------- Cookie management routes --------
    def _list_cookie_files():
        out = []
        try:
            for p in config.DATA_DIR.glob("cookies_*.json"):
                name = p.name
                if name.startswith("cookies_") and name.endswith(".json"):
                    out.append(name[len("cookies_") : -5])
        except Exception:
            pass
        return sorted(out)

    @dashboard_app.route("/cookies")
    @login_required
    def cookies_index():
        files = _list_cookie_files()
        return render_template_string("<h1>Cookies</h1><pre>{{files}}</pre>", files=files)

    @dashboard_app.route("/cookies/<name>/view")
    @login_required
    def view_cookie(name):
        try:
            p = config.cookies_path(name)
            if not p.exists():
                abort(404)
            content = p.read_text(encoding="utf-8")
            return render_template_string(
                '<h1>Cookie: {{name}}</h1><pre>{{content}}</pre><p><a href="/">Back</a></p>',
                name=name,
                content=content,
            )
        except Exception as e:
            abort(500, str(e))

    @dashboard_app.route("/cookies/<name>/delete", methods=["POST"])
    @login_required
    def delete_cookie(name):
        try:
            p = config.cookies_path(name)
            if p.exists():
                p.unlink()
            # also remove session file if exists
            s = config.session_path(name)
            if s.exists():
                s.unlink()
        except Exception:
            pass
        return redirect(url_for("index"))

    @dashboard_app.route("/cookies/upload", methods=["POST"])
    @login_required
    def upload_cookie():
        cookie_name = request.form.get("cookie_name")
        if not cookie_name:
            abort(400, "Missing cookie_name")
        file = request.files.get("cookie_file")
        if not file:
            abort(400, "No file")
        try:
            data = json.loads(file.read())
        except Exception:
            abort(400, "Invalid JSON")
        # Save cookie array
        try:
            config._atomic_write(config.cookies_path(cookie_name), data)
        except Exception as e:
            abort(500, str(e))
        return redirect(url_for("index"))

    @dashboard_app.route("/cookies/paste", methods=["POST"])
    @login_required
    def paste_cookie():
        cookie_name = request.form.get("cookie_name")
        if not cookie_name:
            abort(400, "Missing cookie_name")
        cookie_json = request.form.get("cookie_json")
        session_json = request.form.get("session_json")
        try:
            cookies = json.loads(cookie_json) if cookie_json else []
        except Exception:
            abort(400, "Invalid cookie JSON")
        try:
            if session_json:
                sess = json.loads(session_json)
            else:
                sess = None
        except Exception:
            abort(400, "Invalid session JSON")

        try:
            config._atomic_write(config.cookies_path(cookie_name), cookies)
            if sess is not None:
                config._atomic_write(config.session_path(cookie_name), sess)
        except Exception as e:
            abort(500, str(e))
        return redirect(url_for("index"))

else:
    dashboard_app = None


# ─── Cookie Loading ─────────────────────────────────────────────────
def load_cookies(site_name: str) -> list[dict]:
    """Load cookies from JSON file.

    If a site config specifies a 'cookie_file', use that identifier instead of the
    site name when resolving cookies_<name>.json.
    """
    # Resolve cookie file name from site config if provided
    cookie_identifier = None
    try:
        cookie_identifier = config.SITES.get(site_name, {}).get("cookie_file")
    except Exception:
        cookie_identifier = None

    target = cookie_identifier or site_name
    cpath = cookies_path(target)
    if not cpath.exists():
        logger.warning("No cookies file for '%s' (resolved as '%s') at %s", site_name, target, cpath)
        return []
    try:
        return json.loads(cpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read cookies for '%s' (file: %s): %s", site_name, cpath, e)
        return []


def load_session_data(site_name: str) -> dict:
    """Load session metadata (user-agent, storage, etc.).

    Respect a site-specific 'cookie_file' setting; session files are session_<id>.json
    where <id> is either site name or the configured cookie_file.
    """
    cookie_identifier = None
    try:
        cookie_identifier = config.SITES.get(site_name, {}).get("cookie_file")
    except Exception:
        cookie_identifier = None

    target = cookie_identifier or site_name
    spath = session_path(target)
    if not spath.exists():
        return {}
    try:
        return json.loads(spath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


# ─── Requests Mode ───────────────────────────────────────────────────
def build_requests_session(site_name: str) -> requests.Session | None:
    """Build a requests.Session with cookies and headers from saved data."""
    cookies = load_cookies(site_name)
    if not cookies:
        logger.error("Cannot create session for '%s' — no cookies found.", site_name)
        return None

    session_data = load_session_data(site_name)
    session = requests.Session()

    for c in cookies:
        session.cookies.set(
            name=c["name"],
            value=c["value"],
            domain=c.get("domain", ""),
            path=c.get("path", "/"),
            secure=c.get("secure", False),
        )

    ua = session_data.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36",
    )
    session.headers.update(
        {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    logger.info("Built requests session for '%s' with %d cookies.", site_name, len(cookies))
    return session


def heartbeat_requests(site_name: str, session: requests.Session, url: str) -> bool:
    """Keep-alive via lightweight HTTP GET (no JS)."""
    try:
        resp = session.get(url, timeout=60, allow_redirects=True)
        final_url = resp.url

        login_indicators = [
            "login",
            "signin",
            "accounts.google.com/v3/signin",
            "accounts.google.com/ServiceLogin",
        ]
        if any(ind in final_url.lower() for ind in login_indicators):
            logger.warning("[%s] ⚠ Session expired — redirected to login.", site_name)
            return False

        if resp.status_code == 200:
            size_kb = len(resp.content) / 1024
            logger.info("[%s] ✓ HTTP 200 — %.1f KB — %s", site_name, size_kb, final_url[:60])
            if resp.cookies:
                session.cookies.update(resp.cookies)
            return True

        elif resp.status_code in (401, 403):
            logger.warning("[%s] ⚠ Auth error %d", site_name, resp.status_code)
            return False
        else:
            logger.warning("[%s] ⚠ HTTP %d", site_name, resp.status_code)
            return resp.status_code < 500

    except requests.RequestException as e:
        logger.error("[%s] ✗ Request failed: %s", site_name, e)
        return False


# ─── Playwright Mode ─────────────────────────────────────────────────
def heartbeat_public(site_name: str, public_url: str, timeout: int = 10) -> bool:
    """Lightweight health check for a site's public URL."""
    try:
        resp = requests.get(public_url, timeout=timeout, allow_redirects=True)
        final_url = resp.url

        # Treat redirects to common login pages as failures
        login_indicators = ["login", "signin", "accounts.google.com"]
        if any(ind in final_url.lower() for ind in login_indicators):
            logger.warning("[%s] ⚠ Public URL appears redirected to login: %s", site_name, final_url)
            return False

        if resp.status_code == 200:
            size_kb = len(resp.content) / 1024
            logger.info("[%s] ✓ Public HTTP 200 — %.1f KB — %s", site_name, size_kb, public_url[:60])
            return True
        else:
            logger.warning("[%s] ⚠ Public HTTP %d — %s", site_name, resp.status_code, public_url)
            return False

    except requests.RequestException as e:
        logger.warning("[%s] ✗ Public URL check failed: %s", site_name, e)
        return False


def heartbeat_playwright(site_name: str, url: str, screenshot: bool = False) -> bool:
    """
    Keep-alive via Playwright — full JS execution.
    Opens a fresh browser each time to avoid memory leaks.
    """
    session_data = load_session_data(site_name)
    cookies = load_cookies(site_name)
    cfg = config.SITES[site_name]
    keep_tab_time = cfg.get("keep_tab", 10)  # Default to 10s if not set

    if not cookies:
        logger.error("[%s] No cookies for Playwright heartbeat.", site_name)
        return False

    with playwright_lock:
        try:
            with sync_playwright() as p:
                logger.info("[%s] Launching Playwright Chromium...", site_name)
                # Build launch kwargs in a portable way (don't hardcode executable_path)
                launch_kwargs = {"headless": True, "args": ["--disable-gpu", "--no-sandbox"]}
                try:
                    if Path("/usr/bin/chromium").exists():
                        launch_kwargs["executable_path"] = "/usr/bin/chromium"
                except Exception:
                    pass
                browser = p.chromium.launch(**launch_kwargs)

                ua = session_data.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36",
                )

                context = browser.new_context(user_agent=ua)

                # Map cookies to Playwright format
                pw_cookies = []
                for c in cookies:
                    pc = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ""),
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", False),
                        "httpOnly": c.get("httpOnly", False),
                    }
                    if "expiry" in c:
                        pc["expires"] = float(c["expiry"])
                    if "sameSite" in c and c["sameSite"] in ("Strict", "Lax", "None"):
                        pc["sameSite"] = c["sameSite"]

                    if pc["domain"]:
                        pw_cookies.append(pc)

                context.add_cookies(pw_cookies)
                logger.info("[%s] Injected %d cookies.", site_name, len(pw_cookies))

                page = context.new_page()
                logger.info("[%s] Navigating to %s", site_name, url[:60])

                # Navigate and wait for network to settle
                page.goto(url, wait_until="networkidle", timeout=60000)

                # Keep the tab open for the specified duration
                logger.info("[%s] Keeping tab open for %ds...", site_name, keep_tab_time)
                time.sleep(keep_tab_time)

                current_url = page.url
                title = page.title() or "(no title)"

                # Check for login redirect
                login_indicators = ["login", "signin", "accounts.google.com"]
                if any(ind in current_url.lower() for ind in login_indicators):
                    logger.warning(
                        "[%s] ⚠ Session expired — landed on login page: %s", site_name, current_url
                    )
                    if screenshot:
                        _save_screenshot_pw(page, site_name, "expired")
                    browser.close()
                    return False

                # Save screenshot for verification
                if screenshot:
                    _save_screenshot_pw(page, site_name, "ok")

                # Save updated cookies back to disk (rotation handling)
                updated_cookies = context.cookies()
                _save_updated_cookies_pw(updated_cookies, site_name)

                # Save session metadata (user_agent and storage state) to the same cookie identifier
                try:
                    cookie_identifier = None
                    try:
                        cookie_identifier = config.SITES.get(site_name, {}).get("cookie_file")
                    except Exception:
                        cookie_identifier = None
                    target = cookie_identifier or site_name
                    sess = {"user_agent": ua, "storage": context.storage_state()}
                    config._atomic_write(config.session_path(target), sess)
                    logger.info(
                        "[%s] 🔄 Saved session metadata to %s", site_name, config.session_path(target).name
                    )
                except Exception as e:
                    logger.warning("[%s] Failed to save session metadata: %s", site_name, e)

                logger.info("[%s] ✓ Playwright OK — Title: %s", site_name, title.strip()[:60])
                browser.close()
                return True

        except Exception as e:
            logger.error("[%s] ✗ Playwright error: %s", site_name, e)
            return False


def _save_screenshot_pw(page, site_name: str, status: str) -> None:
    """Save a screenshot to data/ for debugging."""
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = DATA_DIR / f"screenshot_{site_name}_{status}_{ts}.png"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path))
        logger.info("[%s] 📸 Screenshot saved → %s", site_name, path.name)
    except Exception as e:
        logger.warning("[%s] Screenshot failed: %s", site_name, e)


def _save_updated_cookies_pw(pw_cookies: list, site_name: str) -> None:
    """Save the browser's current cookies back to disk (atomic write).

    Use the site's configured 'cookie_file' identifier when present so updated
    cookies overwrite the chosen cookie set instead of creating a new one.
    """
    try:
        if not pw_cookies:
            return

        cookies = []
        for c in pw_cookies:
            cleaned = {
                "name": c["name"],
                "value": c["value"],
                "domain": c["domain"],
                "path": c["path"],
                "secure": c["secure"],
                "httpOnly": c["httpOnly"],
            }
            if "expires" in c:
                cleaned["expiry"] = int(c["expires"])
            if "sameSite" in c:
                cleaned["sameSite"] = c["sameSite"]
            cookies.append(cleaned)

        # Resolve target cookie identifier from site config (fall back to site_name)
        cookie_identifier = None
        try:
            cookie_identifier = config.SITES.get(site_name, {}).get("cookie_file")
        except Exception:
            cookie_identifier = None
        target = cookie_identifier or site_name

        # Atomic write
        cpath = cookies_path(target)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = cpath.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(cookies, indent=2, default=str), encoding="utf-8")
            tmp.replace(cpath)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

        logger.info("[%s] 🔄 Saved %d updated cookies to disk (target: %s).", site_name, len(cookies), target)
    except Exception as e:
        logger.warning("[%s] Failed to save updated cookies: %s", site_name, e)


# ─── Site Runner ─────────────────────────────────────────────────────
def run_site(site_name: str, once: bool = False, screenshot: bool = False) -> None:
    """Run keep-alive loop for a single site.

    This function now reloads the sites.json on every loop so runtime edits
    (refresh_interval, mode, cookie_file, etc.) take effect without restarting
    the process. If a site is removed from sites.json the runner will stop.
    """
    # Initial values
    sites = config.load_sites()
    if site_name not in sites:
        logger.error("Site '%s' not found in configuration; aborting runner.", site_name)
        return
    cfg = sites[site_name]

    # Track last full Playwright run — start now so first full run occurs after `interval`
    last_playwright_run = time.time()
    consecutive_failures = 0

    # max_backoff will be recalculated each loop based on current interval
    req_session = None

    logger.info("Starting keep-alive thread for '%s'", site_name)

    while not _shutdown.is_set():
        # Reload sites so runtime edits take effect
        try:
            sites = config.load_sites()
            # Update global reference so helper functions using config.SITES see changes
            config.SITES = sites
        except Exception:
            # If load fails, keep previous cfg and try again after a short wait
            logger.warning("[%s] Failed to reload site configuration; retrying...", site_name)
            if _shutdown.wait(timeout=5):
                break
            continue

        if site_name not in sites:
            logger.info("[%s] Site removed from configuration; stopping runner.", site_name)
            return

        cfg = sites[site_name]
        url = cfg.get("url")
        if not url:
            logger.warning("[%s] Site configuration missing 'url'; skipping iteration.", site_name)
            if _shutdown.wait(timeout=5):
                break
            continue

        interval = cfg.get("refresh_interval", 3600)
        mode = cfg.get("mode", "requests")
        if mode == "selenium":
            mode = "playwright"
        ping_interval = cfg.get("public_ping_interval", interval)
        keep_tab_time = cfg.get("keep_tab", 10)

        max_backoff = min(interval, 3600)

        # Rebuild requests session if needed
        if "public_url" not in cfg and mode == "requests":
            req_session = build_requests_session(site_name)
            if req_session is None:
                logger.warning("[%s] No requests session available; retrying later.", site_name)
                # Wait a short time and try again; do not exit the entire runner
                if _shutdown.wait(timeout=5):
                    break
                continue

        logger.debug("[%s] Using mode=%s interval=%s", site_name, mode, format_duration(interval))

        success = False

        if "public_url" in cfg:
            public_url = cfg["public_url"]
            success = heartbeat_public(site_name, public_url)

            if not success:
                logger.info("[%s] Public URL down — opening admin URL with Playwright...", site_name)
                ok = heartbeat_playwright(site_name, url, screenshot=screenshot)
                last_playwright_run = time.time()
                success = ok
            else:
                now = time.time()
                if now - last_playwright_run >= interval:
                    logger.info("[%s] Scheduled Playwright run due — opening admin URL...", site_name)
                    ok = heartbeat_playwright(site_name, url, screenshot=screenshot)
                    last_playwright_run = now
                    success = ok
        else:
            if mode == "playwright":
                success = heartbeat_playwright(site_name, url, screenshot=screenshot)
            else:
                success = heartbeat_requests(site_name, req_session, url)  # type: ignore

        if success:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        if once:
            logger.info("[%s] Single pass complete.", site_name)
            return

        # Calculate sleep
        if consecutive_failures > 0:
            backoff = min(60 * (2 ** (consecutive_failures - 1)), max_backoff)
            sleep = backoff
            logger.info("[%s] Retry in %s (failure #%d).", site_name, format_duration(backoff), consecutive_failures)
        else:
            sleep = ping_interval if "public_url" in cfg else interval

        logger.info("[%s] Next heartbeat in %s.", site_name, format_duration(sleep))

        if _shutdown.wait(timeout=sleep):
            break

    logger.info("[%s] Stopped.", site_name)


# ─── Utilities ───────────────────────────────────────────────────────
def format_duration(seconds: int | float) -> str:
    """Human-friendly duration string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m{s}s" if s else f"{m}m"
    elif seconds < 86400:
        h, rem = divmod(seconds, 3600)
        m = rem // 60
        return f"{h}h{m}m" if m else f"{h}h"
    else:
        d, rem = divmod(seconds, 86400)
        h = rem // 3600
        return f"{d}d{h}h" if h else f"{d}d"


# ─── Main ────────────────────────────────────────────────────────────
def main() -> None:
    args = [a.lower() for a in sys.argv[1:]]
    once = "--once" in args
    screenshot = "--screenshot" in args
    site_args = [a for a in args if not a.startswith("--")]

    # Start dashboard server unless --no-dashboard is set and Flask is available
    if "--no-dashboard" not in args and dashboard_app is not None:

        def _run_dashboard():
            # Disable the reloader; run on localhost:8080
            dashboard_app.run(host="127.0.0.1", port=os.getenv("PORT", 8080), debug=False, use_reloader=False)

        t = threading.Thread(target=_run_dashboard, daemon=True)
        t.start()
        logger.info(
            "Dashboard available at http://127.0.0.1:" + str(os.getenv("PORT", 8080)) + " (use /login)"
        )

    # Determine which sites to run
    if site_args:
        target_sites = []
        for name in site_args:
            if name in config.SITES:
                target_sites.append(name)
            else:
                logger.error("Unknown site '%s'. Available: %s", name, ", ".join(config.SITES))
                sys.exit(1)
    else:
        target_sites = list(config.SITES.keys())

    # Helper to determine if a site is "ready" (has cookies or a public_url)
    def _site_is_ready(name: str) -> bool:
        cfg = config.SITES.get(name, {})
        cookie_identifier = None
        try:
            cookie_identifier = cfg.get("cookie_file") if isinstance(cfg, dict) else None
        except Exception:
            cookie_identifier = None
        target = cookie_identifier or name
        return cookies_path(target).exists() or "public_url" in (cfg or {})

    # Filter to sites that actually have cookies (or provide a public_url)
    ready_sites = []
    for name in target_sites:
        if _site_is_ready(name):
            ready_sites.append(name)
        else:
            logger.warning("Skipping '%s' — no cookies found.", name)

    # If nothing ready, and dashboard is available, wait for sites to be added via the dashboard
    if not ready_sites:
        if dashboard_app is None:
            logger.error("No sites ready. Collect cookies first!")
            sys.exit(1)
        else:
            logger.info("No sites ready. Waiting for sites to be added via dashboard...")
            # Poll for new sites until shutdown or sites become ready
            while not _shutdown.is_set():
                try:
                    config.SITES = config.load_sites()
                except Exception:
                    time.sleep(1)
                    continue
                target_sites = list(config.SITES.keys())
                ready_sites = [n for n in target_sites if _site_is_ready(n)]
                if ready_sites:
                    logger.info("Detected %d site(s) ready; starting...", len(ready_sites))
                    break
                # Wait a short time before re-checking; allow graceful shutdown while waiting
                if _shutdown.wait(timeout=5):
                    break
            if not ready_sites:
                logger.info("Shutdown received before sites were added. Exiting.")
                sys.exit(0)

    logger.info("=" * 60)
    logger.info("  Keep-Alive (Playwright Version) — %d site(s)", len(ready_sites))
    for name in ready_sites:
        mode = config.SITES[name].get("mode", "requests")
        if mode == "selenium":
            mode = "playwright"
        interval = format_duration(config.SITES[name]["refresh_interval"])
        logger.info("    • %s [%s] every %s", name, mode.upper(), interval)
    if once:
        logger.info("  Mode: single pass")
    logger.info("=" * 60)

    # Run sites concurrently in threads
    threads = []
    for name in ready_sites:
        if _shutdown.is_set():
            logger.info("Shutdown signal received, skipping remaining sites.")
            break
        thread = threading.Thread(target=run_site, args=(name, once, screenshot))
        threads.append(thread)
        thread.start()

    # Wait for all threads to finish. The signal handler will trigger termination.
    for t in threads:
        t.join()

    logger.info("Keep-Alive stopped. Goodbye!")


if __name__ == "__main__":
    main()

