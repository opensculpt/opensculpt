"""OpenSculpt Desktop App — native window wrapping the dashboard.

Uses pywebview for the native window (Edge WebView2 on Windows)
and pystray for the system tray icon. The OpenSculpt backend (FastAPI +
uvicorn) runs in a background thread.

Launch with:  python -m agos.desktop
         or:  OpenSculpt.exe  (after PyInstaller build)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time

import webview

_logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = int(os.getenv("SCULPT_DASHBOARD_PORT", os.getenv("AGOS_DASHBOARD_PORT", "8420")))
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
WINDOW_TITLE = "OpenSculpt — The Self-Evolving Agentic OS"
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 820


# ── Backend ──────────────────────────────────────────────────

def _run_backend_thread() -> None:
    """Run the OpenSculpt backend in a background thread (used by .exe)."""
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        os.environ["AGOS_DASHBOARD_HOST"] = DASHBOARD_HOST
        os.environ["AGOS_DASHBOARD_PORT"] = str(DASHBOARD_PORT)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        from agos.serve import main as serve_main
        loop.run_until_complete(serve_main())
    except Exception:
        _logger.exception("Backend crashed")
    finally:
        loop.close()


def _run_backend_subprocess() -> subprocess.Popen:
    """Launch the OpenSculpt backend as a child process (used in dev)."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))

    env = os.environ.copy()
    env["AGOS_DASHBOARD_HOST"] = DASHBOARD_HOST
    env["AGOS_DASHBOARD_PORT"] = str(DASHBOARD_PORT)
    env.setdefault("AGOS_LOG_LEVEL", "INFO")

    serve_script = os.path.join(project_root, "agos", "serve.py")
    return subprocess.Popen(
        [sys.executable, serve_script],
        cwd=project_root,
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False)


def _wait_for_backend(timeout: float = 60.0) -> bool:
    """Block until the dashboard HTTP server responds."""
    import urllib.request
    import urllib.error

    deadline = time.monotonic() + timeout
    url = f"{DASHBOARD_URL}/api/status"

    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)

    return False


# ── System tray ──────────────────────────────────────────────

def _run_tray(window: webview.Window) -> None:
    """System tray icon with menu."""
    try:
        import pystray
        from agos.desktop.icon import create_agos_icon

        icon_image = create_agos_icon(64)

        def on_show(_icon, _item):
            window.show()
            window.restore()

        def on_hide(_icon, _item):
            window.hide()

        def on_quit(_icon, _item):
            _icon.stop()
            window.destroy()

        menu = pystray.Menu(
            pystray.MenuItem("Show OpenSculpt", on_show, default=True),
            pystray.MenuItem("Hide to Tray", on_hide),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        )

        icon = pystray.Icon("opensculpt", icon_image, "OpenSculpt", menu)
        icon.run()
    except Exception:
        _logger.exception("System tray failed (non-fatal)")


# ── Main window ──────────────────────────────────────────────

def launch() -> None:
    """Launch the OpenSculpt desktop application."""
    backend_proc = None

    print(f"Starting OpenSculpt backend on {DASHBOARD_URL} ...")

    if _is_frozen():
        # Inside PyInstaller exe — run backend in-process via thread
        t = threading.Thread(target=_run_backend_thread, daemon=True)
        t.start()
    else:
        # Dev mode — run backend as subprocess
        backend_proc = _run_backend_subprocess()

    print("Waiting for backend to start ...")
    if not _wait_for_backend(timeout=60.0):
        print("ERROR: Backend did not start within 60 seconds.")
        print(f"Try opening {DASHBOARD_URL} manually or check logs.")
        if backend_proc:
            backend_proc.terminate()
        sys.exit(1)

    print("Backend ready. Opening window ...")

    # Create the native window
    window = webview.create_window(
        WINDOW_TITLE,
        DASHBOARD_URL,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(800, 500),
        background_color="#0a0a0f",
        text_select=True,
    )

    # Start system tray in background
    tray_thread = threading.Thread(target=_run_tray, args=(window,), daemon=True)
    tray_thread.start()

    # Run the window (blocks until closed)
    webview.start(debug=os.getenv("AGOS_DEBUG", "").lower() in ("1", "true"))

    # Cleanup
    print("OpenSculpt desktop closed. Stopping backend ...")
    if backend_proc and backend_proc.poll() is None:
        backend_proc.terminate()
        try:
            backend_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            backend_proc.kill()

    print("Done.")


if __name__ == "__main__":
    launch()
