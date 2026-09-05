"""Härleder ersättningsregler ur en rättad diktering.

Användaren rättar den senast dikterade texten i en ruta; skillnaden mot
originalet blir förslag på regler ("hört" → "skriv"). Bara ordbyten blir
regler: rena strykningar och tillägg beror nästan alltid på vad som sades
den gången, inte på hur Whisper hör ett visst ord, och skulle bli regler
som ändrar framtida dikteringar på måfå.
"""

import difflib
import re

# Skiljetecken i kanterna hör till meningen, inte till ordet som hördes fel.
_EDGE = " \t\n.,;:!?\"'()[]{}…-–—"

# Längre spann än så här är en omformulering, inte ett felhört ord.
MAX_WORDS = 4


def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def derive_rules(original: str, corrected: str) -> list[tuple[str, str]]:
    """Ordparen som skiljer texterna åt, i den ordning de förekommer.

    Returnerar (hörd fras, rätt text). Tomma listan när inget bytts ut.
    """
    a, b = _tokens(original), _tokens(corrected)
    if not a or not b:
        return []
    rules: list[tuple[str, str]] = []
    seen: set[str] = set()
    matcher = difflib.SequenceMatcher(a=[t.lower() for t in a], b=[t.lower() for t in b])
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        if i2 - i1 > MAX_WORDS or j2 - j1 > MAX_WORDS:
            continue
        if i2 - i1 == j2 - j1:
            # Lika många ord på båda sidor: para ihop dem ett och ett, annars
            # blir två orelaterade felstavningar i rad en enda lång regel som
            # bara träffar när precis samma mening dikteras igen.
            pairs = list(zip(a[i1:i2], b[j1:j2]))
        else:
            pairs = [(" ".join(a[i1:i2]), " ".join(b[j1:j2]))]
        for raw_heard, raw_write in pairs:
            heard = raw_heard.strip(_EDGE)
            write = raw_write.strip(_EDGE)
            if not heard or not write or heard == write:
                continue
            key = heard.lower()
            if key in seen:
                continue
            seen.add(key)
            rules.append((heard, write))
    return rules
