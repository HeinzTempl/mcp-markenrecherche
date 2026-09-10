"""TMview-Anbindung (tmdn.org) — nationale Register (AT, DE, CH, …) und WIPO.

TMview hat keine dokumentierte API. Die Weboberfläche holt ihre Daten über
interne JSON-Endpunkte, die hier direkt angesprochen werden:

    POST /tmview/api/search/results            Trefferliste (Kurzdaten)
    GET  /tmview/api/trademark/detail/{ST13}   Vollauszug inkl. Waren-/DL-Verzeichnis

Der Detail-Endpunkt verlangt eine Browser-Session (Cookies + XSRF-Token), die
beim ersten Aufruf der Startseite eingesammelt wird. Ein Browser-User-Agent ist
Pflicht, sonst antwortet der Server mit 403.

Das ist eine interne Schnittstelle: sie kann sich ohne Ankündigung ändern, und
die Nutzungsbedingungen von tmdn.org sind auf manuelle Nutzung ausgelegt. tmdn.org
sitzt hinter einem Bot-Schutz (F5/ASM): nach einigen Dutzend automatisierten
Abfragen in kurzer Zeit wird die Adresse vorübergehend auf /error/revise.html
umgeleitet — auch für den Browser. Deshalb: 1,5 s Pause (TMVIEW_PAUSE), wenige
Abfragen je Recherche, beim ersten Anzeichen einer Sperre sofort aufhören, und jeder
Fehler wird als Recherchelücke gemeldet statt still verschluckt.

Suchmodi (criteria): C = enthält (Wildcards * erlaubt), E = exaktes Wort,
F = unscharf (TMview-eigene Fuzzy-Suche), W = Wörter.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

BASE = "https://www.tmdn.org/tmview"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Status-Texte von TMview sind freie Strings je Amt; „tot" wird über Schlüsselwörter erkannt.
_TOT_WOERTER = ("ended", "expired", "withdrawn", "refused", "cancel", "surrender",
                "invalid", "revoked", "lapsed", "abandon", "rejected", "dead", "removed")

MODI = {"enthaelt": "C", "exakt": "E", "unscharf": "F", "woerter": "W"}


class TMviewFehler(Exception):
    pass


class TMviewBlockiert(TMviewFehler):
    """tmdn.org leitet auf /error/revise.html um oder liefert eine JS-Challenge statt JSON:
    der Bot-Schutz (F5/ASM) hat die Adresse vorübergehend gesperrt. Weitere Abfragen
    verlängern die Sperre — sofort aufhören und als Recherchelücke melden."""


def status_tot(status: str | None) -> bool:
    s = (status or "").lower()
    return any(w in s for w in _TOT_WOERTER)


class TMviewClient:
    def __init__(self, timeout: float = 30.0, pause: float | None = None) -> None:
        self.pause = float(os.getenv("TMVIEW_PAUSE", pause if pause is not None else 1.5))
        self.blockiert: str | None = None   # gesetzt, sobald der Bot-Schutz zugeschlagen hat
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": _UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "de-AT,de;q=0.9,en;q=0.8",
                "Referer": f"{BASE}/",
                "Origin": "https://www.tmdn.org",
            },
        )
        self._session_ok = False
        self._letzter_aufruf = 0.0

    # -- Session ----------------------------------------------------------------

    def _pruefe_block(self, r: httpx.Response, erwartet_json: bool = True) -> None:
        ort = str(r.url)
        umleitungen = [str(h.headers.get("location", "")) for h in r.history]
        if "/error/" in ort or "revise" in ort or any("/error/" in u or "revise" in u for u in umleitungen):
            self.blockiert = f"Umleitung auf {ort}"
            raise TMviewBlockiert("TMview hat die Adresse vorübergehend gesperrt (Bot-Schutz, "
                                  f"{self.blockiert}) — später erneut versuchen, Abfragen reduzieren")
        # Die Startseite ist IMMER HTML und traegt IMMER das Bot-Schutz-Skript des F5-Shape-
        # Systems (APM_DO_NOT_TOUCH). Das ist der Normalzustand, keine Sperre: die JSON-
        # Endpunkte antworten trotzdem. Nur wenn ein JSON-Endpunkt HTML zurueckgibt, hat der
        # Bot-Schutz die Abfrage tatsaechlich abgefangen.
        if not erwartet_json:
            return
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "html" in ct and ("APM_DO_NOT_TOUCH" in r.text[:2000] or "<html" in r.text[:200].lower()):
            self.blockiert = "JavaScript-Challenge statt JSON"
            raise TMviewBlockiert("TMview liefert eine Bot-Challenge statt Daten — Adresse vorübergehend "
                                  "gesperrt, später erneut versuchen")

    def _session(self) -> None:
        if self.blockiert:
            raise TMviewBlockiert(f"TMview gesperrt ({self.blockiert}) — in dieser Sitzung keine weiteren Abfragen")
        if self._session_ok:
            return
        r = self._http.get(f"{BASE}/")
        self._pruefe_block(r, erwartet_json=False)
        if r.status_code >= 400:
            raise TMviewFehler(f"TMview-Startseite nicht erreichbar ({r.status_code})")
        self._session_ok = True

    def _warte(self) -> None:
        delta = time.time() - self._letzter_aufruf
        if delta < self.pause:
            time.sleep(self.pause - delta)
        self._letzter_aufruf = time.time()

    def _xsrf(self) -> dict[str, str]:
        tok = self._http.cookies.get("XSRF-TOKEN")
        return {"X-XSRF-TOKEN": tok} if tok else {}

    # -- Suche ------------------------------------------------------------------

    def suche(
        self,
        text: str,
        aemter: list[str],
        modus: str = "enthaelt",
        klassen: list[int] | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict:
        self._session()
        self._warte()
        body: dict[str, Any] = {
            "page": str(page),
            "pageSize": str(page_size),
            "criteria": MODI.get(modus, "C"),
            "basicSearch": text,
            "offices": aemter,
            "fOffices": aemter,
        }
        if klassen:
            body["niceClass"] = [str(k) for k in klassen]
            body["fNiceClass"] = [str(k) for k in klassen]
        r = self._http.post(f"{BASE}/api/search/results", json=body, headers=self._xsrf())
        self._pruefe_block(r)
        if r.status_code >= 400:
            raise TMviewFehler(f"TMview-Suche → {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise TMviewFehler(f"TMview-Suche: keine JSON-Antwort ({r.text[:120]!r})") from e

    def suche_alle(self, text: str, aemter: list[str], modus: str = "enthaelt",
                   klassen: list[int] | None = None, max_treffer: int = 200) -> tuple[list[dict], int, bool]:
        treffer: list[dict] = []
        page = 1
        gesamt = 0
        while True:
            data = self.suche(text, aemter, modus=modus, klassen=klassen, page=page, page_size=100)
            gesamt = int(data.get("totalResults") or 0)
            seite = data.get("tradeMarks") or []
            treffer.extend(seite)
            if not seite or len(treffer) >= gesamt or len(treffer) >= max_treffer \
                    or page >= int(data.get("totalPages") or 1):
                break
            page += 1
        return treffer[:max_treffer], gesamt, gesamt > len(treffer[:max_treffer])

    # -- Detail -----------------------------------------------------------------

    def detail(self, st13: str, uebersetzen: bool = False) -> dict:
        self._session()
        self._warte()
        r = self._http.get(f"{BASE}/api/trademark/detail/{st13.strip()}",
                           params={"translate": "true" if uebersetzen else "false"},
                           headers=self._xsrf())
        self._pruefe_block(r)
        if r.status_code == 403:
            # Session verloren → einmal neu aufbauen
            self._session_ok = False
            self._session()
            r = self._http.get(f"{BASE}/api/trademark/detail/{st13.strip()}",
                               params={"translate": "false"}, headers=self._xsrf())
        if r.status_code >= 400:
            raise TMviewFehler(f"TMview-Detail {st13} → {r.status_code}: {r.text[:200]}")
        return r.json()


# -- Mapping auf das gemeinsame Kurzformat ---------------------------------------

def _datum(s: str | None) -> str | None:
    return s[:10] if s else None


def kurz(tm: dict) -> dict:
    """TMview-Trefferzeile → gemeinsames Kurzformat (wie _kurz() für EUIPO)."""
    art = tm.get("tradeMarkType")
    art_map = {"Word": "WORD", "Figurative": "FIGURATIVE", "Combined": "FIGURATIVE",
               "3-D": "SHAPE_3D", "Sound": "SOUND", "Colour": "COLOUR"}
    inhaber = tm.get("applicantName")
    if isinstance(inhaber, list):
        inhaber = ", ".join(x for x in inhaber if x) or None
    return {
        "nummer": tm.get("applicationNumber"),
        "st13": tm.get("ST13"),
        "amt": tm.get("tmOffice"),
        "zeichen": tm.get("tmName"),
        "art": art_map.get(art, (art or "").upper() or None),
        "inhaber": inhaber,
        "klassen": [int(k) for k in (tm.get("niceClass") or []) if str(k).isdigit()],
        "status": tm.get("tradeMarkStatus"),
        "anmeldetag": _datum(tm.get("applicationDate")),
        "registriert": _datum(tm.get("registrationDate")),
        "ablauf": _datum(tm.get("expiryDate")),
        "quelle": "TMview",
    }


def detail_kurz(d: dict) -> dict:
    tm = d.get("tradeMark") or {}
    gs = []
    for liste in d.get("goodsAndServicesList") or []:
        gas = liste.get("goodAndServices") or {}
        for k in gas.get("goodAndServiceList") or []:
            begriffe = [t.get("term") for t in (k.get("goodsAndServices") or []) if t.get("term")]
            gs.append({"klasse": int(k["niceClass"]) if str(k.get("niceClass", "")).isdigit() else k.get("niceClass"),
                       "sprache": gas.get("language"), "begriffe": begriffe})
    if not gs:
        for k in tm.get("goodAndServices") or []:
            gs.append({"klasse": int(k["niceClass"]) if str(k.get("niceClass", "")).isdigit() else k.get("niceClass"),
                       "sprache": None, "begriffe": [k.get("goodsAndServices")]})
    applicants = d.get("applicants") or []
    return {
        "st13": d.get("ST13"),
        "nummer": tm.get("applicationNumber"),
        "registernummer": tm.get("registrationNumber"),
        "amt": tm.get("registrationOfficeCode") or tm.get("tmOffice"),
        "zeichen": tm.get("tmName"),
        "art": tm.get("markFeature"),
        "markenart": tm.get("kindMark"),
        "inhaber": ", ".join(a.get("fullName", "") for a in applicants if a.get("fullName")) or None,
        "vertreter": ", ".join(r.get("fullName", "") for r in (d.get("representatives") or []) if r.get("fullName")) or None,
        "klassen": [int(x) for x in str(tm.get("niceClass") or "").replace(" ", "").split(",") if x.isdigit()],
        "status": tm.get("markCurrentStatusCode"),
        "anmeldetag": _datum(tm.get("applicationDate")),
        "registriert": _datum(tm.get("codeRegistrationDate")),
        "ablauf": _datum(tm.get("expiryDate")),
        "benannte_laender": tm.get("designatedCountries") or [],
        "waren_dienstleistungen": gs,
        "widersprueche": len(d.get("oppositions") or []),
        "loeschungen": len(d.get("cancellations") or []),
        "amtsseite": d.get("officeUrl"),
        "stand_amt": _datum(d.get("officeLastUpdateDate")),
        "quelle": "TMview",
    }
