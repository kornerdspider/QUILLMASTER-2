"""Getting Ollama (the local AI engine) onto the computer from inside Quill Master.

Windows: downloads Ollama's official installer (OllamaSetup.exe) and opens it. It installs
         for this user only, no administrator needed.
Mac:     downloads the official Ollama app, puts it in Applications and opens it.
Linux:   downloads the official Ollama package and unpacks it into Quill Master's own data
         folder, so no admin password is needed. Quill Master starts it when needed.
"""
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import requests

from . import config

URLS = {
    "windows": "https://ollama.com/download/OllamaSetup.exe",
    "mac": "https://ollama.com/download/Ollama-darwin.zip",
    "linux-amd64": "https://ollama.com/download/ollama-linux-amd64.tar.zst",
    "linux-arm64": "https://ollama.com/download/ollama-linux-arm64.tar.zst",
}
RECOMMENDED_MODEL = "qwen3.5:4b"
RECOMMENDED_EMBED = "nomic-embed-text"


def system():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def managed_dir():
    """Where Quill Master keeps its own copy of Ollama on Linux."""
    return config.data_dir() / "ollama"


def _candidates():
    s = system()
    out = []
    if s == "windows":
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        base = local / "Programs" / "Ollama"
        out += [base / "ollama app.exe", base / "ollama.exe"]
    elif s == "mac":
        out += [Path("/Applications/Ollama.app"), Path.home() / "Applications" / "Ollama.app",
                Path("/usr/local/bin/ollama"), Path("/opt/homebrew/bin/ollama")]
    else:
        out += [managed_dir() / "bin" / "ollama", Path("/usr/local/bin/ollama"), Path("/usr/bin/ollama")]
    which = shutil.which("ollama")
    if which:
        out.append(Path(which))
    return out


def find_ollama():
    for p in _candidates():
        if p.exists():
            return p
    return None


def running(cfg=None):
    cfg = cfg or config.load()
    try:
        r = requests.get(f"{cfg['ollama_url'].rstrip('/')}/api/tags", timeout=3)
        return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def has_model(models, name):
    if not models or not name:
        return False
    return any(m == name or m == f"{name}:latest" or (":" not in name and m.split(":")[0] == name)
               for m in models)


def status():
    cfg = config.load()
    path = find_ollama()
    models = running(cfg)
    return {
        "system": system(),
        "installed": path is not None,
        "path": str(path) if path else None,
        "managed": bool(path and managed_dir() in path.parents),
        "running": models is not None,
        "models": models or [],
        "model": cfg["model"],
        "model_installed": has_model(models, cfg["model"]),
        "embed_model": cfg.get("embed_model"),
        "embed_installed": has_model(models, cfg.get("embed_model")),
        "engine": cfg["engine"],
        "recommended": RECOMMENDED_MODEL,
        "download_url": "https://ollama.com/download",
    }


# ---------------------------------------------------------------- start

def start(wait_s=40):
    """Start Ollama if it's installed but not running. Returns a status message."""
    if running() is not None:
        return "Ollama is running."
    path = find_ollama()
    if not path:
        raise RuntimeError("Ollama isn't installed yet.")
    s = system()
    log = open(config.data_dir() / "ollama.log", "ab")
    if s == "windows":
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        app = path.parent / "ollama app.exe"
        if app.exists():
            subprocess.Popen([str(app)], creationflags=flags, close_fds=True)
        else:
            subprocess.Popen([str(path.parent / "ollama.exe"), "serve"], stdout=log, stderr=log,
                             creationflags=flags | 0x08000000, close_fds=True)  # CREATE_NO_WINDOW
    elif s == "mac" and path.suffix == ".app":
        subprocess.Popen(["open", "-a", str(path), "--args", "hidden"])
    else:
        env = dict(os.environ)
        lib = path.parent.parent / "lib" / "ollama"
        if lib.exists():
            env["LD_LIBRARY_PATH"] = f"{lib}:{env.get('LD_LIBRARY_PATH', '')}"
        subprocess.Popen([str(path), "serve"], stdout=log, stderr=log, env=env,
                         start_new_session=True, close_fds=True)
    end = time.time() + wait_s
    while time.time() < end:
        if running() is not None:
            return "Ollama started."
        time.sleep(1)
    raise RuntimeError("Ollama didn't answer after starting. Try again, or restart the computer.")


def autostart():
    """Called when Quill Master opens: quietly start Ollama if it's installed."""
    try:
        if config.load().get("engine") == "ollama" and running() is None and find_ollama():
            start(wait_s=30)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- install

