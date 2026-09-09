"""Deterministische Variantenbildung und Ähnlichkeitsbewertung für Wortzeichen.

Die EUIPO-API kann nur exakt und Wildcard. Phonetik, Schreibvarianten und
Wortstämme müssen daher hier gebildet werden — reproduzierbar, nicht vom
Modell abhängig. Das Modell darf zusätzliche Varianten beisteuern, der
Grundstock kommt aus diesem Modul.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# Kennzeichnungsschwache oder beschreibende Zusätze, die bei der Stammbildung
# wegfallen. Bewusst konservativ; das Modell kann weitere nennen.
SCHWACHE_ZUSAETZE = {
    "GMBH", "AG", "KG", "OG", "SE", "LTD", "INC", "CO", "GESMBH", "FLEXCO",
    "THE", "DIE", "DER", "DAS", "AND", "UND", "OF", "FOR", "FUER", "MIT",
    "AUSTRIA", "OESTERREICH", "WIEN", "VIENNA", "EUROPE", "EUROPA", "INTERNATIONAL",
    "GROUP", "GRUPPE", "HOLDING", "PARTNER", "PARTNERS",
    "SERVICE", "SERVICES", "SYSTEM", "SYSTEMS", "SOLUTION", "SOLUTIONS",
    "TECH", "TECHNOLOGY", "TECHNOLOGIES", "TECHNIK", "DIGITAL", "ONLINE", "APP",
    "SHOP", "STORE", "MARKT", "MARKET", "STUDIO", "LAB", "LABS",
    "BIO", "ECO", "PRO", "PLUS", "NEW", "NEU", "ORIGINAL", "PREMIUM", "SMART",
    "ONE", "24", "365",
}

_ERSETZUNGEN: list[tuple[str, str]] = [
    ("PH", "F"), ("F", "PH"),
    ("C", "K"), ("K", "C"), ("Z", "C"), ("C", "Z"),
    ("CK", "K"), ("K", "CK"),
    ("Y", "I"), ("I", "Y"),
    ("SS", "S"), ("S", "SS"),
    ("EI", "AI"), ("AI", "EI"), ("EY", "AI"),
    ("IE", "I"), ("I", "IE"),
    ("V", "W"), ("W", "V"), ("V", "F"),
    ("X", "KS"), ("KS", "X"), ("CHS", "X"),
    ("QU", "KW"), ("KW", "QU"),
    ("SCH", "SH"), ("SH", "SCH"),
    ("TZ", "Z"), ("Z", "TZ"),
    ("TH", "T"), ("T", "TH"),
    ("OO", "U"), ("U", "OO"),
    ("EE", "I"), ("EA", "I"),
    ("O", "OU"), ("AU", "OW"),
    ("G", "J"), ("J", "G"), ("J", "Y"), ("Y", "J"),
    ("D", "T"), ("T", "D"), ("B", "P"), ("P", "B"),
]

_DOPPELBAR = "LMNRSTPBDFGK"


def normalisiere(text: str) -> str:
    """Großschreibung, Umlaute aufgelöst, alles außer A-Z0-9 und Leerzeichen entfernt."""
    t = text.strip().upper()
    t = t.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE").replace("ß", "SS")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[-_./&+]", " ", t)
    t = re.sub(r"[^A-Z0-9 ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def woerter(text: str) -> list[str]:
    return [w for w in normalisiere(text).split(" ") if w]


def praegende_bestandteile(text: str) -> list[str]:
    """Wörter ohne kennzeichnungsschwache Zusätze; fällt alles weg, bleibt das längste Wort."""
    ws = woerter(text)
    stark = [w for w in ws if w not in SCHWACHE_ZUSAETZE and len(w) >= 3]
    if not stark and ws:
        stark = [max(ws, key=len)]
    return stark


# ---------------------------------------------------------------------------
# Kölner Phonetik (für deutschsprachige Zeichen; für Englisch tauglich genug
# als Zweitmeinung neben der Editierdistanz)
# ---------------------------------------------------------------------------

def koelner_phonetik(word: str) -> str:
    w = normalisiere(word).replace(" ", "")
    w = re.sub(r"[^A-Z]", "", w)
    if not w:
        return ""
    codes: list[str] = []
    n = len(w)
    for i, ch in enumerate(w):
        prev = w[i - 1] if i > 0 else "#"
        nxt = w[i + 1] if i + 1 < n else "#"
        code = ""
        if ch in "AEIJOUY":
            code = "0"
        elif ch == "H":
            code = "-"
        elif ch == "B":
            code = "1"
        elif ch == "P":
            code = "3" if nxt == "H" else "1"
        elif ch in "DT":
            code = "8" if nxt in "CSZ" else "2"
        elif ch in "FVW":
            code = "3"
        elif ch in "GKQ":
            code = "4"
        elif ch == "C":
            if i == 0:
                code = "4" if nxt in "AHKLOQRUX" else "8"
            elif prev in "SZ":
                code = "8"
            elif nxt in "AHKOQUX":
                code = "4"
            else:
                code = "8"
        elif ch == "X":
            code = "8" if prev in "CKQ" else "48"
        elif ch == "L":
            code = "5"
        elif ch in "MN":
            code = "6"
        elif ch == "R":
            code = "7"
        elif ch in "SZ":
            code = "8"
        codes.append(code)
    raw = "".join(codes)
    # Mehrfachcodes zusammenfassen, H entfernen, Nullen außer am Anfang entfernen
    out: list[str] = []
    for c in raw:
        if c == "-":
            continue
        if out and out[-1] == c:
            continue
        out.append(c)
    result = "".join(out)
    if len(result) > 1:
        result = result[0] + result[1:].replace("0", "")
    return result


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def aehnlichkeit(a: str, b: str) -> float:
    """1.0 = identisch, 0.0 = nichts gemeinsam (normalisierte Editierdistanz)."""
    a, b = normalisiere(a).replace(" ", ""), normalisiere(b).replace(" ", "")
    if not a or not b:
        return 0.0
    return 1.0 - levenshtein(a, b) / max(len(a), len(b))


# ---------------------------------------------------------------------------
# Variantenbildung
# ---------------------------------------------------------------------------

@dataclass
class Variante:
    suchstring: str          # RSQL-Wert, ggf. mit * als Wildcard
    art: str                 # identisch | stamm | wildcard | schreibweise | phonetisch | getrennt | serie | skelett
    begruendung: str
    prioritaet: int = 2      # 1 = immer, 2 = normal, 3 = nur wenn Budget reicht


@dataclass
class VariantenSatz:
    zeichen: str
    normalisiert: str
    staemme: list[str]
    varianten: list[Variante] = field(default_factory=list)

    def suchstrings(self, max_anzahl: int | None = None) -> list[str]:
        gesehen: set[str] = set()
        out: list[str] = []
        for v in sorted(self.varianten, key=lambda v: v.prioritaet):
            if v.suchstring in gesehen:
                continue
            gesehen.add(v.suchstring)
            out.append(v.suchstring)
            if max_anzahl and len(out) >= max_anzahl:
                break
        return out


def _schreibvarianten(stamm: str) -> set[str]:
    out: set[str] = set()
    for alt, neu in _ERSETZUNGEN:
        if alt in stamm:
            out.add(stamm.replace(alt, neu, 1))
            if stamm.count(alt) > 1:
                out.add(stamm.replace(alt, neu))
    # Konsonantenverdopplung / -vereinfachung
    for c in _DOPPELBAR:
        dd = c + c
        if dd in stamm:
            out.add(stamm.replace(dd, c, 1))
        else:
            idx = stamm.find(c, 1)
            if 0 < idx < len(stamm) - 1:
                out.add(stamm[:idx] + dd + stamm[idx + 1:])
    out.discard(stamm)
    # Unsinnige Formen aussortieren: Doppelkonsonant oder CK am Wortanfang
    return {v for v in out if len(v) >= 3 and not v.startswith("CK") and not (v[0] == v[1] and v[0] not in "AEIOU")}


def _vokalvarianten(stamm: str) -> set[str]:
    """Einzelner Vokaltausch (A/E/I/O/U) an jeder Vokalposition — nur bei kurzen Stämmen sinnvoll."""
    out: set[str] = set()
    vokale = "AEIOU"
    for i, ch in enumerate(stamm):
        if ch in vokale:
            for v in vokale:
                if v != ch:
                    out.add(stamm[:i] + v + stamm[i + 1:])
    return out


# Kölner Lautklassen für die Expansion des Wortanfangs: welche Buchstaben klingen am
# Wortanfang gleich. C ist doppeldeutig (Zirkus/Cirque vs. Cabrio) und bekommt beide Klassen.
_LAUTKLASSEN: dict[str, str] = {
    "B": "BP", "P": "BP",
    "D": "DT", "T": "DT",
    "F": "FVW", "V": "FVW", "W": "FVW",
    "G": "GK", "K": "KCG", "Q": "KQ",
    "C": "CKZS",
    "S": "SZC", "Z": "ZSC",
    "M": "MN", "N": "NM",
    "J": "JY", "Y": "YJI",
    "X": "X",
    "L": "L", "R": "R", "H": "H",
}


def _phonetische_wortanfaenge(stamm: str) -> list[str]:
    """Wortanfang mit lautgleich ersetztem erstem Konsonanten als Präfix-Wildcard.

    KIRVO → CIR*, GIR* — damit die Register auch lautgleiche Schreibungen liefern, die eine
    Wortlaut- oder Schreibvariantensuche nie erreicht. Der Wortanfang prägt das Zeichen,
    deshalb wird nur der erste Konsonant variiert; die Bewertung sortiert danach phonetisch.
    Präfixlänge 3 (Stämme mit 5 Buchstaben) bzw. 4 (ab 6), damit die Trefferzahl tragbar bleibt."""
    if len(stamm) < 5:
        return []
    plen = 3 if len(stamm) == 5 else 4
    out: list[str] = []
    for i, ch in enumerate(stamm[:2]):          # erster Konsonant an Position 0 oder 1
        if ch in "AEIOU":
            continue
        for alt in _LAUTKLASSEN.get(ch, ""):
            if alt == ch:
                continue
            praefix = stamm[:i] + alt + stamm[i + 1:plen]
            if praefix not in out:
                out.append(praefix + "*")
        break
    return out


def _skelett(stamm: str) -> str | None:
    """Konsonantenskelett mit Wildcards: KLAROX -> K*L*R*X. Nur für 3–5 Konsonantengruppen."""
    gruppen = re.findall(r"[^AEIOUY]+", stamm)
    if not 3 <= len(gruppen) <= 5:
        return None
    return "*".join(gruppen)


def bilde_varianten(zeichen: str, umfang: str = "normal") -> VariantenSatz:
    """umfang: 'knapp' (Identität + Stamm + Wildcards), 'normal', 'breit' (zusätzlich Vokaltausch)."""
    norm = normalisiere(zeichen)
    staemme = praegende_bestandteile(zeichen)
    satz = VariantenSatz(zeichen=zeichen, normalisiert=norm, staemme=staemme)
    V = satz.varianten

    if not norm:
        return satz

    kompakt = norm.replace(" ", "")
    V.append(Variante(norm, "identisch", "exakter Wortlaut", 1))
    if kompakt != norm:
        V.append(Variante(kompakt, "getrennt", "Bestandteile zusammengeschrieben", 1))
        V.append(Variante(f"*{kompakt}*", "wildcard", "zusammengeschrieben als Bestandteil", 2))

    for st in staemme:
        if len(st) < 4 and len(staemme) > 1:
            # Kurze Bestandteile (FIT, PRO, VIP) liefern als Wildcard nur Rauschen
            continue
        V.append(Variante(f"*{st}*", "stamm", f"prägender Bestandteil „{st}“ als Wortbestandteil", 1))
        if len(st) >= 6:
            praefix = st[: max(4, len(st) - 2)]
            V.append(Variante(f"{praefix}*", "wildcard", f"Wortanfang „{praefix}“ (Serienzeichen, Suffixvarianten)", 2))
        if len(st) >= 7:
            praefix5 = st[:5]
            V.append(Variante(f"{praefix5}*", "wildcard", f"kurzer Wortanfang „{praefix5}“", 3))

        if umfang == "knapp":
            continue

        for sv in sorted(_schreibvarianten(st)):
            V.append(Variante(f"*{sv}*", "schreibweise", f"Schreibvariante von „{st}“", 2))

        sk = _skelett(st)
        if sk and len(st) >= 5:
            V.append(Variante(sk, "skelett", f"Konsonantenskelett von „{st}“ (Vokalvarianten)", 3))

        for pa in _phonetische_wortanfaenge(st):
            V.append(Variante(pa, "phon_anfang", f"lautgleicher Wortanfang zu „{st[:len(pa) - 1]}“", 2))

        if umfang == "breit" and len(st) <= 7:
            for vv in sorted(_vokalvarianten(st)):
                V.append(Variante(f"*{vv}*", "phonetisch", f"Vokaltausch in „{st}“", 3))

    # Mehrwortzeichen: Reihenfolge und Bindestrich spielen für die API keine Rolle,
    # aber Serienzeichen mit gleichem Stamm und anderem Zusatz
    if len(staemme) >= 2:
        V.append(Variante("*" + "*".join(staemme) + "*", "serie", "alle prägenden Bestandteile in dieser Reihenfolge", 2))

    return satz


# ---------------------------------------------------------------------------
# Trefferbewertung
# ---------------------------------------------------------------------------

def bewerte_treffer(zeichen: str, treffer_text: str) -> dict:
    """Zeichenähnlichkeit zwischen Suchzeichen und Treffer (0..1) mit Aufschlüsselung."""
    orig_ganz = normalisiere(zeichen).replace(" ", "")
    hit_ganz = normalisiere(treffer_text).replace(" ", "")
    if not orig_ganz or not hit_ganz:
        return {"score": 0.0, "stufe": "niedrig", "schriftbild": 0.0, "klang": 0.0, "enthalten": False}

    staemme = praegende_bestandteile(zeichen)
    kandidaten_h = [hit_ganz] + woerter(treffer_text)

    def _klang(a: str, b: str) -> float:
        ka, kb = koelner_phonetik(a), koelner_phonetik(b)
        if not ka or not kb:
            return 0.0
        return 1.0 if ka == kb else 1.0 - levenshtein(ka, kb) / max(len(ka), len(kb))

    # Gesamtzeichen gegen Gesamttreffer
    schrift = aehnlichkeit(orig_ganz, hit_ganz)
    klang = _klang(orig_ganz, hit_ganz)
    # Bestandteile: bei einem Stamm zählt der beste Treffer, bei mehreren der Durchschnitt —
    # sonst bekäme „Vita CONTROL“ für „Vita Nova“ dieselbe 1,0 wie „NOVA VITA“.
    if staemme:
        s_best = [max(aehnlichkeit(st, h) for h in kandidaten_h) for st in staemme]
        k_best = [max(_klang(st, h) for h in kandidaten_h) for st in staemme]
        schrift = max(schrift, sum(s_best) / len(s_best))
        klang = max(klang, sum(k_best) / len(k_best))
    kandidaten_o = [orig_ganz] + staemme

    enthalten = any(
        (o in hit_ganz or hit_ganz in o) and len(o) >= 4
        for o in kandidaten_o
    )

    score = max(schrift, 0.9 * klang)
    if enthalten:
        score = min(1.0, score + 0.15)
    if orig_ganz == hit_ganz:
        score = 1.0

    stufe = "hoch" if score >= 0.85 else "mittel" if score >= 0.65 else "niedrig"
    return {
        "score": round(score, 3),
        "stufe": stufe,
        "schriftbild": round(schrift, 3),
        "klang": round(klang, 3),
        "enthalten": enthalten,
    }
