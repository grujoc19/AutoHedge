# AutoHedge: Repository-Review und Entwicklungsplan

Stand: 20. Oktober 2026. Dies ist eine Analyse und ein Implementierungsplan; der aktuelle Umsetzungsstand ist unten separat aufgeführt.

## Umsetzungsstand nach Merge von PR #1

PR #1 wurde als Main-Commit `9027880` übernommen. Das erste Stabilisierungspaket
implementiert die Schritte 1.1–1.3: zusätzliche Laufpfad- und Runtime-Tests,
verzögerten Paket-/CLI-Import, ein zentrales Paketmanifest mit uv.lock und eine
konsolidierte CI. Die folgenden Review-Befunde dokumentieren den vorherigen
Stand und sind entsprechend historisch zu lesen. Inzwischen sind auch 1.4–1.6
lokal umgesetzt: zentrale Settings und geschützte Logs, gemeinsame Providerfehler,
validierte Domänenverträge und fünf explizite Research-Stufen mit frischen Teams.
Phase 1 ist als Commit `5daea6e` auf `feat/stabilize-research-core` veröffentlicht.
Schritt 2.1 ist als Commit `71e7384` veröffentlicht: normalisierte Quote-/OHLCV-/News-
Verträge, Provider-Protokolle, Datenqualitätsprüfung vor und während Research
sowie ein nachvollziehbarer Prüfbericht. Details in `docs/MARKET_DATA.md`.
Schritt 2.2 ist implementiert: validierte Jupiter-/Exa-Adapter, begrenzte
Read-Retries und offline geprüfte HTTP-Verträge. Jupiter-Preise erhalten ohne
vertrauenswürdige Blockzeitauflösung keinen erfundenen Ereigniszeitpunkt;
datierte Exa-Quellen werden normalisiert. Details in `docs/PROVIDERS.md`.
Es gibt noch keine automatische Zusammenstellung vollständiger Marktdaten-Snapshots,
Indikatoren, validierten KI-Handelssignale oder deterministische Risk-Freigabe; dies folgt ab Phase 2. Die installierte lokale
Runtime wurde mit Python 3.12 geprüft; GitHub-Matrixergebnisse sind noch offen.

## Geprüfte Grundlage

