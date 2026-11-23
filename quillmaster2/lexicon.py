"""The Lexicographer: definitions, synonyms and poetry-form checks.

Lookups use Merriam-Webster if you add your free keys in Settings (the same keys
Quill Master 1 used); otherwise the local AI answers, clearly labelled.
"""
import re

import requests

from . import textutil as tu
from .llm import LLMError

FORMS = {
    "haiku": {"lines": 3, "syllables": [5, 7, 5]},
    "tanka": {"lines": 5, "syllables": [5, 7, 5, 7, 7]},
    "limerick": {"lines": 5, "rhyme": "AABBA", "syllables_range": [(7, 11), (7, 11), (4, 7), (4, 7), (7, 11)]},
    "shakespearean sonnet": {"lines": 14, "rhyme": "ABABCDCDEFEFGG", "syllables_each": 10},
    "petrarchan sonnet": {"lines": 14, "rhyme8": "ABBAABBA", "syllables_each": 10},
    "villanelle": {"lines": 19, "refrains": True},
    "quatrain": {"lines": 4},
    "couplet": {"lines": 2, "rhyme": "AA"},
    "free verse": {},
}


def _rhyme_key(word):
    """Crude rhyme sound: last vowel plus what follows, after dropping a silent e."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    if len(w) > 3 and w.endswith("e") and w[-2] not in "aeiouy":
        w = w[:-1]
    m = re.search(r"[aeiouy][^aeiouy]*$", w)
    return m.group(0) if m else w[-2:]


def analyze_poem(text, form=None):
    lines = [l.rstrip() for l in text.strip().splitlines()]
    stanzas = [s for s in re.split(r"\n\s*\n", text.strip()) if s.strip()]
    lines = [l for l in lines if l.strip()]
    info = []
    letters, keys = [], {}
    for l in lines:
        ws = tu.words(l)
        syl = sum(tu.syllables(w) for w in ws)
        k = _rhyme_key(ws[-1]) if ws else ""
        if k not in keys:
            keys[k] = chr(ord("A") + len(keys)) if len(keys) < 26 else "?"
        letters.append(keys[k])
        info.append({"text": l, "syllables": syl, "end_word": ws[-1] if ws else "", "rhyme": keys[k]})
    scheme = "".join(letters)
    guess = form or _guess_form(lines, info, scheme)
    checks = []
    spec = FORMS.get(guess, {})
    if "lines" in spec and len(lines) != spec["lines"]:
        checks.append(f"A {guess} has {spec['lines']} lines; this has {len(lines)}.")
    for i, want in enumerate(spec.get("syllables", [])):
        if i < len(info) and info[i]["syllables"] != want:
            checks.append(f"Line {i + 1} has {info[i]['syllables']} syllables; a {guess} wants {want}.")
    for i, (lo, hi) in enumerate(spec.get("syllables_range", [])):
        if i < len(info) and not lo <= info[i]["syllables"] <= hi:
            checks.append(f"Line {i + 1} has {info[i]['syllables']} syllables; usually {lo}–{hi}.")
    if spec.get("syllables_each"):
        for i, x in enumerate(info):
            if abs(x["syllables"] - spec["syllables_each"]) > 1:
                checks.append(f"Line {i + 1} has {x['syllables']} syllables; pentameter wants about 10.")
    want = spec.get("rhyme") or spec.get("rhyme8")
    if want and len(scheme) >= len(want):
        if not _same_pattern(scheme[:len(want)], want):
            checks.append(f"Rhyme scheme reads {scheme[:len(want)]}; a {guess} wants {want}.")
    if spec.get("refrains") and len(lines) >= 19:
        a1, a2 = lines[0].strip().lower(), lines[2].strip().lower()
        for n, want_line, label in ((5, a1, "A1"), (11, a1, "A1"), (17, a1, "A1"),
                                    (8, a2, "A2"), (14, a2, "A2"), (18, a2, "A2")):
            if lines[n].strip().lower() != want_line:
                checks.append(f"Line {n + 1} should repeat refrain {label}: “{lines[0 if label == 'A1' else 2].strip()}”.")
        if len(stanzas) != 6:
            checks.append(f"A villanelle has 6 stanzas (five tercets and a quatrain); this has {len(stanzas)}.")
    if not checks and guess != "free verse":
        checks.append(f"Fits the {guess} form.")
    note = "Syllable counts are estimates; check words like “fire” or “every” by ear."
    return {"form": guess, "scheme": scheme, "lines": info, "checks": checks, "note": note}


def _same_pattern(a, b):
    m1, m2 = {}, {}
    for x, y in zip(a, b):
        if m1.setdefault(x, y) != y or m2.setdefault(y, x) != x:
            return False
    return True


def _guess_form(lines, info, scheme):
    n = len(lines)
    syl = [x["syllables"] for x in info]
    if n == 3 and syl == [5, 7, 5]:
        return "haiku"
    if n == 5 and syl == [5, 7, 5, 7, 7]:
        return "tanka"
    if n == 5 and _same_pattern(scheme, "AABBA"):
        return "limerick"
    if n == 14:
        return "petrarchan sonnet" if _same_pattern(scheme[:8], "ABBAABBA") else "shakespearean sonnet"
    if n == 19:
        return "villanelle"
    if n == 3:
        return "haiku"
    return "free verse"


def lookup(word, cfg, llm):
    word = word.strip()
    if not word:
        raise ValueError("Type a word.")
    out = {"word": word, "source": None, "definitions": [], "synonyms": [], "syllables": tu.syllables(word)}
    if cfg.get("mw_key"):
        try:
            r = requests.get(f"https://www.dictionaryapi.com/api/v3/references/collegiate/json/{word}",
                             params={"key": cfg["mw_key"]}, timeout=10)
            data = r.json()
            for e in data if isinstance(data, list) else []:
                if isinstance(e, dict):
                    fl = e.get("fl", "")
                    out["definitions"] += [f"({fl}) {d}" for d in e.get("shortdef", [])]
            out["source"] = "Merriam-Webster Collegiate Dictionary"
        except (requests.RequestException, ValueError):
            pass
    if cfg.get("mw_thesaurus_key"):
        try:
            r = requests.get(f"https://www.dictionaryapi.com/api/v3/references/thesaurus/json/{word}",
                             params={"key": cfg["mw_thesaurus_key"]}, timeout=10)
            data = r.json()
            for e in data if isinstance(data, list) else []:
                if isinstance(e, dict):
                    for group in e.get("meta", {}).get("syns", []):
                        out["synonyms"] += group
            out["synonyms"] = list(dict.fromkeys(out["synonyms"]))[:30]
        except (requests.RequestException, ValueError):
            pass
    if (not out["definitions"] or not out["synonyms"]) and getattr(llm, "name", "") != "demo":
        schema = {"type": "object", "properties": {
            "definitions": {"type": "array", "items": {"type": "string"}},
            "synonyms": {"type": "array", "items": {"type": "string"}}},
            "required": ["definitions", "synonyms"]}
        try:
            data = llm.json("You are a careful dictionary. Give 1-3 short definitions (with part of "
                            "speech) and up to 12 synonyms. If the word is invented, say so.",
                            f"Word: {word}", schema)
            if not out["definitions"]:
                out["definitions"] = data.get("definitions", [])[:3]
            if not out["synonyms"]:
                out["synonyms"] = data.get("synonyms", [])[:12]
            out["source"] = (out["source"] + " + local AI") if out["source"] else "Local AI (check important words)"
        except LLMError as e:
            out["error"] = str(e)
    if not out["source"]:
        out["source"] = "No dictionary available: add Merriam-Webster keys in Settings, or use the local AI."
    return out
