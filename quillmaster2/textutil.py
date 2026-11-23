"""Small text helpers shared by the passes and workbenches. No AI here."""
import re

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
QUOTE_RE = re.compile(r"“[^”]*”|\"[^\"\n]*\"")
SENT_RE = re.compile(r"[^.!?…]+(?:[.!?…]+[”\"’']?|$)", re.S)

STOPWORDS = set("""a an the and or but if then than so of to in on at by for with from into onto over
under about after before as is was were be been being am are has have had do does did not no nor
this that these those it its it's he she they we i you him her them us me my your his their our
there here what which who whom whose when where why how all any some each every both few more most
other such only own same too very can will just should would could may might must shall up down out
off again further once said says say like one two back now still even also into through upon""".split())

TAG_VERBS = ("said|asked|replied|whispered|shouted|called|muttered|murmured|answered|added|"
             "snapped|cried|yelled|breathed|growled|hissed|told|began|continued|admitted|insisted")

FILTER_VERBS = "saw|heard|felt|noticed|realized|realised|watched|wondered|knew|seemed to|decided"

CLICHES = [
    "heart pounding", "heart pounded in", "blood ran cold", "in the nick of time",
    "at the end of the day", "dead of night", "piercing blue eyes", "time stood still",
    "a chill ran down", "shiver down (?:her|his|my|their) spine", "every fiber of",
    "deafening silence", "as luck would have it", "calm before the storm", "white as a ghost",
    "let out a breath (?:she|he|i|they) didn'?t know", "cold as ice", "crystal clear",
    "dark and stormy", "few and far between", "fit as a fiddle", "frozen in fear",
    "in the blink of an eye", "last but not least", "lips curled into a smile",
    "only time will tell", "quiet as a mouse", "scared to death", "sent shivers",
    "stopped dead in (?:her|his|my|their) tracks", "the calm before", "think outside the box",
    "tip of the iceberg", "without a doubt", "all hell broke loose", "bated breath",
    "a sea of faces", "stark contrast", "eyes like saucers", "weak at the knees",
]
CLICHE_RE = re.compile(r"\b(?:" + "|".join(CLICHES) + r")\b", re.I)

SENSES = {
    "sight": "saw see seen look looked glance glanced glow glowed gleam bright dark shadow color colour "
             "red blue green gold grey gray pale light flicker glitter shine shone dim",
    "sound": "heard hear sound noise whisper whispered creak creaked hum hummed ring rang roar roared "
             "silence silent loud quiet echo echoed crack cracked thud clatter rustle rustled",
    "smell": "smell smelled smelt scent stench reek reeked odor odour fragrance aroma perfume smoke "
             "musty sour rot rotting sniffed",
    "touch": "touch touched cold warm hot rough smooth soft hard wet damp sticky sharp ache ached "
             "shiver shivered grip gripped brush brushed texture prickle sting",
    "taste": "taste tasted tongue sweet bitter salt salty sour savory savoury flavor flavour "
             "swallowed sip sipped chewed",
}
SENSE_WORDS = {w: k for k, ws in SENSES.items() for w in ws.split()}
TIME_WORDS = re.compile(r"\b(morning|noon|afternoon|evening|night|dawn|dusk|midnight|day|days|"
                        r"week|weeks|month|year|years|hour|hours|today|yesterday|tomorrow|later|"
                        r"spring|summer|autumn|fall|winter|sunrise|sunset|o'clock|minutes)\b", re.I)


def words(text):
    return WORD_RE.findall(text)


def word_count(text):
    return len(words(text))


def paragraphs(text):
    """[(start, end, text)] for each paragraph."""
    out, pos = [], 0
    for part in re.split(r"(\n\s*\n)", text):
        if part.strip() and not re.fullmatch(r"\n\s*\n", part):
            out.append((pos, pos + len(part), part))
        pos += len(part)
    return out


def sentences(text):
    """[(start, end, text)] — rough, but stable."""
    out = []
    for m in SENT_RE.finditer(text):
        s = m.group(0)
        if s.strip():
            lead = len(s) - len(s.lstrip())
            out.append((m.start() + lead, m.end(), s.strip()))
    return out


def dialogue_spans(text):
    return [(m.start(), m.end()) for m in QUOTE_RE.finditer(text)]


def narration(text):
    """The text with dialogue blanked out (same length, so offsets still match)."""
    chars = list(text)
    for s, e in dialogue_spans(text):
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def dialogue_ratio(text):
    total = word_count(text)
    if not total:
        return 0.0
    d = sum(word_count(text[s:e]) for s, e in dialogue_spans(text))
    return d / total


def anchor(text, quote, near=0):
    """Find a quote in text. Returns (start, end) or None. Tolerates spacing and quote style."""
    if not quote:
        return None
    q = quote.strip().strip('"“”')
    if not q:
        return None
    i = text.find(q, max(0, near - 200)) if near else -1
    if i < 0:
        i = text.find(q)
    if i >= 0:
        return i, i + len(q)
    pattern = re.escape(re.sub(r"\s+", " ", q))
    pattern = pattern.replace(r"\ ", r"\s+").replace("'", "['’]").replace('"', '["“”]')
    m = re.search(pattern, text, re.I)
    return (m.start(), m.end()) if m else None


def syllables(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|[^laeiouy]ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    n = len(re.findall(r"[aeiouy]{1,2}", w))
    return max(1, n)
