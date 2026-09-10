"""Schlanker Client für die EUIPO-APIs (Trademark Search, Goods and Services).

Auth: OAuth 2.0 client_credentials, scope "uid". Jede Anfrage trägt
Authorization: Bearer <token> UND X-IBM-Client-Id.

Umgebung über EUIPO_ENV=production|sandbox. Die Sandbox ist ein eingefrorener
Datenbestand plus Testdaten — gut für Struktur- und Ablauftests, nicht für
echte Recherchen. Produktivzugang erfordert die Identitätsprüfung durch die EUIPO.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

_HOSTS = {
    "production": {
        "api": "https://api.euipo.europa.eu",
        "token": "https://euipo.europa.eu/cas-server-webapp/oidc/accessToken",
    },
    "sandbox": {
        "api": "https://api-sandbox.euipo.europa.eu",
        "token": "https://auth-sandbox.euipo.europa.eu/oidc/accessToken",
    },
}

TM_PATH = "/trademark-search"
GS_PATH = os.getenv("EUIPO_GS_PATH", "/goods-and-services")

# Lebende Marken — alles, was ein Hindernis sein kann oder werden kann.
# (APPEALABLE und ACCEPTANCE_PENDING stehen zwar im Schema, werden vom Query-Filter aber
#  abgelehnt — Stand Sandbox 9/2026 — und fehlen deshalb hier.)
STATUS_LEBEND = [
    "RECEIVED", "UNDER_EXAMINATION", "APPLICATION_PUBLISHED", "REGISTRATION_PENDING",
    "REGISTERED", "OPPOSITION_PENDING", "APPEALED", "CANCELLATION_PENDING",
    "START_OF_OPPOSITION_PERIOD", "ACCEPTED",
]
STATUS_TOT = ["WITHDRAWN", "REFUSED", "CANCELLED", "SURRENDERED", "EXPIRED", "REMOVED_FROM_REGISTER"]


class EuipoFehler(Exception):
    pass


@dataclass
class _Token:
    wert: str
    ablauf: float


class EuipoClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        env: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        # Fallback ist bewusst production: eine fehlende Angabe darf nicht still
        # Sandbox-Daten liefern, die wie ein Registerauszug aussehen.
        self.env = (env or os.getenv("EUIPO_ENV") or "production").strip().lower()
        if self.env not in _HOSTS:
            raise EuipoFehler(f"EUIPO_ENV muss production oder sandbox sein, nicht {self.env!r}")
        # Sandbox- und Produktivportal sind getrennte Registrierungen mit eigenen Credentials.
        # Für die Sandbox werden EUIPO_SANDBOX_CLIENT_ID/_SECRET bevorzugt, sonst die normalen.
        if self.env == "sandbox":
            self.client_id = client_id or os.getenv("EUIPO_SANDBOX_CLIENT_ID") or os.getenv("EUIPO_CLIENT_ID", "")
            self.client_secret = client_secret or os.getenv("EUIPO_SANDBOX_CLIENT_SECRET") or os.getenv("EUIPO_CLIENT_SECRET", "")
        else:
            self.client_id = client_id or os.getenv("EUIPO_CLIENT_ID", "")
            self.client_secret = client_secret or os.getenv("EUIPO_CLIENT_SECRET", "")
        self.api_base = os.getenv("EUIPO_API_BASE") or _HOSTS[self.env]["api"]
        self.token_url = os.getenv("EUIPO_TOKEN_URL") or _HOSTS[self.env]["token"]
        self._token: _Token | None = None
        self._http = httpx.Client(timeout=timeout, headers={"Accept": "application/json",
                                                              "User-Agent": "mcp-marken/0.1"})

    # -- Auth -----------------------------------------------------------------

    @property
    def konfiguriert(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _hole_token(self) -> str:
        if self._token and self._token.ablauf - 60 > time.time():
            return self._token.wert
        if not self.konfiguriert:
            raise EuipoFehler("EUIPO_CLIENT_ID / EUIPO_CLIENT_SECRET fehlen (.env prüfen)")
        r = self._http.post(
            self.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": "uid",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise EuipoFehler(f"Token-Anfrage fehlgeschlagen ({r.status_code}): {r.text[:300]}")
        data = r.json()
        self._token = _Token(data["access_token"], time.time() + float(data.get("expires_in", 3600)))
        return self._token.wert

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._hole_token()}", "X-IBM-Client-Id": self.client_id}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.api_base + path
        r = self._http.get(url, params=params, headers=self._headers())
        if r.status_code == 401:
            # Token evtl. serverseitig verworfen: einmal erneuern
            self._token = None
            r = self._http.get(url, params=params, headers=self._headers())
        if r.status_code >= 400:
            raise EuipoFehler(f"GET {path} → {r.status_code}: {r.text[:400]}")
        return r.json()

    def _post(self, path: str, body: Any) -> Any:
        url = self.api_base + path
        r = self._http.post(url, json=body, headers={**self._headers(), "Content-Type": "application/json"})
        if r.status_code >= 400:
            raise EuipoFehler(f"POST {path} → {r.status_code}: {r.text[:400]}")
        return r.json()

    # -- Trademark Search -----------------------------------------------------

    @staticmethod
    def rsql(
        verbal: str | None = None,
        klassen: list[int] | None = None,
        status: list[str] | None = None,
        mark_feature: list[str] | None = None,
        extra: str | None = None,
    ) -> str:
        teile: list[str] = []
        if verbal:
            v = verbal.replace('"', "")
            # Werte mit Leerzeichen oder Sonderzeichen müssen in Anführungszeichen
            if " " in v or any(c in v for c in "();,="):
                v = f'"{v}"'
            teile.append(f"wordMarkSpecification.verbalElement=={v}")
        if klassen:
            teile.append("niceClasses=in=(" + ",".join(str(k) for k in klassen) + ")")
        if status:
            teile.append("status=in=(" + ",".join(status) + ")")
        if mark_feature:
            teile.append("markFeature=in=(" + ",".join(mark_feature) + ")")
        if extra:
            teile.append(f"({extra})")
        return " and ".join(teile)

    def suche(self, query: str, page: int = 0, size: int = 100, sort: str | None = None,
              fields: str | None = None) -> dict:
        size = max(10, min(100, size))
        params: dict[str, Any] = {"query": query, "page": page, "size": size}
        if sort:
            params["sort"] = sort
        if fields:
            params["fields"] = fields
        return self._get(f"{TM_PATH}/trademarks", params)

    def suche_alle(self, query: str, max_treffer: int = 300, sort: str | None = None) -> tuple[list[dict], int, bool]:
        """Blättert bis max_treffer. Rückgabe: (treffer, gesamtzahl, abgeschnitten)."""
        treffer: list[dict] = []
        page = 0
        gesamt = 0
        while True:
            data = self.suche(query, page=page, size=100, sort=sort)
            gesamt = int(data.get("totalElements", 0))
            treffer.extend(data.get("trademarks", []))
            page += 1
            if len(treffer) >= gesamt or len(treffer) >= max_treffer or page >= int(data.get("totalPages", 1)):
                break
        return treffer[:max_treffer], gesamt, gesamt > len(treffer[:max_treffer])

    def marke(self, nummer: str) -> dict:
        return self._get(f"{TM_PATH}/trademarks/{nummer.strip()}")

    # -- Goods and Services (Spec 1.2.0, verifiziert gegen specs/goodsandservices.json) ----
    #   GET  /terms?language=de&termText=…&classNumber=…&page=&size=   → {terms:[{text,classNumber,conceptId,taxonomyParentId}], totalElements,…}
    #   POST /terms-suggestion-list?maxSuggestions=N  {language, texts[], classNumber?} → {suggestions:[{sourceTerm, suggestedTerms:[Term]}]}
    #   POST /classification-validation {sourceLanguage, goodsAndServices:[{classNumber, terms[]}]}
    #        → {sourceLanguage, goodsAndServices:[{classNumber, terms:[{text, harmonized, conceptId, errors[]}]}]}
    #   GET  /classHeadings?language=de → {headings:[{classNumber, heading}]}
    #   GET  /taxonomy?language=de&termText=… → Taxonomiebaum

    def gs_terms(self, term_text: str, sprache: str = "de", klassen: list[int] | None = None,
                 page: int = 0, size: int = 50, varianten: bool = True) -> dict:
        params: dict[str, Any] = {"language": sprache, "termText": term_text, "page": page,
                                  "size": max(10, min(100, size)), "includeTermVariants": str(varianten).lower()}
        if klassen:
            params["classNumber"] = [str(k) for k in klassen]
        return self._get(f"{GS_PATH}/terms", params)

    def gs_suggest(self, texte: list[str], sprache: str = "de", klasse: int | None = None,
                   max_suggestions: int = 10) -> dict:
        body: dict[str, Any] = {"language": sprache, "texts": texte}
        if klasse:
            body["classNumber"] = klasse
        url = f"{GS_PATH}/terms-suggestion-list?maxSuggestions={int(max_suggestions)}"
        return self._post(url, body)

    def gs_validate(self, eintraege: list[dict], sprache: str = "de") -> dict:
        """eintraege: [{"classNumber": 42, "terms": ["…", "…"]}]"""
        return self._post(f"{GS_PATH}/classification-validation",
                          {"sourceLanguage": sprache, "goodsAndServices": eintraege})

    def gs_class_headings(self, sprache: str = "de") -> dict:
        return self._get(f"{GS_PATH}/classHeadings", {"language": sprache})