def _download(url, dest, progress, stop, label):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        if r.status_code != 200:
            raise RuntimeError(f"Couldn't download Ollama (error {r.status_code}). Check your internet.")
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if stop():
                    raise RuntimeError("Download stopped.")
                f.write(chunk)
                done += len(chunk)
                pct = f" {done / total:.0%}" if total else ""
                progress(done >> 20, max(1, total >> 20),
                         f"{label}: {done >> 20:,} of {total >> 20:,} MB{pct}" if total
                         else f"{label}: {done >> 20:,} MB")
    tmp.replace(dest)
    return dest


def _zstd_reader(fh):
    try:
        from compression import zstd  # Python 3.14+
        return zstd.ZstdFile(fh)
    except ImportError:
        pass
    try:
        import zstandard
        return zstandard.ZstdDecompressor().stream_reader(fh)
    except ImportError:
        return None


def extract_tar_zst(archive, dest):
    """Unpack a .tar.zst. Uses Python if it can, or the system's tar/zstd."""
    dest.mkdir(parents=True, exist_ok=True)
    with open(archive, "rb") as fh:
        reader = _zstd_reader(fh)
        if reader is not None:
            with tarfile.open(fileobj=reader, mode="r|") as tf:
                if hasattr(tarfile, "data_filter"):
                    tf.extractall(dest, filter="data")
                else:
                    tf.extractall(dest)
            return dest
    for cmd in (["tar", "--zstd", "-xf", str(archive), "-C", str(dest)],
                ["sh", "-c", f'zstd -dc "{archive}" | tar -xf - -C "{dest}"']):
        try:
            if subprocess.run(cmd, capture_output=True).returncode == 0:
                return dest
        except FileNotFoundError:
            continue
    raise RuntimeError("Couldn't unpack Ollama: this computer needs 'zstd'. On Linux Mint run: "
                       "sudo apt install zstd   then try again.")


def install(progress, stop):
    """Download and install Ollama for this computer, then start it."""
    s = system()
    dl = config.data_dir() / "downloads"
    if s == "windows":
        exe = _download(URLS["windows"], dl / "OllamaSetup.exe", progress, stop, "Downloading Ollama")
        progress(1, 1, "The Ollama installer is open. Click Install, then wait here.")
        os.startfile(str(exe))  # noqa: S606 (Windows only)
        end = time.time() + 20 * 60
        while time.time() < end:
            if stop():
                return "paused"
            if running() is not None:
                return "Ollama is installed and running."
            if find_ollama():
                progress(1, 1, "Ollama is installed. Starting it…")
                try:
                    start(wait_s=20)
                    return "Ollama is installed and running."
                except RuntimeError:
                    pass
            time.sleep(3)
        raise RuntimeError("The Ollama installer didn't finish. Open it again from Settings.")
    if s == "mac":
        z = _download(URLS["mac"], dl / "Ollama-darwin.zip", progress, stop, "Downloading Ollama")
        progress(1, 1, "Putting Ollama in Applications…")
        target = Path("/Applications")
        if not os.access(target, os.W_OK):
            target = Path.home() / "Applications"
            target.mkdir(exist_ok=True)
        old = target / "Ollama.app"
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        r = subprocess.run(["ditto", "-x", "-k", str(z), str(target)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Couldn't unzip Ollama: {r.stderr[:200]}")
        progress(1, 1, "Starting Ollama…")
        start(wait_s=60)
        return f"Ollama is installed in {target} and running."
    arch = "arm64" if platform.machine().lower() in ("aarch64", "arm64") else "amd64"
    archive = _download(URLS[f"linux-{arch}"], dl / f"ollama-linux-{arch}.tar.zst", progress, stop,
                        "Downloading Ollama (large: it includes graphics-card support)")
    progress(1, 1, "Unpacking Ollama…")
    dest = managed_dir()
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    extract_tar_zst(archive, dest)
    binary = dest / "bin" / "ollama"
    if not binary.exists():
        raise RuntimeError("Ollama unpacked, but its program file wasn't where expected.")
    binary.chmod(0o755)
    try:
        archive.unlink()
    except OSError:
        pass
    progress(1, 1, "Starting Ollama…")
    start(wait_s=40)
    return "Ollama is installed and running."


def check_model(llm_client):
    """A tiny request to prove the model answers. Returns seconds taken."""
    t = time.time()
    reply = llm_client.text("Reply with the single word: ready", "Are you ready?")
    return {"reply": reply.strip()[:80], "seconds": round(time.time() - t, 1)}
