# mcp-markenrecherche

**Vorrecherche für Marken mit Wortbestandteil — in der EU und in nationalen Registern, direkt aus dem Chat.**

Ein MCP-Server für die Kanzlei: Du sagst deinem Sprachmodell (Claude Desktop, Cherry Studio,
Msty …), welches Zeichen du für welche Waren oder Dienstleistungen prüfen willst, und bekommst
eine belegte Trefferliste, einen Klassifizierungsvorschlag und am Ende einen Aktenvermerk.
Die rechtliche Beurteilung bleibt bei dir. Kein Treffer wird erfunden, jede Lücke wird benannt.

*Pre-filing clearance search for word and word/figurative marks (EUIPO API, TMview, Nice
classification) as an MCP server. German documentation — the target audience is Austrian and
German-speaking practitioners.*

---

## Was du bekommst

**Eine Recherche in einem Satz.** „Prüf mir KIRVO für Software und IT-Beratung in Österreich
und der EU." Das Modell klassifiziert, lässt den Server die Register abfragen, zieht für die
kritischen Treffer den Vollauszug und schreibt den Aktenvermerk — mit Ampel je Treffer,
Suchprotokoll und den offenen Punkten.

**Belegte Treffer statt Modellwissen.** Jeder Treffer trägt Registernummer, Amt, Inhaber,
Klassen, Status, Anmeldetag und den Abrufzeitpunkt aus dem Register. Was der Server nicht
abfragen konnte, steht als Recherchelücke im Ergebnis — nie stillschweigend weggelassen.

**Varianten, die du händisch nie alle suchen würdest.** Der Server bildet aus dem Zeichen
Wortstämme, Schreibweisen (C/K, PH/F, Doppelkonsonanten …), Wildcards und lautgleiche
Wortanfänge und fragt jede Variante ab. Ein Kunstwort wie KIRVO liefert so auch CIRVO, GIRVO
oder KIRBO, ein zusammengesetztes Zeichen auch die Serienmarken mit demselben Stamm. Die
Treffer werden nach Schriftbild und Klang sortiert, die ähnlichsten zuerst.

**Klassifizierung gegen die amtliche Datenbank.** „Welche Klassen und Begriffe für ein
Unternehmen, das Physiotherapie und Trainingsgeräte anbietet?" — das Modell schlägt vor, der
Server prüft jeden Begriff gegen die harmonisierte Datenbank der EUIPO und sagt, ob er so
akzeptiert wird oder ein Beanstandungsrisiko trägt.

**Ein Aktenvermerk, der im Akt bestehen kann.** Auftrag, Rechercheumfang, Klassifizierung,
Trefferübersicht, Bewertung der gelben und roten Treffer nach § 10 MSchG / Art 8 UMV, absolute
Eintragungshindernisse, Empfehlung, Recherchelücken, Suchprotokoll. Der Systemprompt dafür
liegt bei (`prompts/systemprompt_beispiel.md`).

## Was du nicht bekommst

Das ist eine **Vorrecherche**. Sie sagt dir, ob eine amtliche Ähnlichkeitsrecherche oder eine
kommerzielle Vollrecherche nötig ist — sie ersetzt keine von beiden.

Gefunden wird nur, was einen Wortbestandteil hat. **Reine Bildmarken** und die grafische
Seite von Wort-Bild-Marken bleiben außen vor. Nicht registrierte Kennzeichenrechte
(Firmenwortlaut, Domains, Etablissementbezeichnungen) sind nicht abgedeckt. Und die
nationalen Register laufen über TMview, das nur für gelegentliche Abfragen ausgelegt ist
(Details unten).

## Typische Fragen an den Agenten

„Welche Nizza-Klassen kommen für ein Unternehmen in Frage, das Mähroboter verkauft und wartet?"

„Prüf das Zeichen SARUMO für Kosmetik und Nahrungsergänzung, Gebiet Österreich und EU."

„Gibt es in Klasse 30 ältere Marken, die wie VANDELIX klingen?"

„Zieh mir den Vollauszug zu 018512783 und sag mir, ob das Verzeichnis mit Software-Wartung
kollidiert."

„Ist ‚Software-Wartung für Zahnärzte' ein harmonisierter Begriff in Klasse 42?"

Das Modell entscheidet selbst, welche Werkzeuge es dafür braucht. Für eine vollständige
Recherche ist `recherche_lauf` der Kern; alles andere sind Einzelabfragen und Vollauszüge.

## Einrichtung

