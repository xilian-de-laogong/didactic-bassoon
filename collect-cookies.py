#!/usr/bin/env python3
"""
Cookie Collector — Run locally with a visible browser.

Usage:
    python collect_cookies.py                 # interactive menu
    python collect_cookies.py firebase        # collect for specific site
    python collect_cookies.py --all           # collect for all sites sequentially

Flow:
    1. Opens Chrome (non-headless) and navigates to the site
    2. YOU manually log in (handle captcha, 2FA, etc.)
    3. Press Enter in the terminal when you're fully logged in
    4. Script saves cookies + session data to data/ directory
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import SITES, DATA_DIR, cookies_path, session_path


def extract_storage(page) -> dict:
    """Extract localStorage and sessionStorage from current page (Playwright)."""
    try:
        local_storage = page.evaluate(
            "() => { try { const s = {}; for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); s[k] = localStorage.getItem(k); } return s; } catch (e) { return {}; } }"
        )
    except Exception:
        local_storage = {}
    try:
        session_storage = page.evaluate(
            "() => { try { const s = {}; for (let i = 0; i < sessionStorage.length; i++) { const k = sessionStorage.key(i); s[k] = sessionStorage.getItem(k); } return s; } catch (e) { return {}; } }"
        )
    except Exception:
        session_storage = {}
    return {
        "localStorage": local_storage or {},
        "sessionStorage": session_storage or {},
    }


def sanitize_cookie(cookie: dict) -> dict:
    """Normalize cookie dict from Selenium or Playwright to stored format.

    Ensures keys: name, value, domain, path, secure, httpOnly, sameSite, expiry (int).
    """
    out = {}
    for k in ("name", "value", "path", "domain", "secure", "httpOnly", "sameSite"):
        if k in cookie:
            out[k] = cookie[k]

    # Normalize expiry / expires -> expiry (int)
    exp = cookie.get("expiry", cookie.get("expires"))
    if exp is not None:
        try:
            out["expiry"] = int(exp)
        except (ValueError, TypeError):
            pass

    # Validate sameSite
    if "sameSite" in out and out["sameSite"] not in ("Strict", "Lax", "None"):
        out.pop("sameSite", None)

    return out


def safe_json_write(path: Path, data) -> None:
    """Atomic JSON write (write to tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def collect_for_site(site_name: str) -> None:
    """Open a Playwright browser, let user log in, save cookies for one site."""
    if site_name not in SITES:
        print(f"  ✗ Unknown site '{site_name}'. Available: {', '.join(SITES)}")
        return

    cfg = SITES[site_name]
    login_url = cfg.get("login_url", cfg["url"])

    print(f"\n{'=' * 60}")
    print(f"  Collecting cookies for: {site_name}")
    print(f"  URL: {login_url}")
    print(f"{'=' * 60}\n")

    # Launch Playwright in headed mode for manual login
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )

        context = browser.new_context(user_agent=ua)
        try:
            page = context.new_page()
            page.goto(login_url)

            print("  ➤ Browser opened. Please log in manually in the opened window.")
            print("  ➤ Complete any captcha / 2FA prompts.")
            print("  ➤ Navigate to the final logged-in page if needed.")
            input("\n  ✦ Press ENTER here when you are fully logged in... ")

            # Give the page a moment to settle
            time.sleep(2)

            # ── Collect cookies ──────────────────────────────────
            raw_cookies = context.cookies()
            cookies = [sanitize_cookie(c) for c in raw_cookies]

            # ── Collect session data ─────────────────────────────
            storage = extract_storage(page)
            user_agent = page.evaluate("() => navigator.userAgent")
            current_url = page.url

            session_data = {
                "user_agent": user_agent,
                "url_after_login": current_url,
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "storage": storage,
            }

            # ── Save ────────────────────────────────────────────
            c_path = cookies_path(site_name)
            s_path = session_path(site_name)

            safe_json_write(c_path, cookies)
            safe_json_write(s_path, session_data)

            print(f"\n  ✓ Saved {len(cookies)} cookies  → {c_path}")
            print(f"  ✓ Saved session data    → {s_path}")
            print(f"  ✓ Logged-in URL: {current_url}")

            # Quick validation
            auth_cookies = [
                c
                for c in cookies
                if any(
                    kw in c["name"].lower()
                    for kw in ("sid", "session", "auth", "token", "login", "sso", "sacsid")
                )
            ]
            if auth_cookies:
                print(
                    f"  ✓ Found {len(auth_cookies)} auth-related cookies: {[c['name'] for c in auth_cookies]}"
                )
            else:
                print("  ⚠ No obvious auth cookies detected — this may still work fine.")

        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            print(f"  ✓ Browser closed for {site_name}.\n")


def interactive_menu() -> None:
    """Show a menu of available sites to collect cookies for."""
    print("\n" + "=" * 60)
    print("  Cookie Collector — Interactive Mode")
    print("=" * 60)
    print("\n  Available sites:\n")

    names = list(SITES.keys())
    for i, name in enumerate(names, 1):
        cfg = SITES[name]
        print(f"    {i}. {name:15s}  {cfg['url']}")

    print(f"\n    0. All sites")
    print(f"    q. Quit\n")

    choice = input("  Select (number or name): ").strip().lower()

    if choice == "q":
        return
    elif choice == "0":
        for name in names:
            collect_for_site(name)
    elif choice.isdigit() and 1 <= int(choice) <= len(names):
        collect_for_site(names[int(choice) - 1])
    elif choice in SITES:
        collect_for_site(choice)
    else:
        print(f"  ✗ Invalid choice: {choice}")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) < 2:
        interactive_menu()
        return

    arg = sys.argv[1].lower()

    if arg == "--all":
        for name in SITES:
            collect_for_site(name)
    elif arg in SITES:
        collect_for_site(arg)
    else:
        print(f"Usage: python {sys.argv[0]} [site_name | --all]")
        print(f"       Sites: {', '.join(SITES)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
