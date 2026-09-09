# mcp-markenrecherche

MCP-Server für die Markenrecherche in Österreich und der EU — gebaut für den Einsatz in der
Kanzlei mit lokalen oder gehosteten Sprachmodellen (Claude Desktop, Cherry Studio, Msty, MCPProxy).

*An MCP server for trade mark clearance searches in Austria and the EU. It queries the official
EUIPO APIs (Trademark Search, Goods and Services) and TMview, generates search variants
deterministically (stems, wildcards, spelling variants, phonetics), deduplicates and scores the
hits, and hands the model one consolidated result with a search log and explicit gaps. The model
does the legal assessment; the server never invents a hit. Documentation is in German because the
target audience is Austrian practitioners.*

## Grundsatz

Datenbeschaffung deterministisch im Server, Bewertung im Modell. Kein Tool erfindet Treffer; jeder
Treffer trägt Registernummer, Amt, Quelle und Abrufzeitpunkt. Was nicht abgefragt werden konnte,
steht als Recherchelücke im Ergebnis — nie stillschweigend weggelassen.

Das Ergebnis ist eine **Vorrecherche**. Sie ersetzt weder die amtliche Ähnlichkeitsrecherche des
Österreichischen Patentamts noch eine kommerzielle Vollrecherche.

## Quellen

| Register | Zugang | Was kommt |
|---|---|---|
| Unionsmarken und IR mit EU-Benennung | EUIPO Trademark Search API 1.1.0 (offiziell, OAuth) | Trefferliste, Vollauszug mit Verzeichnis, Status, Fristen |
| Nizza-Klassifikation / harmonisierte Datenbank | EUIPO Goods and Services API 1.2.0 (offiziell) | Begriffsvorschläge, Validierung „harmonisiert ja/nein", Klassenüberschriften |
| Nationale Register (AT, DE, CH, …) und WIPO | TMview — interne JSON-Endpunkte der Weboberfläche | Kurzliste, Vollauszug mit Verzeichnis, Inhaber, Vertreter |

**Zu TMview:** Es gibt keine dokumentierte API. Der Server spricht die Endpunkte an, die die
Weboberfläche selbst benutzt (`/tmview/api/search/results`, `/tmview/api/trademark/detail/{ST13}`,
Browser-Session nötig). Das kann sich ohne Ankündigung ändern, und die Nutzungsbedingungen von
tmdn.org sind auf manuelle Nutzung ausgelegt. Deshalb: Rate-Limit (`TMVIEW_PAUSE`, Standard 0,4 s),
wenige Abfragen je Lauf, und jeder Ausfall wird als Recherchelücke gemeldet. Nutzung auf eigene
Verantwortung.

## Tools

| Tool | Zweck |
|---|---|
| `recherche_lauf` | **Der eigentliche Lauf** über mehrere Register: Varianten bilden, abfragen, deduplizieren, Zeichenähnlichkeit bewerten, eine sortierte Trefferliste plus Suchprotokoll und Recherchelücken. `register` Standard `["EU","AT"]`, optional `"WO"`, `"DE"`, `"CH"` … |
| `eutm_get` / `tmview_get` | Vollauszug einer EU-Marke bzw. einer nationalen Marke (ST13) inkl. Waren-/Dienstleistungsverzeichnis |
| `nizza_suggest` / `nizza_validate` / `nizza_klassenueberschriften` | Klassifizierung gegen die harmonisierte Datenbank |
| `eutm_search` / `tmview_search` / `varianten` | Einzelabfragen und Einblick in die Variantenlogik |
| `marken_status` | Konfiguration, Umgebung (sandbox/production), Token-Test |

Ein passender Systemprompt für den Agenten liegt in `prompts/systemprompt_beispiel.md`
(Platzhalter für Kanzlei und Anwalt ausfüllen).

## Einrichtung