Du brauchst Python ab 3.12, [uv](https://docs.astral.sh/uv/) und einen EUIPO-Zugang.

1. **EUIPO-Zugang.** Auf https://dev.euipo.europa.eu mit deinem EUIPO-Konto einloggen, eine
   App anlegen und die Produkte **Trademark search** und **Goods And Services** abonnieren.
   „Key" ist die Client-ID. Die EUIPO schaltet die Subscriptions nach Prüfung frei (Pass und
   Adressnachweis werden verlangt); bis dahin antwortet die API mit 403. Zum Testen gibt es
   ein eigenes Sandbox-Portal (https://dev-sandbox.euipo.europa.eu) mit eigenen Keys und
   eingefrorenem Datenbestand — Sandbox-Treffer gehören in keinen Aktenvermerk.
2. **Konfiguration.** `.env.example` nach `.env` kopieren und Keys eintragen:

   ```
   EUIPO_CLIENT_ID=…
   EUIPO_CLIENT_SECRET=…
   EUIPO_ENV=production        # oder sandbox (dann EUIPO_SANDBOX_CLIENT_ID/_SECRET)
   ```

3. **Im Chat-Programm eintragen.** Claude Desktop und Cherry Studio nehmen denselben Eintrag
   (Cherry: „Add Server → Import from JSON"; bei Cherry den absoluten Pfad zu `uv` verwenden,
   `which uv` zeigt ihn):

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

   Beim ersten Start richtet uv die Umgebung selbst ein.

4. **Systemprompt.** `prompts/systemprompt_beispiel.md` in den Assistenten kopieren, die
   Platzhalter (Kanzlei, Anwalt, Ich- oder Wir-Form) ausfüllen. Ohne Prompt funktionieren die
   Werkzeuge auch, aber der Aktenvermerk kommt erst mit ihm in Form.

Erste Probe: „Prüf bitte den Zugang" — das Modell ruft `marken_status` auf und meldet, ob
EUIPO und TMview erreichbar sind.

## Werkzeuge

| Werkzeug | Was es tut |
|---|---|
| `recherche_lauf` | Der vollständige Lauf: Varianten bilden, Register abfragen, deduplizieren, nach Ähnlichkeit sortieren; liefert Trefferliste, Suchprotokoll und Recherchelücken. `register` Standard `["EU","AT"]`, dazu `"WO"`, `"DE"`, `"CH"` … |
| `eutm_get`, `tmview_get` | Vollauszug einer EU-Marke bzw. einer nationalen Marke mit Waren-/Dienstleistungsverzeichnis, Inhaber, Status, Fristen |
| `nizza_suggest`, `nizza_validate`, `nizza_klassenueberschriften` | Klassifizierung: Begriffe vorschlagen, gegen die harmonisierte Datenbank prüfen, Klassenüberschriften nachschlagen |
| `eutm_search`, `tmview_search`, `varianten` | Einzelabfragen und Einblick, welche Varianten der Lauf bildet |
| `marken_status` | Zugang und Erreichbarkeit prüfen |

## Quellen und ihre Grenzen

**EUIPO** (Unionsmarken und internationale Registrierungen mit EU-Benennung) über die
offiziellen APIs *Trademark Search* und *Goods and Services*. Stabil, vollständig, mit
Verzeichnis.

**TMview** (nationale Register wie AT, DE, CH sowie WIPO) über die Schnittstelle, die die
TMview-Weboberfläche selbst benutzt. Es gibt dafür keine dokumentierte API; sie kann sich
ohne Ankündigung ändern, und die Nutzungsbedingungen von tmdn.org sind auf manuelle Nutzung
ausgelegt. Der Server hält deshalb 1,5 Sekunden Pause zwischen den Anfragen, stellt je Lauf
nur wenige, und hört beim ersten Anzeichen einer Sperre sofort auf — dann steht das im
Ergebnis als Lücke, und du prüfst das nationale Register von Hand nach. Zwei, drei Recherchen
am Tag sind unauffällig; Serienläufe sind es nicht. Nutzung auf eigene Verantwortung.

## Wie der Ähnlichkeitsscore zu lesen ist

Jeder Treffer bekommt einen Wert zwischen 0 und 1 aus Schriftbild (Editierdistanz), Klang
(Kölner Phonetik) und der Frage, ob das eine Zeichen im anderen enthalten ist; bei
Mehrwortzeichen zählt der Durchschnitt der prägenden Bestandteile. Ab 0,85 gilt ein Treffer
als „hoch", ab 0,65 als „mittel". Der Wert ist eine Sortierhilfe: Er sagt, wo du zuerst
hinschaust, nicht, ob Verwechslungsgefahr besteht. Das entscheidet die Bewertung nach Zeichen-
und Warenähnlichkeit, Kennzeichnungskraft und Publikum — und die machst du.

## Für Entwickler

Python 3.12, FastMCP, httpx. Tests: `uv run --extra dev pytest`. Die Variantenlogik liegt in
`src/varianten.py`, die Anbindungen in `src/euipo_client.py` und `src/tmview_client.py`, die
Werkzeuge in `src/server.py`. Statusfilter, Nachfrist bei abgelaufenen Marken (Art 53 Abs 3
UMV), Deduplizierung von IR mit EU-Benennung und die Erkennung des TMview-Bot-Schutzes sind im
Code kommentiert. Die Antworten der Werkzeuge sind deutsch beschriftet; die erste Zeile jeder
Werkzeugbeschreibung trägt ein englisches Stichwort, damit auch englisch angesprochene Modelle
das richtige Werkzeug finden.

## Lizenz

MIT — siehe `LICENSE`. Keine Gewähr für Vollständigkeit oder Richtigkeit der
Rechercheergebnisse.