- Repository: `grujoc19/AutoHedge`, Main-Commit `c549c7950da112286e76725d49f6a25de8fa99bd`.
- [PR #1](https://github.com/grujoc19/AutoHedge/pull/1), Head `2da5662d569e644ea412e35f15279eb2b9fc9ae4`, sechs Commits, sechs geänderte/neue Dateien. Diff und öffentliche PR-Beschreibung gelesen.
- Alle versionierten Python-Dateien, Paketdeklarationen, Dokumentation, GitHub-Workflows und Konfiguration geprüft. Die beiden großen historischen Trade-CSVs wurden hinsichtlich Format und Verwendbarkeit eingeordnet, nicht als Performancebeweis ausgewertet.
- PR über einen exportierten Snapshot in `/tmp/autohedge-pr1-review` geprüft. Kein Branchwechsel, kein Worktree, keine Änderung am PR, keine Remote-Kommentare und kein Merge.
- GitHub-GraphQL meldet `Forbidden`. Deshalb sind Actions-Ergebnisse, Reviews und Mergefähigkeit nicht unabhängig bestätigt; lokale Prüfergebnisse ersetzen diese Statusinformationen nicht.

## Ergebnis und Grenzen

Der funktionierende Kern ist ein Python-Rechercheprogramm mit CLI, Swarms-Agenten, Prompts, Conversation und einzelnen Daten-/Swap-Adaptern. Die README verspricht bereits kontinuierliches autonomes Solana-Trading, der Anwendungspfad implementiert das nicht. Fehlende API-Zugänge sind ein zusätzlicher Umgebungsblocker, nicht die Ursache der architektonischen Lücken.

Lokal verifiziert:

| Prüfung | Ergebnis | Aussagegrenze |
| --- | --- | --- |
| PR: `python -m unittest discover -s tests -v` | 3 Tests bestanden | Swarms, Prompts und Exa werden ersetzt; keine Integration nachgewiesen |
| PR: echte `build_director_agent()`-Aufrufe | Frische Director- und vier Worker-Objekte | Keine entfernten LLM-Aufrufe |
| PR: echte AutoHedge-/Conversation-Initialisierung | Bestanden | Kein vollständiger Research-/Trading-Zyklus |
| PR: CLI `python -m autohedge --version` | `0.1.5` | Warnung wegen fehlendem OpenAI-Key |
| PR: Wheel-Build | Erfolgreich | Kein neuer Rechner und keine Python-Versionsmatrix getestet |
| Vorhandene Umgebung: `uv pip check` | 110 Pakete konsistent | Kein Nachweis zukünftiger Auflösung ungepinnter Abhängigkeiten |
| PR: Director mit lokal ersetzter Modellantwort und Worker-Antwort | Handoff läuft, danach endet der Director nach einem Modellschritt | Kontrollflussfehler bestätigt, keine echte Modellqualität bewertet |
| PR: alter CI-Befehl `python -m unittest tests/` | Fehler: `No module named 'tests/'` | Auf lokalem Python 3.12 reproduziert |
| Solana-Signaturaufbau mit zwei lokalen Testsignierern | Signaturliste wird auf eine Signatur reduziert; Sanitization scheitert | Nicht jeder Single-Signer-Jupiter-Swap ist betroffen; nichts übertragen |
| Unveränderter experimenteller Market-Maker, vorheriger lokaler Test | Standardpaar `BTC/USDT` verursacht CSV-Pfadfehler | Separater Prototyp, kein Hauptpfad |
| Experimenteller Backtest mit 100 konstanten Kursen | 200 Trades, ungefähr +10 auf 10.000 Startkapital | Gewinn entsteht durch unterstellte Ausführungen; kein belastbarer Strategienachweis |

Ausgeführte Runtime-Prüfungen nutzten Python 3.12.14, Swarms 16.0.1, swarm-models 0.3.2 und LiteLLM 1.76.1. Keine echten Orders, Wallet-Zugriffe oder kostenpflichtigen Modellläufe ausgeführt. Der aktuelle Runtime-Status meldet keine konfigurierten Secrets. Live-Daten und externe API-Verträge sind deshalb nicht end-to-end validiert.

## Review von PR #1

**Übernehmen:** Die Agenten-Factory beseitigt globale Agentensingletons. `main.py` verwendet sie pro `run()`. Die normale Uhrzeit ist pro Factory-Aufruf neu und UTC-bewusst. `.env.example` nennt jetzt Exa und den tatsächlich gelesenen Solana-Key. DEVELOPMENT.md beschreibt den aktuellen Research-Umfang ehrlich. Die drei Regressionstests sind ein sinnvoller Anfang.

**Vor dem Merge beziehungsweise als unmittelbar vorbereitete kleine Ergänzung:**

1. Einen Test ergänzen, der zwei `AutoHedge.run()`-Aufrufe ausführt und die tatsächlich verwendete Factory pro Aufruf beobachtet. Der bestehende Factory-Test würde bestehen, wenn `main.py` wieder einen Director zwischenspeicherte.
2. Einen separaten Integration-Smoke-Test mit installierten, festgelegten Paketen ergänzen: Paketimport, CLI, echte Agenten-Factory und Tool-Schemata, ohne entfernten Modellaufruf. Die bisherige CI installiert absichtlich keine Runtime-Pakete und ersetzt sämtliche relevanten Imports.
3. Veraltete CI konsolidieren: Der neue Workflow behebt weder fehlende lokale Actions noch Dockerfile-/Makefile-Verweise oder den falschen unittest-Aufruf. Actions-Status vor Merge über GitHub prüfen; hier nicht bestätigt.
4. README mit DEVELOPMENT.md abgleichen. Noch immer verspricht sie vollständiges autonomes Solana-Trading. Die Formulierung „paper trading“ im Env-Beispiel darf nicht als bereits vorhandener integrierter Modus verstanden werden.

Nicht als PR-Regression behandeln: fehlende Orderausführung, Markt-/Portfolioeingaben und Risk-Gates bestanden schon vorher. PR #1 soll eine eng begrenzte Stabilisierung bleiben. Das injizierbare `now` sollte später ausschließlich timezone-aware Werte akzeptieren oder explizit nach UTC normalisieren; derzeit gilt UTC nur für den Standardpfad.

## Befunde nach Priorität

| ID | Priorität | Befund und Auswirkung | Beleg |
| --- | --- | --- | --- |
| F01 | P0 | Hauptpfad ruft nur Director auf; Ausführungstools sind nicht angebunden. Textvorschläge sind keine ausgeführten Orders. | `autohedge/main.py:49`, `autohedge/workers.py`, `autohedge/tools/tools_registry.py:7` |
| F02 | P0 | Ein Director-Modellschritt reicht nicht für abhängige Analyse, Risiko und abschließende Entscheidung. Mehrere Handoffs desselben Aufrufs können parallel laufen und haben dann keine Ergebnisse voneinander. | `workers.py`: `max_loops=1`, `handoffs`; lokal bestätigter Swarms-Kontrollfluss |
| F03 | P0 | Quant und Risk besitzen keine Datenwerkzeuge. Es fehlen verpflichtende Candles, Portfolio-/Orderzustand und berechnete Indikatoren. Kennzahlen sind nicht durch die Anwendung belegt. | `workers.py`, `prompts.py`, ungenutzte Yahoo-/Massive-Adapter |
| F04 | P0 | Keine validierten Signal-/Orderverträge und keine technisch zwingenden Positions-, Verlust- oder Liquiditätslimits. Prompt-Anweisungen ersetzen keine Freigabe. | `workers.py`, `prompts.py`, `main.py` |
| F05 | P0 vor Live | Signierer akzeptiert beliebige übergebene Transaktionen ohne Abgleich mit genehmigter Order; erwartet faktisch einen Signierer. Vorhandene/weitere Signaturen werden verworfen. | `tools/ultra_tools.py:197` |
| F06 | P0 vor Live | Kein dauerhafter Orderlebenszyklus, Idempotenzschutz, Reconciliation oder bestätigter Fill-Abgleich. Timeout nach Sendung bleibt wirtschaftlich unaufgelöst. | `tools/ultra_tools.py:227`, `main.py` |
| F07 | P1 | Aktienprompts mit Stückzahlen, Long/Short und Limit-/Stop-Orders passen nicht zum Solana-Spot-Swap-Adapter mit Mint-Adressen und Basis-Einheiten. | `prompts.py`, `tools/ultra_tools.py:85` |
| F08 | P1 | Globale Agenten und Importzeit im Main; PR #1 behebt beides. AutoHedge-Conversation akkumuliert bei wiederholtem `run()` weiterhin Ausgaben; Zykluszustand und Sitzungshistorie sind nicht getrennt. | `main.py:31`, `main.py:46`, PR-Diff |
| F09 | P1 | Ungepinntes `swarms`, `pydantic` usw.; kein Projekt-Lockfile. requirements und pyproject widersprechen sich. Python-3.9-CI widerspricht `>=3.10`. | `pyproject.toml`, `requirements.txt`, `.github/workflows/test.yml` |
| F10 | P1 | swarm-models ist im Hauptpfad ungenutzt und bringt u. a. Transformers mit. FastAPI/Uvicorn sind deklariert, aber kein API-Server existiert. Experimente importieren nicht deklarierte/nicht installierte `cryptoagent` und `websocket`. | Paketmetadaten, `experimental/crypto_agent_wrapper.py`, `experimental/btc_agent.py` |
| F11 | P1 | CI enthält kopierte Pfade `swarms`, `swarms_torch`, fehlende Actions, Make-/Docker-/Docs-Ziele. Ein Formatierungsworkflow versucht Änderungen zurückzupushen. | `.github/workflows/*` |
| F12 | P1 | Fehlende Daten werden als `{}` oder Fehlertext zurückgegeben; keine gemeinsame Fehlerklasse und keine zwingende Handelsverweigerung. Exa-Kontext ist auf 20 Zeichen gesetzt, bei zwei Quellen; Text/Summary können daneben vorhanden sein. | `yahoo_api.py`, `exa_search_tool.py:51` und `:102` |
| F13 | P1 | API-Konfiguration ist verteilt; OpenAI-Check prüft nur Präsenz. POLYGON_BASE_URL wird dokumentiert, aber ignoriert. Modellnamen und Logging-Schalter sind hart kodiert. | `env_loader.py`, `polygon_api.py:30`, `workers.py`, `cli.py` |
| F14 | P1 | Keine integrierte Paper-Ausführung, kein realistischer gemeinsamer Backtest. Experimenteller Backtest kauft und verkauft ohne Fill-Bedingung und Kosten; Bestandslimit begrenzt Einzelorders statt Gesamtbestand. | `experimental/market_making.py:176`, `:356` |
| F15 | P2 | Experimenteller CSV-Dateiname enthält `/`; HTTP-Nichterfolg kann aus fetch_market_data ohne MarketData zurückkehren; Fallback kann erfundene Kurse liefern. BTC-Monitor baut bei Close nur ein neues WebSocketApp, startet es dort nicht. | `experimental/market_making.py:71` und `:90`, `experimental/btc_agent.py:140` |
| F16 | P2 | output_file_path ungenutzt, CLI schneidet Ergebnisse auf 2.000 Zeichen ab; keine durchgängige run_id oder revisionssichere Zuordnung von Daten, Modellen, Risiko und Orders. | `main.py:26`, `cli.py:180` |
| F17 | P2 | Rund 37 MB versionierte historische CSV-Logs ohne Schema-/Strategie-/Kostenprovenienz; experimental ist trotz versionierter Dateien in .gitignore und wird von gewöhnlichem rg übersprungen. | `logs/`, `.gitignore` |

P0 bedeutet fehlende Kernfähigkeit oder notwendige Sperre vor Live-Betrieb, nicht eine Behauptung über bereits erfolgte Verluste. Externe API-Spezifikationen und mögliche Paket-Sicherheitslücken müssen gesondert geprüft werden; erfolgreiche Installation ist kein Security-Audit.

## Was erhalten bleibt

| Komponente | Weiterverwendung | Gezielte Änderung |
| --- | --- | --- |
| CLI und Paket-Entry-Points | Einstieg für Research, später Paper/Status | Abhängigkeiten spät laden, vollständiges JSON optional ausgeben, gemeinsame Fehlercodes |
| Agenten-Factory aus PR #1 | Basis für isolierte Modellrollen | Konfiguration, injizierte Uhr/LLM, explizite Orchestrierung |
| Director-, Quant-, Sentiment-, Risk-Prompts | Fachliche Rollen und Begründungen | Assetklassen trennen, gemeinsame validierte Ein-/Ausgaben; Quantwerte vorab berechnen |
| Swarms | Zunächst Agenten-/Modelladapter | Version festlegen, kontrollierter Ablauf im Anwendungscode statt unbeschränkter Tool-Delegation |
| Jupiter Search/Price/Order/Holdings | Solana-Adapter | Typisierte Antworten, Qualitätskontrollen, Timeouts/Backoff, später abgesicherte Orderanbindung |
| Yahoo/Massive-Adapter | Optionaler Aktien-Datenbereich | Einheitliche Provider-Schnittstelle; nicht als Solana-Historienquelle verwenden |
| Exa | Nachrichten-/Quellenadapter | Zeitfilter, Quellenprovenienz, Payload-Vertrag und Fehlerbehandlung testen |
| Loguru und Pydantic | Logging und Datenverträge | Ein zentraler Logging-Einstieg, Redaction und validierte Schemas |
| Market-Maker-Experiment | Einzelne Formeln als Anschauung | Weder dessen Fill-Modell noch Ergebnisse übernehmen; nützliche Logik nur nach Tests migrieren |

Kein vollständiger Rewrite und zunächst keine Microservices. Ein modularer Python-Prozess mit klaren Schnittstellen genügt. Historische Logs bleiben erhalten; eine spätere Archivierung erfolgt ohne Umschreiben der Git-Historie.

## Zielarchitektur und feste Grenzen

Arbeitsannahme: zuerst Solana-Spot, zunächst wenige explizit freigegebene liquide Mint-Paare; keine automatische Ausweitung auf beliebige Memecoins und keine Short-/Hebelprodukte. Die Architektur erhält ein Asset-/Venue-Feld für spätere Erweiterungen. Vor Auswahl des historischen Solana-Datenanbieters dessen OHLCV-Abdeckung, Zeitauflösung, Kosten, Limits und Nutzungsrechte prüfen; Jupiter Search/Price allein liefern die benötigte Historie nicht.

```text
Provider -> normalisierter, zeitgestempelter Snapshot -> Python-Indikatoren
         -> Quant + Sentiment -> Director -> validierter Signalvorschlag
         -> feste Risikoprüfung + Portfolio-Snapshot -> genehmigte Order
         -> PaperBroker | getrennt freigegebener LiveBroker
         -> Ledger, Fill-Abgleich, Audit-Ereignisse
```

- Risk-Agent liefert zusätzliche Einschätzungen; er kann Regeln verschärfen, niemals lockern. Nur die feste Risk-Engine berechnet zulässige Mengen und genehmigt Orders.
- Kein LLM erhält Signierfunktion, privaten Schlüssel, unkontrollierten HTTP-Zugang oder die Möglichkeit, Live-Modus/Kill-Switch umzuschalten.
- Ein BUY-/SELL-Signal allein autorisiert keine Ausführung. Fehlende/ungültige/veraltete Daten führen zu HOLD beziehungsweise einer expliziten Ablehnung.
- SELL reduziert vorhandenen Bestand. Eröffnung von Short-Positionen ist im ersten Spot-Umfang ausgeschlossen.
- Geldwerte mit Decimal bzw. Integer-Basiseinheiten; keine float-basierte Order- oder Kontobuchhaltung. Zeitstempel UTC-aware, Uhr injizierbar.
- Vorschlag für Module, schrittweise anlegen: `config`, `domain`, `data`, `indicators`, `agents`, `orchestration`, `risk`, `execution`, `portfolio`, `backtesting`, `runtime`. Bestehende `tools` zunächst über Adapter weiterverwenden; keine reine Massenverschiebung.

## Kleine Implementierungsschritte

Jede Zeile ist ein separat reviewbarer Commit oder kleiner PR. Tests des jeweiligen Verhaltens gehören in denselben Schritt. Vorgänger sind explizit angegeben; spätere Phasen rechtfertigen keine direkte Live-Anbindung in Phase 1.

### Phase 1: Bestehendes Projekt stabilisieren — zuerst

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 1.1 | PR #1 um Laufpfad-Regression und echten Runtime-Smoke-Test ergänzen; README korrigieren | PR #1 | Zwei run()-Aufrufe bauen unabhängige Teams; Import erzeugt keine Agenten; reale Factory/CLI ohne Keys nutzbar |
| 1.2 | Ein Paketmanifest als Quelle, reproduzierbares Lockfile und Dev-Dependencies; zunächst getestetes Python 3.12, weitere Versionen nur per Matrix | 1.1 | Installation in frischer venv mit Lockfile; pip/uv check, Wheel-Installation und CLI außerhalb des Checkouts bestehen |
| 1.3 | CI konsolidieren: Offline-Tests, Runtime-Smoke, Lint und Build; fremde Ziele/Auto-Push entfernen oder passend ersetzen | 1.2 | Definierte PR-Checks laufen ohne Secrets; fehlende Imports und bewusst eingeführte Regression lassen Checks scheitern |
| 1.4 | Zentrale Settings, gemeinsame Providerfehler und Logging-Initialisierung mit run_id und Secret-Redaction | 1.2 | Env-Priorität/Defaults geprüft; optionaler Provider erfordert keine Keys; Fehler logs enthalten keine Schlüssel/Header; CLI-Hilfe ohne Remote-Zugriff |
| 1.5 | Minimale Domänenmodelle für Instrument, Snapshot, Signal(BUY/SELL/HOLD), PortfolioSnapshot und Ablehnung; Ausgabe von jedem Zyklus getrennt halten | 1.4 | Ungültige Aktion, Menge, NaN/Infinity, negative Preise und naive Zeitstempel werden abgelehnt; keine Vermischung von Zyklus und Sitzungshistorie |
| 1.6 | Expliziten, injizierbaren Research-Orchestrator statt einmaligem Director-Handoff implementieren; bestehende Factory hinter Adapter verwenden | 1.1, 1.5 | Lokale Fake-Rollen belegen Reihenfolge, abhängige Eingaben, Timeout-/Fehler-Abbruch und isolierte Zustände; Signieradapter wird nie aufgerufen |

Phasengrenze: reproduzierbarer Research-Build mit belastbaren Tests. Keine Behauptung über Marktdatenqualität, Profitabilität oder Handelsbereitschaft.

### Phase 2: Zuverlässige Marktdaten

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 2.1 | Gemeinsame Providerverträge und Datenqualitätsregeln: event_time/received_at, Quelle, Währung, Venue, Decimals und Vollständigkeit | 1.5 | Doppelte/unsortierte Candles, falsche Mint, Lücken, veraltete Daten und unrealistische Werte werden erkannt |
| 2.2 | Jupiter Search/Price und Exa über bestehende Funktionen kapseln; normalisierte Antworten, begrenzte Retries bei Reads, 429/Timeout/Schemafehler behandeln | 1.4, 2.1 | HTTP-Fixtures prüfen Headers/Payload/Parsing; leere Antworten sind keine gültigen Preise; optionale echte Read-only-Contract-Tests separat |
| 2.3 | Solana-OHLCV-/Volumenquelle nach dokumentierter Eignungsprüfung anbinden; Historie seitenweise und unveränderlich speichern | 2.1 | Überlappende Seiten, Intervallgrenzen, Provider-Ausfall und History-Replay getestet; keine stillschweigend erfundenen Ersatzkurse |
| 2.4 | RSI, MACD und realisierte Volatilität in Python berechnen; vorhandene NumPy/Pandas verwenden | 2.3 | Referenzreihen prüfen Initialisierung/Warm-up, konstante und fehlende Werte; keine zukünftigen Candles im Ergebnis |
| 2.5 | Gemeinsamen Research-Snapshot aus Kurs, Candles, Indikatoren und datierten News herstellen | 2.2, 2.4 | Ein Snapshot ist reproduzierbar, ausreichend frisch und mit Quellen belegbar; fehlende Pflichtdaten blockieren ein Handelssignal |

Phasengrenze: konsistente reale Daten und geprüfte Berechnungen; Live-Providerprüfungen mit tatsächlichen Zugängen müssen zusätzlich zu HTTP-Fixtures bestehen.

### Phase 3: Intelligente Trading-Agenten

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 3.1 | Quant interpretiert berechnete Werte; Sentiment bewertet datierte Quellen. Rollenoutputs mit Evidenz-IDs und Begrenzungen typisieren | 1.6, 2.5 | Agent kann keine nicht gelieferte Kennzahl als berechneten Fakt ausgeben; ungültiges Modell-JSON führt zu Ablehnung |
| 3.2 | Director aggregiert nach abgeschlossener Quant-/Sentiment-Analyse einen BUY-/SELL-/HOLD-Vorschlag mit Begründung | 3.1 | Widersprüche und unzureichende Evidenz führen definiert zu HOLD; keine Order bei unvollständigen Pflichtrollen |
| 3.3 | Risk-Agent liefert ergänzende Warnungen; Budget für LLM-Aufrufe, Laufzeit und begrenzte Reparaturversuche einführen | 3.2 | Schleifen/Timeouts begrenzt; Warnungen können Ausführung blockieren, aber späteres hartes Limit nicht überschreiben |
| 3.4 | Research-Evaluation mit eingefrorenen Snapshots und adversarialen News-/Tool-Antworten | 3.3 | Prompt-Injection erzeugt keine Tool-/Wallet-Berechtigung; Quellenbezüge und Schemakonformität messbar; LLM-Qualität separat von deterministischen Unit-Tests |

Phasengrenze: nachvollziehbare Signale, keine Erlaubnis zum Signieren oder Senden.

### Phase 4: Technisch erzwungenes Risikomanagement

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 4.1 | Dauerhaften minimalen Portfolio-/Orderzustand und atomare Kapitalreservierung einführen; SQLite reicht zunächst | 1.5, 3.2 | Zwei konkurrierende Vorschläge können dasselbe Kapital nicht doppelt reservieren; Neustart stellt Reservierungen wieder her |
| 4.2 | Reine Risk-Engine: Positionsgröße aus Stop-Abstand/Risikobudget, Bestand, Gesamt-Exposure, Tagesverlust und Drawdown | 4.1 | Grenzwert-/Eigenschaftstests: kein Limit überschreitbar, SELL nicht größer als freier Bestand, keine Null-/Negativgrößen |
| 4.3 | Liquidität, Quote-Alter, maximalen Preisimpact/Slippage, Gebühren- und SOL-Reserve vor Orderfreigabe prüfen | 2.2, 4.2 | Veraltete/illiquide Quotes, falsche Einheit und Überschreiten von minOut-/Kostenlimits werden abgelehnt |
| 4.4 | Solana-Tokenprüfung: Mint-/Freeze-Rechte, relevante Token-2022-Extensions/Transfergebühren, Konzentration, Routen und verkaufbare Menge prüfen | 4.3 | Fixtures für gesperrte Transfers, fehlende Rückroute und unvollständige Sicherheitsdaten blockieren; keine Garantie künftiger Verkaufbarkeit behaupten |
| 4.5 | Persistenten Kill-Switch und zentralen Ausführungszugang implementieren; genehmigte Order kurzlebig, eindeutig und an Quote/Policy gebunden | 4.4 | Jeder Ausführungspfad einschließlich direktem Adapteraufruf verlangt gültige Freigabe; Kill-Switch wirkt nach Neustart und vor Dispatch |

Phasengrenze: keine Order kann die Risk-Engine umgehen. Kill-Switch blockiert neue riskante Aktionen; Storno, Abgleich und ein regelgebundener risikoreduzierender Exit erhalten eigene Rechte. Kein unkontrollierter „alles sofort verkaufen“-Pfad.

### Phase 5: Backtesting und Paper-Trading

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 5.1 | Gemeinsames Order-/Fill-Ledger und PaperBroker hinter dem abgesicherten Ausführungszugang | 4.5 | Geld-/Token-Erhaltung, Gebühren, Reservierungen und Teilfills geprüft; niemals Signierer/Live-Endpunkt aufrufen |
| 5.2 | Eventbasierten Backtest mit historischer Uhr, spätestem verfügbarem Snapshot und konservativem Fill-Modell implementieren | 2.3, 5.1 | Keine Ausführung vor Signalzeit; kein automatischer Buy-/Sell-Gewinn bei konstantem Kurs ohne passende Fills; Spread, Kosten, Slippage und Verzögerung wirksam |
| 5.3 | Nettorendite, Drawdown aus Equity-Kurve, Profit Factor und Buy-and-Hold-Vergleich implementieren | 5.2 | Kleine handgerechnete Kontoverläufe stimmen; keine Trades/keine Verluste sauber als undefiniert behandeln, nicht künstlich auf Erfolg setzen |
| 5.4 | Zeitlich getrennte Evaluationsdaten und Walk-forward-Bericht; Modell-/Prompt-/Datenversionen speichern | 5.3 | Keine Parameterwahl anhand des Testzeitraums; identische Zeit-/Kostenbasis für Benchmark; historische News ohne spätere Informationen |
| 5.5 | Paper-Zyklus mit echten Read-only-Live-Daten über CLI starten, unterbrochene Datenfeeds und Neustarts testen | 5.4 | Mehrere vollständige Zyklen: Daten -> Signal -> Risiko -> virtuelle Fills -> Ledger; Ausfall, doppelte Events und Kill-Switch geprüft |

Modellwissen kann historische Backtests durch später erlernte Fakten verzerren. Für quantitative Baselines deterministische Signale nutzen und LLM-Ergebnisse ausdrücklich auf diesen möglichen Bias prüfen. Paper-Fills sind ebenfalls Modellannahmen, keine nachgewiesene Live-Ausführbarkeit.

### Phase 6: 24/7-Betrieb und separat freigegebenes Live-Trading

| Schritt | Umsetzung und Wiederverwendung | Abhängigkeit | Konkrete Abnahme |
| --- | --- | --- | --- |
| 6.1 | Scheduler mit Sperre pro Portfolio, sauberem Shutdown und Restart-/Recovery-Verhalten | 5.5 | Überlappende Jobs handeln nicht doppelt; Crash zwischen Reservierung/Dispatch/Antwort ist wiederherstellbar |
| 6.2 | Venue-Adapter, Idempotenz und Statusabgleich implementieren; bestehende Jupiter-Reads/Order-Funktion kapseln | 6.1 | Timeout nach Sendung wird durch Abgleich aufgelöst, nicht blind mit neuem Auftrag wiederholt; Teil-/Fehlfills und abgelaufene Quotes getestet |
| 6.3 | Signiergrenze härten: genehmigte Transaktion dekompilieren/prüfen, Empfänger, Mints, Betrag, Programme, Gebühren, minOut, Signierer und Frist abgleichen | 6.2 | Manipulierte/zusätzliche Instruktionen und fremde Transaktionen blockiert; zusätzliche Signaturen erhalten oder vorab explizit ablehnen; Netzwerk-Simulation vor Versand |
| 6.4 | Monitoring, Alerts und zunächst lesendes Dashboard; Metriken für Datenalter, Limits, Equity, offene Orders und Abgleichfehler | 6.1 | Simulierte Ausfälle lösen definierte Alerts aus; keine Schlüssel/Signieraktionen im Dashboard; Status mit Ledger konsistent |
| 6.5 | Ausführbare Live-Freigabe ergänzen: standardmäßig deaktiviert, separate begrenzte Wallet, explizit gespeicherte Betreiberfreigabe | 6.3, 6.4 und alle Live-Gates | Fehlende/abgelaufene Freigabe, falsche Wallet/Venue oder Kapitalgrenze verhindern jeden Versand; Reaktivierung nur explizit |

## Freigabekriterien für echtes Kapital

Vor irgendeinem echten Trade müssen alle folgenden Nachweise vorliegen:

- Passende Unit-, Provider-Contract-, Integrations-, Risiko- und Recovery-Tests bestehen auf den festgelegten Paketversionen.
- Backtest und Paper-Trading sind mit offengelegten Kosten-/Fill-Annahmen ausgewertet; Ergebnisse werden gegen Buy-and-Hold verglichen.
- Vor der Evaluation werden Beobachtungsdauer, Mindestzahl unterschiedlicher Handelssituationen, Verlustgrenzen und akzeptable Betriebsfehler festgelegt. Keine nachträgliche Auswahl erfolgreicher Zeitfenster und keine pauschale Profitabilitätsgarantie.
- Kill-Switch, doppelte Events, Timeout nach Versand, stale Quotes, problematische Token und Neustart unter offenen Orders sind nachweislich behandelt.
- Separater Wallet-/Signierzugang mit begrenztem Kapital; privater Schlüssel bleibt außerhalb des LLM-Kontexts. Proxy-API-Secrets sind kein Ersatz für lokale Signier-/Schlüsselverwaltung.
- Live-Modus ist standardmäßig aus. Die vorliegende Beauftragung erlaubt Analyse und Planung; sie ist keine Freigabe für Echtgeldhandel.

## Unmittelbar nächste Arbeit

Die Schritte 1.1–1.6 und 2.1–2.2 sind implementiert. Als Nächstes folgt 2.3: eine geeignete Solana-OHLCV-Quelle anhand dokumentierter Abdeckung, Einheiten, Zeitstempel, Pagination und Zugriffsbedingungen auswählen und anbinden. Authentifizierte Live-Contract-Tests und eine vertrauenswürdige Jupiter-Blockzeitauflösung bleiben zusätzlich offen. Die ersten PRs sollen keine Broker-Ausführung anbinden und keine vermeintliche Profitabilität demonstrieren, sondern reproduzierbare Installation, korrekten Agentenlebenszyklus und einen belegbar kontrollierten Research-Ablauf liefern.