Voraussetzungen: Python ≥ 3.12 und [uv](https://docs.astral.sh/uv/).

1. **EUIPO-Zugang.** Auf https://dev.euipo.europa.eu mit dem EUIPO-Konto einloggen, eine App
   anlegen und die Produkte **Trademark search 1.1.0** und **Goods And Services 1.2.0**
   abonnieren. „Key" im Portal ist die Client-ID. Die Freischaltung der Subscriptions durch die
   EUIPO kann Identitätsnachweise erfordern (Pass, Adressnachweis) und dauert; bis dahin antwortet
   die API mit 403. Für Tests gibt es ein getrenntes Sandbox-Portal
   (https://dev-sandbox.euipo.europa.eu, eigene App, eigene Keys, eingefrorener Datenbestand).
2. `.env.example` nach `.env` kopieren und ausfüllen:

   ```
   EUIPO_CLIENT_ID=…              # App im Produktivportal
   EUIPO_CLIENT_SECRET=…
   EUIPO_SANDBOX_CLIENT_ID=…      # optional: App im Sandbox-Portal
   EUIPO_SANDBOX_CLIENT_SECRET=…
   EUIPO_ENV=production           # oder sandbox
   ```

3. Start: `uv --directory /pfad/zu/mcp-markenrecherche run python src/server.py`
   (uv legt beim ersten Start das `.venv` an). Tests: `uv run --extra dev pytest`.

### Claude Desktop / Cherry Studio (JSON-Import)

```json
{
  "mcpServers": {
    "marken": {
      "command": "uv",
      "args": ["--directory", "/pfad/zu/mcp-markenrecherche", "run", "python", "src/server.py"]
    }
  }
}
```

GUI-Apps bekommen auf macOS nicht den PATH der Shell: wenn der Start mit „command not found"
scheitert, den absoluten Pfad zu `uv` eintragen (`which uv`, z. B. `/opt/homebrew/bin/uv`).
Umgebungsvariablen sind nicht nötig, die `.env` liegt im Projektordner.

### MCPProxy

Eintrag wie oben; die Tools erscheinen dann als `marken__recherche_lauf` usw. Für einen Agenten
empfiehlt sich ein eigenes Toolset mit `marken__*`, einem RIS-Tool für Judikatur und einem
Word-Export — je weniger Tools im Kontext, desto verlässlicher wählt das Modell.

## Wie der Recherchelauf arbeitet

Die EUIPO-API kann nur exakt und Wildcard, TMview „enthält", „exakt" und eine eigene unscharfe
Suche. Phonetik und Schreibvarianten bildet deshalb der Server (`src/varianten.py`): prägende
Bestandteile ohne kennzeichnungsschwache Zusätze, Wildcards am Wortanfang, Schreibvarianten
(C/K, PH/F, I/Y, Konsonantenverdopplung …), Konsonantenskelett (`KL*R*X` findet KLARTEXT und
KLÄR-FIX), bei `umfang="breit"` Vokaltausch. Für „Klarox" sind das neun Suchstrings, für
„Sonnenblick" siebzehn — ein Tool-Aufruf statt zwanzig.

Jeder Treffer bekommt einen Ähnlichkeitsscore (0–1) aus Schriftbild (normalisierte
Editierdistanz), Klang (Kölner Phonetik) und Enthaltensein; bei Mehrwortzeichen zählt der
Durchschnitt über die Bestandteile. Stufen: hoch ≥ 0,85, mittel ≥ 0,65. Der Score ist eine
Sortierhilfe, keine Rechtsbewertung.

`nur_lebend=True` (Standard) liefert nur Marken, die ein Hindernis sein oder werden können.
Abgelaufene EU-Marken werden mit dem Hinweis auf die sechsmonatige Verlängerungsnachfrist
(Art 53 Abs 3 UMV) versehen. IR mit EU-Benennung, die im EU-Teil schon enthalten sind, werden
bei `"WO"` nicht doppelt gelistet. Fällt die EUIPO-API aus, holt der Lauf die EU-Marken
ersatzweise über TMview (Amt „EM") und sagt das in den Lücken.

## Grenzen

Nur Wortbestandteile — reine Bildähnlichkeit wird nicht recherchiert. Nicht registrierte
Kennzeichenrechte (Firmenwortlaut, Domains, Etablissementbezeichnungen) sind nicht abgedeckt.
TMview-Treffer sind Kurzdaten der Webabfrage; für die Bewertung der Warenähnlichkeit ist der
Vollauszug (`tmview_get`, `eutm_get`) zu ziehen. Bei sehr häufigen Wortstämmen wertet der Lauf
je Variante nur `max_pro_variante` Treffer aus und meldet das.

## Lizenz

MIT — siehe `LICENSE`. Keine Gewähr für Vollständigkeit oder Richtigkeit der Rechercheergebnisse.
