#!/usr/bin/env python3
"""MCP-Server „marken“ — Markenrecherche AT/EU (EUIPO-API + TMview).

Grundsatz: Datenbeschaffung deterministisch hier, Bewertung im Modell.
Kein Tool erfindet Treffer; jeder Treffer trägt Quelle und Abrufzeitpunkt.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).parent))
from euipo_client import STATUS_LEBEND, STATUS_TOT, EuipoClient, EuipoFehler  # noqa: E402
from tmview_client import TMviewBlockiert, TMviewClient, TMviewFehler, status_tot  # noqa: E402
from tmview_client import detail_kurz as tmview_detail_kurz, kurz as tmview_kurz  # noqa: E402
from varianten import bewerte_treffer, bilde_varianten, koelner_phonetik, normalisiere  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
log = logging.getLogger("marken")

mcp = FastMCP("Markenrecherche")

_client: EuipoClient | None = None


def client() -> EuipoClient:
    global _client
    if _client is None:
        _client = EuipoClient()
    return _client


def _jetzt() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _quelle() -> str:
    return f"EUIPO Trademark Search API ({client().env})"


def _kurz(tm: dict) -> dict:
    """Kompakte, modellfreundliche Darstellung eines Registereintrags."""
    wm = tm.get("wordMarkSpecification") or {}
    applicants = tm.get("applicants") or []
    inhaber = ", ".join(a.get("name", "") for a in applicants if isinstance(a, dict) and a.get("name")) or None
    basis = tm.get("markBasis")
    amt = "WIPO/EU-Benennung" if basis == "INTERNATIONAL_TRADEMARK" else "EUIPO"
    return {
        "nummer": tm.get("applicationNumber"),
        "amt": amt,
        "zeichen": wm.get("verbalElement"),
        "art": tm.get("markFeature"),
        "inhaber": inhaber,
        "klassen": tm.get("niceClasses") or [],
        "status": tm.get("status"),
        "anmeldetag": tm.get("applicationDate") or tm.get("designationDate"),
        "registriert": tm.get("registrationDate"),
        "ablauf": tm.get("expiryDate"),
    }


def _status_hinweis(tm: dict) -> str | None:
    """Abgelaufene Marken: sechsmonatige Nachfrist für die Verlängerung (Art 53 Abs 3 UMV)."""
    if tm.get("status") != "EXPIRED":
        return None
    ablauf = tm.get("expiryDate")
    if not ablauf:
        return "abgelaufen — Nachfrist nicht prüfbar"
    try:
        d = date.fromisoformat(ablauf[:10])
    except ValueError:
        return "abgelaufen"
    if d + timedelta(days=183) >= date.today():
        return "abgelaufen, Verlängerungsnachfrist läuft möglicherweise noch"
    return "abgelaufen, Nachfrist verstrichen"


# ---------------------------------------------------------------------------
# Status / Diagnose
# ---------------------------------------------------------------------------

@mcp.tool()
def marken_status() -> dict:
    """[status] Prüft Konfiguration und Erreichbarkeit der EUIPO-API (Umgebung, Credentials, Token).
    Immer zuerst aufrufen, wenn ein Recherchelauf fehlschlägt."""
    try:
        c = client()
    except EuipoFehler as e:
        return {"ok": False, "fehler": str(e)}
    info: dict[str, Any] = {
        "umgebung": c.env,
        "api_base": c.api_base,
        "credentials_vorhanden": c.konfiguriert,
        "hinweis": None,
    }
    if c.env == "sandbox":
        info["hinweis"] = ("Sandbox: eingefrorener Datenbestand plus Testdaten — Ergebnisse sind "
                           "NICHT registeraktuell und dürfen nicht in einen Aktenvermerk.")
    if not c.konfiguriert:
        info["ok"] = False
        info["fehler"] = "EUIPO_CLIENT_ID / EUIPO_CLIENT_SECRET fehlen in .env"
        return info
    try:
        c._hole_token()
        probe = c.suche(EuipoClient.rsql(verbal="TEST*", status=["REGISTERED"]), size=10)
        info["ok"] = True
        info["probe_treffer_gesamt"] = probe.get("totalElements")
    except EuipoFehler as e:
        info["ok"] = False
        info["fehler"] = str(e)
        if "403" in str(e) and c.env == "production":
            info["hinweis"] = ("Token wird ausgestellt, API antwortet 403: die App ist im Produktivportal registriert, "
                               "aber der Produktivzugang (Identitätsprüfung durch die EUIPO) ist noch nicht freigeschaltet. "
                               "Für Tests eine eigene App im Sandbox-Portal (dev-sandbox.euipo.europa.eu) anlegen und "
                               "EUIPO_SANDBOX_CLIENT_ID/_SECRET setzen.")
    except Exception as e:  # Netzwerk etc.
        info["ok"] = False
        info["fehler"] = f"{type(e).__name__}: {e}"
    # TMview-Erreichbarkeit (eine einzige, leichte Abfrage)
    try:
        d = tmview().suche("TEST", ["AT"], modus="exakt", page_size=10)
        info["tmview"] = {"ok": True, "probe_treffer": d.get("totalResults")}
    except Exception as e:
        info["tmview"] = {"ok": False, "fehler": f"{type(e).__name__}: {str(e)[:160]}"}
    return info


# ---------------------------------------------------------------------------
# Varianten
# ---------------------------------------------------------------------------

@mcp.tool()
def varianten(zeichen: str, umfang: str = "normal") -> dict:
    """[search variants] Bildet deterministisch die Suchvarianten für ein Wortzeichen (Stamm, Wildcards,
    Schreibweisen, Konsonantenskelett, bei umfang='breit' auch Vokaltausch).
    Zeigt, was recherche_lauf abfragen wird — für das Suchprotokoll und um Varianten
    zu ergänzen. umfang: knapp | normal | breit."""
    satz = bilde_varianten(zeichen, umfang=umfang)
    return {
        "zeichen": zeichen,
        "normalisiert": satz.normalisiert,
        "praegende_bestandteile": satz.staemme,
        "koelner_phonetik": {s: koelner_phonetik(s) for s in satz.staemme},
        "varianten": [
            {"suchstring": v.suchstring, "art": v.art, "grund": v.begruendung, "prio": v.prioritaet}
            for v in satz.varianten
        ],
        "anzahl": len(satz.suchstrings()),
    }


# ---------------------------------------------------------------------------
# Einzelabfragen
# ---------------------------------------------------------------------------

@mcp.tool()
def eutm_search(
    zeichen: str,
    klassen: list[int] | None = None,
    modus: str = "enthaelt",
    nur_lebend: bool = True,
    nur_wortmarken: bool = False,
    max_treffer: int = 100,
) -> dict:
    """[EU trade mark search] Einzelne Abfrage der EUIPO-Datenbank (Unionsmarken und internationale Registrierungen
    mit EU-Benennung). modus: exakt | beginnt | enthaelt | roh (zeichen wird als RSQL-Wert
    mit eigenen * verwendet). Liefert Trefferliste mit Registernummer, Inhaber, Klassen, Status.
    Für die vollständige Recherche recherche_lauf verwenden."""
    z = normalisiere(zeichen)
    if modus == "exakt":
        verbal = z
    elif modus == "beginnt":
        verbal = f"{z}*"
    elif modus == "roh":
        verbal = zeichen.strip().upper()
    else:
        verbal = f"*{z}*"
    status = STATUS_LEBEND if nur_lebend else None
    feature = ["WORD"] if nur_wortmarken else None
    query = EuipoClient.rsql(verbal=verbal, klassen=klassen, status=status, mark_feature=feature)
    try:
        rohe, gesamt, abgeschnitten = client().suche_alle(query, max_treffer=max_treffer)
    except EuipoFehler as e:
        return {"fehler": str(e), "query": query, "quelle": _quelle(), "abruf": _jetzt()}
    return {
        "query": query,
        "gesamt": gesamt,
        "abgeschnitten": abgeschnitten,
        "treffer": [_kurz(t) for t in rohe],
        "quelle": _quelle(),
        "abruf": _jetzt(),
    }


@mcp.tool()
def eutm_get(nummer: str) -> dict:
    """[EU trade mark record] Vollständiger Registerauszug einer Unionsmarke / IR mit EU-Benennung nach Anmeldenummer
    (z. B. 018945321 oder W01234567). Enthält das Waren-/Dienstleistungsverzeichnis je Klasse."""
    try:
        tm = client().marke(nummer)
    except EuipoFehler as e:
        return {"fehler": str(e), "nummer": nummer}
    gs = []
    for k in tm.get("goodsAndServices") or []:
        begriffe: list[str] = []
        for d in k.get("description") or []:
            if d.get("language") in ("de", "en"):
                begriffe.extend(d.get("terms") or [])
        gs.append({"klasse": k.get("classNumber"), "begriffe": begriffe[:60]})
    out = _kurz(tm)
    if not out["klassen"] and gs:
        out["klassen"] = [g["klasse"] for g in gs if g.get("klasse")]
    out.update({
        "waren_dienstleistungen": gs,
        "anmeldesprache": tm.get("applicationLanguage"),
        "widerspruchsfrist_beginn": tm.get("oppositionPeriodStartDate"),
        "status_hinweis": _status_hinweis(tm),
        "quelle": _quelle(),
        "abruf": _jetzt(),
    })
    return out


# ---------------------------------------------------------------------------
# TMview (nationale Register, WIPO)
# ---------------------------------------------------------------------------

_tmv: TMviewClient | None = None


def tmview() -> TMviewClient:
    global _tmv
    if _tmv is None:
        _tmv = TMviewClient()
    return _tmv


# Register-Kürzel für recherche_lauf → TMview-Ämter. „EU" läuft über die EUIPO-API,
# „EM" ist die EU über TMview (nur als Ersatz, wenn die EUIPO-API nicht erreichbar ist).
REGISTER_AEMTER = {"AT": "AT", "DE": "DE", "CH": "CH", "WO": "WO", "EM": "EM",
                   "IT": "IT", "FR": "FR", "ES": "ES", "GB": "GB", "BX": "BX", "US": "US"}


@mcp.tool()
def tmview_search(
    zeichen: str,
    aemter: list[str] | None = None,
    modus: str = "enthaelt",
    klassen: list[int] | None = None,
    nur_lebend: bool = True,
    max_treffer: int = 100,
) -> dict:
    """[TMview search — national registers, WIPO] Einzelabfrage in TMview (tmdn.org) — nationale Register und WIPO. aemter: Amtskürzel,
    Standard ["AT"]; z. B. ["AT","WO"] für österreichische Marken und internationale
    Registrierungen, ["DE"], ["CH"]. modus: enthaelt (Wildcards * erlaubt) | exakt (ganzes
    Wort) | unscharf (TMview-Fuzzy) | woerter. Liefert Kurzdaten je Treffer mit ST13-Kennung
    für tmview_get. Für die vollständige Recherche recherche_lauf verwenden."""
    aemter = [a.upper() for a in (aemter or ["AT"])]
    try:
        rohe, gesamt, abgeschnitten = tmview().suche_alle(zeichen, aemter, modus=modus,
                                                          klassen=klassen, max_treffer=max_treffer)
    except (TMviewFehler, Exception) as e:
        return {"fehler": f"{type(e).__name__}: {e}", "zeichen": zeichen, "aemter": aemter,
                "quelle": "TMview (Webabfrage)", "abruf": _jetzt()}
    treffer = [tmview_kurz(t) for t in rohe]
    if nur_lebend:
        treffer = [t for t in treffer if not status_tot(t["status"])]
    return {"zeichen": zeichen, "aemter": aemter, "modus": modus, "gesamt": gesamt,
            "abgeschnitten": abgeschnitten, "treffer": treffer,
            "quelle": "TMview (Webabfrage)", "abruf": _jetzt()}


@mcp.tool()
def tmview_get(st13: str) -> dict:
    """[TMview record] Vollauszug einer Marke aus TMview nach ST13-Kennung (z. B. AT501972000001736),
    inkl. Waren-/Dienstleistungsverzeichnis, Inhaber, Vertreter, Widersprüche und Link zur
    Amtsseite (See-IP bei AT-Marken)."""
    try:
        d = tmview().detail(st13)
    except (TMviewFehler, Exception) as e:
        return {"fehler": f"{type(e).__name__}: {e}", "st13": st13}
    out = tmview_detail_kurz(d)
    out["abruf"] = _jetzt()
    return out


# ---------------------------------------------------------------------------
# Konsolidierter Recherchelauf
# ---------------------------------------------------------------------------

def _leer_bewertung() -> dict:
    return {"score": 0.0, "stufe": "niedrig", "schriftbild": 0.0, "klang": 0.0, "enthalten": False}


@mcp.tool()
def recherche_lauf(
    zeichen: str,
    klassen: list[int] | None = None,
    register: list[str] | None = None,
    zusatz_varianten: list[str] | None = None,
    umfang: str = "normal",
    nur_lebend: bool = True,
    max_varianten: int = 25,
    max_pro_variante: int = 200,
    min_aehnlichkeit: float = 0.5,
    max_gelistet: int = 60,
) -> dict:
    """[trade mark clearance search — main tool] Vollständiger Recherchelauf in einem Aufruf über mehrere Register: bildet die
    Suchvarianten, fragt jede gegen die Register ab, dedupliziert, bewertet die
    Zeichenähnlichkeit (Schriftbild, Klang, Enthaltensein) und liefert EINE sortierte
    Trefferliste plus Suchprotokoll und Recherchelücken.
    register: Standard ["EU","AT"] — EU = Unionsmarken + IR mit EU-Benennung über die
    EUIPO-API; AT/DE/CH/WO/… = nationale Register bzw. WIPO über TMview. klassen:
    angemeldete UND angrenzende Klassen; ohne Klassen klassenübergreifend.
    zusatz_varianten: eigene Suchstrings (mit * als Wildcard), z. B. Übersetzungen.
    Treffer unter min_aehnlichkeit werden nur gezählt; gelistet werden höchstens
    max_gelistet (die ähnlichsten zuerst)."""
    register = [r.upper() for r in (register or ["EU", "AT"])]
    satz = bilde_varianten(zeichen, umfang=umfang)
    suchstrings = satz.suchstrings(max_anzahl=max_varianten)
    for extra in zusatz_varianten or []:
        e = extra.strip().upper()
        if e and e not in suchstrings:
            suchstrings.append(e)

    status = STATUS_LEBEND if nur_lebend else None
    protokoll: list[dict] = []
    luecken: list[str] = []
    gefunden: dict[str, dict] = {}          # schlüssel → kurzformat
    gefunden_durch: dict[str, list[str]] = {}
    quellen: list[str] = []

    def _merke(schluessel: str, k: dict, s: str) -> None:
        gefunden.setdefault(schluessel, k)
        gefunden_durch.setdefault(schluessel, []).append(s)

    # -- EU über EUIPO-API -------------------------------------------------------
    eu_ok = False
    if "EU" in register:
        c = client()
        quellen.append(_quelle())
        if not c.konfiguriert:
            luecken.append("EUIPO-Credentials fehlen — EU-Register nicht abgefragt")
        else:
            fehler_eu = 0
            for s in suchstrings:
                query = EuipoClient.rsql(verbal=s, klassen=klassen, status=status)
                try:
                    rohe, gesamt, abgeschnitten = c.suche_alle(query, max_treffer=max_pro_variante)
                except Exception as e:
                    fehler_eu += 1
                    protokoll.append({"register": "EU", "suchstring": s, "trefferzahl": None, "fehler": str(e)[:160]})
                    if fehler_eu == 1:
                        luecken.append(f"EU: Variante „{s}“ nicht abgefragt: {str(e)[:120]}")
                    if fehler_eu >= 3:
                        luecken.append("EU: EUIPO-API antwortet wiederholt mit Fehler — restliche Varianten übersprungen")
                        break
                    continue
                eu_ok = True
                protokoll.append({"register": "EU", "suchstring": s, "trefferzahl": gesamt, "abgeschnitten": abgeschnitten})
                if abgeschnitten:
                    luecken.append(f"EU: Variante „{s}“: {gesamt} Treffer, nur {max_pro_variante} ausgewertet")
                for tm in rohe:
                    nr = tm.get("applicationNumber")
                    if not nr:
                        continue
                    k = _kurz(tm)
                    k["quelle"] = "EUIPO-API"
                    k["status_hinweis"] = _status_hinweis(tm)
                    _merke(f"EU:{nr}", k, s)
            if c.env == "sandbox":
                luecken.insert(0, "EU-Treffer aus der SANDBOX: nicht registeraktuell, nicht für den Aktenvermerk verwendbar")

    # -- Nationale Register / WIPO über TMview -----------------------------------
    aemter = [REGISTER_AEMTER[r] for r in register if r in REGISTER_AEMTER]
    if "EU" in register and not eu_ok and "EM" not in aemter:
        aemter.append("EM")
        luecken.append("EU-Register ersatzweise über TMview (EM) abgefragt, weil die EUIPO-API nicht verfügbar war")
    if aemter:
        quellen.append(f"TMview (Webabfrage, Ämter {', '.join(aemter)})")
        t = tmview()
        # TMview sparsam abfragen (Bot-Schutz!): exaktes Zeichen, Stämme als „enthält",
        # Konsonantenskelett, dazu TMviews eigene unscharfe Suche über Zeichen und Stämme.
        # Die Schreibvarianten laufen NICHT einzeln — die unscharfe Suche deckt sie weitgehend ab.
        tmv_abfragen: list[tuple[str, str]] = [(satz.normalisiert, "exakt")]
        for v in satz.varianten:
            if v.art in ("stamm", "getrennt", "skelett", "serie", "wildcard") and v.prioritaet <= 2:
                tmv_abfragen.append((v.suchstring, "enthaelt"))
        tmv_abfragen.append((satz.normalisiert, "unscharf"))
        for st in satz.staemme:
            if st != satz.normalisiert and len(st) >= 4:
                tmv_abfragen.append((st, "unscharf"))
        for extra in zusatz_varianten or []:
            tmv_abfragen.append((extra.strip().upper(), "enthaelt"))
        gesehen_tmv: set[tuple[str, str]] = set()
        tmv_abfragen = [a for a in tmv_abfragen if not (a in gesehen_tmv or gesehen_tmv.add(a))][:12]
        fehler_tmv = 0
        for s, modus in tmv_abfragen:
            try:
                rohe, gesamt, abgeschnitten = t.suche_alle(s, aemter, modus=modus, klassen=klassen,
                                                           max_treffer=max_pro_variante)
            except TMviewBlockiert as e:
                protokoll.append({"register": "TMview", "suchstring": s, "modus": modus, "trefferzahl": None,
                                  "fehler": str(e)[:160]})
                luecken.append("TMview GESPERRT (Bot-Schutz von tmdn.org): nationale Register (" + ", ".join(aemter) +
                               ") in diesem Lauf NICHT recherchiert — Lauf später mit register=" +
                               str([a for a in register if a != "EU"]) + " wiederholen oder See-IP manuell prüfen")
                break
            except Exception as e:
                fehler_tmv += 1
                protokoll.append({"register": "TMview", "suchstring": s, "modus": modus, "trefferzahl": None,
                                  "fehler": f"{type(e).__name__}: {str(e)[:120]}"})
                if fehler_tmv >= 2:
                    luecken.append("TMview antwortet wiederholt mit Fehler — nationale Register (" + ", ".join(aemter) +
                                   ") NICHT vollständig recherchiert; See-IP manuell nachziehen")
                    break
                continue
            protokoll.append({"register": "TMview", "suchstring": s, "modus": modus, "trefferzahl": gesamt,
                              "abgeschnitten": abgeschnitten})
            if abgeschnitten:
                luecken.append(f"TMview: „{s}“ ({modus}): {gesamt} Treffer, nur {max_pro_variante} ausgewertet")
            for tm in rohe:
                k = tmview_kurz(tm)
                if nur_lebend and status_tot(k["status"]):
                    continue
                # IR mit EU-Benennung sind schon im EU-Teil (Nummer W0…): nicht doppelt listen
                if k.get("amt") == "WO" and f"EU:W0{k.get('nummer')}" in gefunden:
                    gefunden[f"EU:W0{k.get('nummer')}"]["auch_in_tmview"] = True
                    continue
                schluessel = k.get("st13") or f"{k['amt']}:{k['nummer']}"
                _merke(schluessel, k, f"{s} [{modus}]")
        if fehler_tmv and fehler_tmv < 2:
            luecken.append(f"TMview: {fehler_tmv} Abfrage(n) fehlgeschlagen (siehe Suchprotokoll)")
    fehlende = [r for r in register if r != "EU" and r not in REGISTER_AEMTER]
    if fehlende:
        luecken.append(f"Unbekannte Registerkürzel ignoriert: {', '.join(fehlende)}")

    # -- Bewertung und Sortierung ---------------------------------------------------
    treffer: list[dict] = []
    unter_schwelle = 0
    for schluessel, k in gefunden.items():
        text = k.get("zeichen") or ""
        bew = bewerte_treffer(zeichen, text) if text else _leer_bewertung()
        ueberschneidung = sorted(set(k.get("klassen") or []) & set(klassen)) if klassen else None
        if bew["score"] < min_aehnlichkeit and not (ueberschneidung and bew["score"] >= 0.4):
            unter_schwelle += 1
            continue
        k["zeichenaehnlichkeit"] = bew
        k["klassenueberschneidung"] = ueberschneidung
        k.setdefault("status_hinweis", None)
        k["gefunden_durch"] = gefunden_durch[schluessel][:4]
        treffer.append(k)

    treffer.sort(key=lambda t: (-(t["zeichenaehnlichkeit"]["score"]),
                                -len(t["klassenueberschneidung"] or []), t.get("anmeldetag") or ""))
    nicht_gelistet = max(0, len(treffer) - max_gelistet)
    treffer = treffer[:max_gelistet]

    if nicht_gelistet:
        luecken.append(f"{nicht_gelistet} weitere Treffer mit Ähnlichkeit ≥ {min_aehnlichkeit} nicht gelistet "
                       f"(max_gelistet={max_gelistet}) — bei Bedarf Klassen einschränken oder max_gelistet erhöhen")
    if "AT" not in register:
        luecken.append("Nationale österreichische Marken NICHT recherchiert (register um \"AT\" ergänzen)")
    if "WO" not in register:
        luecken.append("Internationale Registrierungen mit Schutzerstreckung auf AT NICHT gesondert recherchiert "
                       "(register um \"WO\" ergänzen); IR mit EU-Benennung sind im EU-Teil enthalten")
    luecken.append("TMview-Treffer sind Kurzdaten der Webabfrage — vor der Bewertung Vollauszug über tmview_get "
                   "(AT-Marken) bzw. eutm_get (EU-Marken) ziehen")
    luecken.append("Reine Bildähnlichkeit ohne Wortbestandteil nicht recherchiert")

    return {
        "zeichen": zeichen,
        "normalisiert": satz.normalisiert,
        "praegende_bestandteile": satz.staemme,
        "klassen": klassen,
        "register": register,
        "nur_lebend": nur_lebend,
        "anzahl_suchstrings": len(suchstrings),
        "anzahl_treffer_gesamt": len(gefunden),
        "anzahl_gelistet": len(treffer),
        "anzahl_unter_schwelle": unter_schwelle,
        "anzahl_nicht_gelistet": nicht_gelistet,
        "treffer": treffer,
        "suchprotokoll": protokoll,
        "recherchelücken": luecken,
        "quellen": quellen,
        "stichtag": _jetzt(),
    }


# ---------------------------------------------------------------------------
# Nizza / Goods and Services (EUIPO G&S-API 1.2.0)
# ---------------------------------------------------------------------------

def _term_kurz(t: dict) -> dict:
    return {"klasse": t.get("classNumber"), "begriff": t.get("text"), "concept_id": t.get("conceptId")}


@mcp.tool()
def nizza_suggest(freitext: str, sprache: str = "de", klasse: int | None = None, max_treffer: int = 15) -> dict:
    """[Nice classification suggestions] Klassifizierungsvorschlag: liefert zu einem Freitext (Ware/Dienstleistung im Klartext,
    z. B. 'Wartung von Software' oder 'Physiotherapie') die passenden harmonisierten Begriffe
    (TMclass/HDB) mit Nizza-Klasse. Zwei Wege in einem Aufruf: Vorschlagsliste der EUIPO und
    Volltextsuche in den Begriffen. Optional auf eine Klasse einschränken."""
    c = client()
    out: dict[str, Any] = {"freitext": freitext, "sprache": sprache, "vorschlaege": [], "volltext": [],
                           "quelle": "EUIPO Goods and Services API (harmonisierte Datenbank)", "abruf": _jetzt()}
    fehler = []
    try:
        sug = c.gs_suggest([freitext], sprache=sprache, klasse=klasse, max_suggestions=max_treffer)
        for s_ in sug.get("suggestions", []):
            out["vorschlaege"].extend(_term_kurz(t) for t in s_.get("suggestedTerms", []))
    except EuipoFehler as e:
        fehler.append(f"Vorschlagsliste: {e}")
    try:
        res = c.gs_terms(freitext, sprache=sprache, klassen=[klasse] if klasse else None, size=max_treffer)
        out["volltext"] = [_term_kurz(t) for t in res.get("terms", [])]
        out["volltext_gesamt"] = res.get("totalElements")
    except EuipoFehler as e:
        fehler.append(f"Volltextsuche: {e}")
    if fehler:
        out["fehler"] = fehler
    # Klassen, die in den Ergebnissen vorkommen — als Hinweis für den Ähnlichkeitsbereich
    out["klassen_im_ergebnis"] = sorted({t["klasse"] for t in out["vorschlaege"] + out["volltext"] if t.get("klasse")})
    return out


@mcp.tool()
def nizza_validate(begriffe: list[str], sprache: str = "de") -> dict:
    """[Nice classification validation] Prüft ein Waren-/Dienstleistungsverzeichnis gegen die harmonisierte Datenbank der EUIPO.
    begriffe im Format 'Klasse: Begriff' (z. B. '42: Wartung von Computersoftware'); mehrere
    Begriffe derselben Klasse einzeln angeben. Rückgabe je Begriff: harmonisiert ja/nein und
    formale Fehler. Nicht harmonisierte Begriffe sind im Bericht als Beanstandungsrisiko zu
    kennzeichnen (Klasse muss angegeben sein — ohne Klasse kein Eintrag)."""
    gruppen: dict[int, list[str]] = {}
    ungueltig: list[str] = []
    for b in begriffe:
        if ":" in b and b.split(":", 1)[0].strip().isdigit():
            kl, txt = b.split(":", 1)
            k = int(kl)
            if 1 <= k <= 45 and txt.strip():
                gruppen.setdefault(k, []).append(txt.strip())
                continue
        ungueltig.append(b)
    if not gruppen:
        return {"fehler": "Kein gültiger Eintrag — Format 'Klasse: Begriff' verwenden", "ungueltig": ungueltig}
    eintraege = [{"classNumber": k, "terms": v} for k, v in sorted(gruppen.items())]
    try:
        data = client().gs_validate(eintraege, sprache=sprache)
    except EuipoFehler as e:
        return {"fehler": str(e), "eingabe": eintraege}
    ergebnis = []
    nicht_harmonisiert = []
    for k in data.get("goodsAndServices", []):
        for t in k.get("terms", []):
            row = {"klasse": k.get("classNumber"), "begriff": t.get("text"),
                   "harmonisiert": bool(t.get("harmonized")), "concept_id": t.get("conceptId"),
                   "fehler": [e.get("detail") or e.get("title") for e in (t.get("errors") or [])]}
            ergebnis.append(row)
            if not row["harmonisiert"] or row["fehler"]:
                nicht_harmonisiert.append(f"{row['klasse']}: {row['begriff']}")
    return {"sprache": sprache, "ergebnis": ergebnis, "beanstandungsrisiko": nicht_harmonisiert,
            "ungueltige_eingaben": ungueltig, "quelle": "EUIPO Goods and Services API (harmonisierte Datenbank)",
            "abruf": _jetzt()}


@mcp.tool()
def nizza_klassenueberschriften(sprache: str = "de", klassen: list[int] | None = None) -> dict:
    """[Nice class headings] Amtliche Klassenüberschriften der Nizza-Klassifikation (alle 45 oder Auswahl) — zur
    Orientierung über den Ähnlichkeitsbereich, nicht als Verzeichnis (Klassenüberschriften
    decken seit IP Translator nicht die ganze Klasse ab)."""
    try:
        data = client().gs_class_headings(sprache=sprache)
    except EuipoFehler as e:
        return {"fehler": str(e)}
    rows = [{"klasse": h.get("classNumber"), "ueberschrift": h.get("heading")} for h in data.get("headings", [])]
    if klassen:
        rows = [r for r in rows if r["klasse"] in klassen]
    return {"sprache": sprache, "klassen": rows, "quelle": "EUIPO Goods and Services API", "abruf": _jetzt()}


def main() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=transport, host=os.getenv("MCP_HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8765")))


if __name__ == "__main__":
    main()
