"""The model the Archivist talks to.

OllamaLLM  - the real thing: a local model through Ollama.
DemoLLM    - a simple pattern-matcher so the screens can be tried without
             Ollama. It only spots a few sentence patterns. Not an AI.
"""
import json
import re

import requests

FIELDS_HINT = (
    "age, eyes, hair, height, build, appearance, occupation, title, family, "
    "relationship, home, birthplace, goal, fear, secret, voice, skill, "
    "location, holder, status, description, rule, history"
)

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "pov": {"type": "string"},
        "location": {"type": "string"},
        "time": {"type": "string"},
        "characters_present": {"type": "array", "items": {"type": "string"}},
        "facts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"},
                "type": {"type": "string", "enum": ["character", "place", "object", "rule"]},
                "field": {"type": "string"},
                "value": {"type": "string"},
                "quote": {"type": "string"},
            },
            "required": ["entity", "type", "field", "value", "quote"],
        }},
        "events": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "when": {"type": "string"}},
            "required": ["description"],
        }},
        "threads": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string",
                         "enum": ["opens", "rises", "turns", "resolves", "mentions"]},
                "note": {"type": "string"},
            },
            "required": ["name", "role"],
        }},
        "unclear": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "entity": {"type": "string"}, "field": {"type": "string"},
                "question": {"type": "string"},
            },
            "required": ["question"],
        }},
    },
    "required": ["summary", "characters_present", "facts", "events", "threads"],
}

EXTRACT_SYSTEM = f"""You are the Archivist, building a Story Bible for a novelist.
Read ONE scene and record only what the text states. Never invent or guess.

Return JSON with:
- summary: 1-2 plain sentences: who does what, and what changes.
- pov: whose point of view, or "" if unclear.
- location, time: where and when the scene happens ("" if not stated).
- characters_present: names of characters who appear or speak.
- facts: concrete facts the text states about characters, places, objects or
  world rules. Use short lowercase field names such as: {FIELDS_HINT}.
  value = a few words. quote = the exact words from the scene (under 20 words)
  that state it. Skip feelings that pass within the scene.
- events: things that happen that matter later. when = any time words given.
- threads: plot threads or mysteries this scene touches. Use the same short
  name every time (e.g. "the locked box"). role = opens, rises, turns,
  resolves or mentions.
- unclear: at most 2 things the story leans on but does not make clear,
  as short questions to ask the author.
Use the names already known when they refer to the same person or place."""

SUMMARY_SYSTEM = """You summarize fiction for its author. Plain, specific, present tense.
Name characters. Say what happens and what changes. No praise, no advice."""

ASK_SYSTEM = """You answer the author's questions about their own story, using ONLY the
Story Bible notes and passages given. Cite the fact numbers you used like [F12].
If the notes don't answer it, say so plainly. Never invent."""


class LLMError(RuntimeError):
    pass


class OllamaLLM:
    name = "ollama"

    def __init__(self, cfg):
        self.cfg = cfg
        self.url = cfg["ollama_url"].rstrip("/")

    def _chat(self, system, user, schema=None):
        body = {
            "model": self.cfg["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "stream": False,
            "options": {"num_ctx": self.cfg["num_ctx"],
                        "temperature": self.cfg["temperature"]},
        }
        if schema is not None:
            body["format"] = schema
        if self.cfg.get("think") is not None:
            body["think"] = self.cfg["think"]
        try:
            r = requests.post(f"{self.url}/api/chat", json=body,
                              timeout=self.cfg["request_timeout_s"])
        except requests.RequestException as e:
            raise LLMError(f"Can't reach Ollama at {self.url}. Is it running? ({e})")
        if r.status_code != 200:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json().get("message", {}).get("content", "")

    def json(self, system, user, schema):
        return parse_json(self._chat(system, user, schema=schema))

    def text(self, system, user):
        return self._chat(system, user).strip()

    def embed(self, texts):
        model = self.cfg.get("embed_model") or "nomic-embed-text"
        try:
            r = requests.post(f"{self.url}/api/embed", json={"model": model, "input": texts},
                              timeout=self.cfg["request_timeout_s"])
        except requests.RequestException as e:
            raise LLMError(f"Can't reach Ollama for embeddings ({e})")
        if r.status_code != 200:
            raise LLMError(f"Embedding model '{model}' isn't available "
                           f"(run: ollama pull {model})")
        return r.json().get("embeddings", [])

    def installed(self):
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            return [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:  # noqa: BLE001
            return None

    def health(self):
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=5)
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"Ollama is not running at {self.url} ({e})"}
        model = self.cfg["model"]
        if not any(n == model or n.split(":")[0] == model for n in names):
            return {"ok": False,
                    "message": f"Ollama is running, but the model '{model}' isn't installed. "
                               f"Installed: {', '.join(names) or 'none'}"}
        return {"ok": True, "message": f"Ollama ready with {model}"}

    def extract(self, scene_text, known_names, context):
        user = (f"{context}\nKnown names so far: {', '.join(known_names[:80]) or 'none'}\n\n"
                f"SCENE:\n{scene_text}")
        raw = self._chat(EXTRACT_SYSTEM, user, schema=EXTRACT_SCHEMA)
        return parse_json(raw)

    def summarize(self, texts, what):
        user = f"Summarize this {what} in one paragraph.\n\n" + "\n".join(f"- {t}" for t in texts)
        return self._chat(SUMMARY_SYSTEM, user).strip()

    def synopsis(self, book_title, chapter_summaries):
        user = (f"Write a 1-2 page synopsis of '{book_title}' for agents and publishers. "
                "Present tense, ending included, main storyline first.\n\n"
                + "\n".join(f"Chapter {i}: {s}" for i, s in enumerate(chapter_summaries, 1)))
        return self._chat(SUMMARY_SYSTEM, user).strip()

    def ask(self, question, notes):
        return self._chat(ASK_SYSTEM, f"NOTES:\n{notes}\n\nQUESTION: {question}").strip()


