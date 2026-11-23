"""AI setup: finding, installing and starting Ollama (with a stand-in Ollama package)."""
import http.server
import io
import os
import socket
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QM2_DATA", tempfile.mkdtemp(prefix="qm2test_"))

from quillmaster2 import config, ollama_setup  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FAKE_OLLAMA = r'''#!/usr/bin/env python3
# Pretends to be "ollama serve": answers /api/tags, stops on /quit.
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
PORT = int(os.environ.get("FAKE_PORT", "0"))
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/quit":
            self.send_response(200); self.end_headers()
            threading.Thread(target=srv.shutdown).start(); return
        b = json.dumps({"models": [{"name": "qwen3.5:4b"}]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)
if len(sys.argv) > 1 and sys.argv[1] == "serve":
    srv = HTTPServer(("127.0.0.1", PORT), H); srv.serve_forever()
'''


def test_has_model():
    assert ollama_setup.has_model(["qwen3.5:4b", "nomic-embed-text:latest"], "qwen3.5:4b")
    assert ollama_setup.has_model(["nomic-embed-text:latest"], "nomic-embed-text")
    assert not ollama_setup.has_model(["qwen3.5:9b"], "qwen3.5:4b")
    assert not ollama_setup.has_model(None, "qwen3.5:4b")


def test_status_when_nothing_installed(monkeypatch):
    monkeypatch.setattr(ollama_setup, "_candidates", lambda: [])
    monkeypatch.setattr(ollama_setup, "running", lambda cfg=None: None)
    st = ollama_setup.status()
    assert st["installed"] is False and st["running"] is False
    assert st["recommended"] == "qwen3.5:4b"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="Linux-style install")
def test_linux_install_downloads_unpacks_and_starts(monkeypatch, tmp_path):
    port = free_port()
    monkeypatch.setenv("FAKE_PORT", str(port))
    cfg = config.load()
    cfg["ollama_url"] = f"http://127.0.0.1:{port}"
    config.save(cfg)

    # Build a stand-in Ollama package: bin/ollama + lib/ollama/
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = FAKE_OLLAMA.encode()
        info = tarfile.TarInfo("bin/ollama")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
        d = tarfile.TarInfo("lib/ollama")
        d.type, d.mode = tarfile.DIRTYPE, 0o755
        tf.addfile(d)
    pkg = tmp_path / "pkg.tar.zst"
    pkg.write_bytes(buf.getvalue())

    # Serve it over HTTP like ollama.com would.
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(tmp_path), **k)

        def log_message(self, *a):
            pass
    web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{web.server_address[1]}/pkg.tar.zst"

    monkeypatch.setattr(ollama_setup, "system", lambda: "linux")
    monkeypatch.setitem(ollama_setup.URLS, "linux-amd64", url)
    monkeypatch.setitem(ollama_setup.URLS, "linux-arm64", url)
    monkeypatch.setattr(ollama_setup, "_candidates", lambda: [ollama_setup.managed_dir() / "bin" / "ollama"])

    def fake_extract(archive, dest):  # the stand-in is gzip, not zstd
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(dest)
        return dest
    monkeypatch.setattr(ollama_setup, "extract_tar_zst", fake_extract)

    msgs = []
    try:
        result = ollama_setup.install(lambda d, t, m: msgs.append(m), lambda: False)
        assert "running" in result
        assert any("Downloading Ollama" in m for m in msgs)
        st = ollama_setup.status()
        assert st["installed"] and st["managed"] and st["running"]
        assert st["model_installed"]  # the stand-in reports qwen3.5:4b
        assert ollama_setup.start() == "Ollama is running."
    finally:
        import requests
        try:
            requests.get(f"http://127.0.0.1:{port}/quit", timeout=2)
        except Exception:  # noqa: BLE001
            pass
        web.shutdown()
        time.sleep(0.3)


def test_extract_tar_zst_real(tmp_path):
    zstd = pytest.importorskip("zstandard")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        data = b"#!/bin/sh\necho hi\n"
        info = tarfile.TarInfo("bin/ollama")
        info.size, info.mode = len(data), 0o755
        tf.addfile(info, io.BytesIO(data))
    arc = tmp_path / "o.tar.zst"
    arc.write_bytes(zstd.ZstdCompressor().compress(raw.getvalue()))
    out = ollama_setup.extract_tar_zst(arc, tmp_path / "out")
    assert (out / "bin" / "ollama").read_bytes().startswith(b"#!/bin/sh")
