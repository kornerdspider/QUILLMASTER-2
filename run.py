"""Start Quill Master 2.

    python run.py              opens in its own window (or your browser if that isn't possible)
    python run.py --browser    always use your web browser
    python run.py --demo       try the screens without Ollama (simple pattern reader, not AI)
"""
import os
import socket
import sys
import threading
import time
import webbrowser

# A packaged app with no console has no stdout/stderr; give logging somewhere to write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import uvicorn  # noqa: E402

from quillmaster2 import config  # noqa: E402
from quillmaster2.server import create_app  # noqa: E402


def already_running(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def wait_until_up(port, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        if already_running(port):
            return True
        time.sleep(0.15)
    return False


def main():
    cfg = config.load()
    if "--demo" in sys.argv:
        cfg["engine"] = "demo"
        config.save(cfg)
    port = cfg["port"]
    url = f"http://127.0.0.1:{port}"

    if already_running(port):
        # Quill Master is already open: just show it again.
        open_window(url, blocking=False)
        return

    print(f"\n  Quill Master 2 is running at {url}")
    print(f"  Your books and Story Bible are saved in {config.data_dir()}")
    print("  Keep this window open while you work. Close it to stop.\n")
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port,
                                           log_level="warning", log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not wait_until_up(port):
        print("  The server didn't start. Is another program using port", port, "?")
        return
    if open_window(url, blocking=True):
        server.should_exit = True  # the window was closed: stop the server too
        thread.join(timeout=5)
        return
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True


def open_window(url, blocking):
    """Open the app in its own window. Returns True if a window was shown and closed."""
    if "--browser" not in sys.argv and "--no-browser" not in sys.argv:
        try:
            import webview  # pywebview: a native window around the app
            webview.create_window("Quill Master 2", url, width=1360, height=900,
                                  min_size=(960, 640), background_color="#0F1114")
            webview.start()
            return True
        except Exception:  # noqa: BLE001  (no pywebview, or no system web engine)
            pass
    if "--no-browser" not in sys.argv:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    return False


if __name__ == "__main__":
    main()
