"""Start Quill Master 2 and open it in your web browser.

    python run.py            normal start
    python run.py --demo     try the screens without Ollama (simple pattern reader)
"""
import sys
import threading
import webbrowser

import uvicorn

from quillmaster2 import config
from quillmaster2.server import create_app


def main():
    cfg = config.load()
    if "--demo" in sys.argv:
        cfg["engine"] = "demo"
        config.save(cfg)
    port = cfg["port"]
    url = f"http://127.0.0.1:{port}"
    print(f"\n  Quill Master 2 is running at {url}")
    print(f"  Your books and Story Bible are saved in {config.data_dir()}")
    print("  Keep this window open while you work. Close it to stop.\n")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
