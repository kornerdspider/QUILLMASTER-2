"""Settings, stored as config.json in the data folder.

Data folder:
  Windows: C:\\Users\\<you>\\QuillMaster2Data
  Mac:     /Users/<you>/QuillMaster2Data
  Linux:   /home/<you>/QuillMaster2Data
Override with the QM2_DATA environment variable.
"""
import json
import os
from pathlib import Path

DEFAULTS = {
    # "ollama" uses the local model. "demo" uses a simple built-in reader so
    # the screens can be tried without Ollama (it is NOT a real AI).
    "engine": "ollama",
    "ollama_url": "http://127.0.0.1:11434",
    # Set this to the same small model the Mac build of Quill Master 1 uses.
    "model": "qwen3.5:4b",
    "num_ctx": 8192,
    "temperature": 0.2,
    # Thinking off keeps extraction fast. Set to null if your model complains.
    "think": False,
    "port": 8765,
    # Scenes longer than this are cut into parts before the model reads them.
    "max_scene_words": 2500,
    "request_timeout_s": 600,
    # Used by the Concordance to spot scenes that cover the same beat.
    "embed_model": "nomic-embed-text",
    # If LanguageTool is running (Quill Master 1 used it), grammar uses it.
    "languagetool_url": "http://127.0.0.1:8081",
    "language": "en-US",
    # Story framework for the Structure pass: 7-point, three-act, hero's journey, save the cat
    "framework": "7-point",
    # Optional Merriam-Webster key for the Lexicon (leave empty to use the local AI).
    "mw_key": "",
    "mw_thesaurus_key": "",
    # Ask before applying changes to the manuscript (make a backup first).
    "ask_backup": True,
}


def data_dir() -> Path:
    d = Path(os.environ.get("QM2_DATA") or (Path.home() / "QuillMaster2Data"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "uploads").mkdir(exist_ok=True)
    (d / "backups").mkdir(exist_ok=True)
    return d


def config_path() -> Path:
    return data_dir() / "config.json"


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = config_path()
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            pass
    else:
        save(cfg)
    return cfg


def save(cfg: dict) -> dict:
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    config_path().write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged
