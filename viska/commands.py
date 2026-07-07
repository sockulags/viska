"""Röstkommandon: talade fraser som blir formatering i stället för text.

"ny rad" och "nytt stycke" (samt engelska motsvarigheter) ersätts med
radbrytningar efter transkriberingen. Frasen matchas som egna ord och
slukar omgivande skiljetecken — Whisper skriver gärna "Ny rad." med
punkt och versal. Kommandolistan ligger i config (voice_commands) så
att fraser kan läggas till eller tas bort.
"""

import re


def apply(text: str, commands: dict) -> str:
    if not text or not commands:
        return text
    # Längsta frasen först så att "nytt stycke" inte hinner halvmatchas
    # av en kortare fras med samma början.
    for phrase in sorted(commands, key=len, reverse=True):
        replacement = commands[phrase]
        # Komma före frasen slukas ("det här, ny rad" → "det här\n") men
        # meningsavslutare lämnas kvar — punkten tillhör förra meningen.
        pattern = re.compile(
            r"[ \t]*,?[ \t]*\b" + re.escape(phrase) + r"\b[.,!?]*",
            re.IGNORECASE,
        )
        text = pattern.sub(lambda _m, r=replacement: r, text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)  # inga hängande mellanslag
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" ")