def parse_json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except ValueError:
                pass
    raise LLMError("The model didn't return readable JSON for this scene.")


# --------------------------------------------------------------------------

_STOP = set("""The A An And But Or So Then When While After Before If It Its He She They We I
You His Her Their Our My Your This That These Those There Here What Who Why How Where
Chapter Part Book Prologue Epilogue Yes No Not Mr Mrs Ms Dr Sir Lady Lord Captain""".split())


class DemoLLM:
    """Spots a handful of patterns, e.g. "Mara's eyes were grey" or
    "Tavin was 19". Good enough to show how the Bible and Q&A work."""
    name = "demo"

    def health(self):
        return {"ok": True, "message": "Demo mode: simple pattern reader, not an AI"}

    def json(self, system, user, schema):
        return {}

    def text(self, system, user):
        return ""

    def extract(self, text, known_names, context):
        facts = []

        def add(entity, typ, field, value, m):
            facts.append({"entity": entity, "type": typ, "field": field,
                          "value": value, "quote": m.group(0)})

        for m in re.finditer(r"\b([A-Z][a-z]+)'s (eyes|hair) (?:were|was) ([a-z]+)", text):
            add(m.group(1), "character", m.group(2), m.group(3), m)
        for m in re.finditer(r"\b([A-Z][a-z]+) was (\d{1,3}|[a-z]+(?:-[a-z]+)?) years old", text):
            add(m.group(1), "character", "age", m.group(2), m)
        for m in re.finditer(r"\b([A-Z][a-z]+) was the ([a-z]+(?: [a-z]+)?) of ([A-Z][a-z]+)", text):
            add(m.group(1), "character", "title", f"{m.group(2)} of {m.group(3)}", m)
        for m in re.finditer(r"\b([A-Z][a-z]+) (?:carried|held|took) the ([a-z]+ [a-z]+)", text):
            add("the " + m.group(2), "object", "holder", m.group(1), m)

        present = []
        for m in re.finditer(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b", text):
            w = m.group(1)
            if w not in _STOP and w not in present:
                present.append(w)
        present = [p for p in present if re.search(rf"\b{p}\b (said|asked|turned|walked|"
                                                   rf"looked|smiled|shouted|whispered|ran|"
                                                   rf"nodded|was|had)", text)] or present[:3]

        loc = ""
        m = re.search(r"\b(?:in|at|reached|entered) ([A-Z][a-z]{2,})", text)
        if m and m.group(1) not in present:
            loc = m.group(1)
            facts.append({"entity": loc, "type": "place", "field": "description",
                          "value": "a place in the story", "quote": m.group(0)})

        threads = []
        for m in re.finditer(r"\bthe ((?:locked|silver|black|old|broken) [a-z]+)", text):
            name = "the " + m.group(1)
            if any(t["name"] == name for t in threads):
                continue
            tail = text[m.end():m.end() + 80].lower()
            done = re.search(r"\b(opened|solved|destroyed|unlocked)\b", tail)
            role = "resolves" if done and not re.search(r"\bnot\b|n't", tail) else "mentions"
            threads.append({"name": name, "role": role, "note": ""})

        first = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0]
        return {"summary": first[:220], "pov": present[0] if present else "",
                "location": loc, "time": "", "characters_present": present[:8],
                "facts": facts, "events": [{"description": first[:160], "when": ""}],
                "threads": threads, "unclear": []}

    def summarize(self, texts, what):
        return " ".join(t for t in texts[:6] if t)[:900]

    def synopsis(self, book_title, chapter_summaries):
        return f"{book_title}: " + " ".join(chapter_summaries)[:3000]

    def ask(self, question, notes):
        return ("Demo mode can't answer free questions. Here are the Bible notes that "
                "match your words:\n\n" + (notes or "(none found)"))


def get(cfg):
    return DemoLLM() if cfg.get("engine") == "demo" else OllamaLLM(cfg)
