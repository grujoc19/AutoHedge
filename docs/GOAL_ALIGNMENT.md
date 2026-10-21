# Zielabgleich und verbindlicher Arbeitsstand

Stand: **21. Oktober 2026**, Basisabgleich gegen Code-Commit `68f97dc` auf
`feat/stabilize-research-core`, anschließend bis **G1.3** fortgeschrieben.
Die [erneut bereitgestellte Zieldefinition](PROJECT_GOAL.md)
ist die fachliche Grundlage. Sie ist bis auf normalisierte Zeilenenden im Original
gespeichert. Dieser Abgleich beschreibt den Ist-Stand und die daraus folgenden
Arbeitspakete; [ENGINE_ARCHITECTURE.md](ENGINE_ARCHITECTURE.md) beschreibt das Zielbild.
Die früheren Review-Befunde und Schritt-IDs bleiben im
[Entwicklungsplan](../DEVELOPMENT_PLAN.md) als Legacy-Referenz erhalten.

**Ergebnis:** Ein getesteter Daten-/Research-Kern ist vorhanden und wird weiterverwendet.
Ein autonomes Trading-System ist noch nicht implementiert. Die bisherige
Stabilisierung ist abgeschlossen, die in der neuen Definition zusätzlich verlangte
KI-Provider-Abstraktion der Foundation jedoch noch nicht vollständig umgesetzt.
G1.1 liefert den SDK-unabhängigen Modellvertrag und eine validierte Aufrufgrenze,
G1.2 sechs unveränderliche Rollenprofile mit gezielter Credential-Prüfung.
G1.3 verwendet beide jetzt im Swarms/OpenAI-Research: fünf validierte Calls,
expliziter Exa-Abruf ohne SDK-Tools und promptfreie Stufen-Audits. Andere aktive
Backends werden vor Agentenkonstruktion abgewiesen. Als Nächstes folgt G1.4, Ollama.

## 1. Was bereits passt und erhalten bleibt

„Implementiert“ bedeutet hier im aktiven Paket vorhanden und lokal getestet.
Es bedeutet weder live gegen einen Anbieter bestätigt noch als profitable
Strategie validiert. Prototypen unter `experimental/` zählen nicht als fertige Module.

| Anforderung | Tatsächlicher Stand / Beleg | Weiterverwendung und Grenze |
| --- | --- | --- |
| Bestehenden Fork stabilisieren statt neu schreiben | Frische Swarms-Teams pro Lauf, expliziter Orchestrator, getrennte Reports; [main.py](../autohedge/main.py), [workers.py](../autohedge/workers.py), [Orchestrierung](../autohedge/orchestration.py) | Funktionierenden Forschungsablauf hinter kompatibler Fassade erhalten; ereignisgesteuerten Ablauf separat einführen |
| Reproduzierbare Installation, modulare Struktur und Tests | [pyproject.toml](../pyproject.toml), [uv.lock](../uv.lock), [CI](../.github/workflows/ci.yml), Unit-/Runtime-/Importtests | Kein vollständiger Rewrite und zunächst ein modularer Python-Prozess; CI-Matrix ist konfiguriert, ihre Remote-Ergebnisse hier nicht bestätigt |
| Zentrale API-Konfiguration, sichere Logs und Fehler | [Settings](../autohedge/config.py), [Logging](../autohedge/logging.py), [HTTP-Transport](../autohedge/providers.py) | Secrets redigieren, Timeouts und begrenzte Read-Retries übernehmen; neue Daten-/Modellanbieter ergänzen |
| Providerneutraler Modellvertrag (G1.1) | [autohedge.ai](../autohedge/ai/__init__.py): unveränderliche Requests/Replies, korrelierte Ergebnisse, sichere Fehler, bekannte/teilweise/unbekannte Usage; [Details](MODEL_CALLS.md) | In G1.3 für OpenAI angebunden; weitere Modelladapter fehlen |
| Rollenprofile und Schlüsselwahl (G1.2) | [Konfiguration](MODEL_CONFIGURATION.md) für alle sechs Rollen mit Provider/Modell, Limits, geerbtem Timeout und alten Modellvariablen | OpenAI-Profile funktionieren im Research; lokale Konfiguration braucht keinen OpenAI-Key, ihr Adapter fehlt noch. Keine ignorierten Profile oder automatische Provider-Fallbacks |
| Swarms/OpenAI-Brücke (G1.3) | [Transport und Runner](OPENAI_RESEARCH.md) sowie [ModelCallAudit](../autohedge/ai/audit.py) | Fünf validierte Modellaufrufe mit Stufen-Audit; Exa lesend vor Sentiment, Snapshot ohne zusätzliche Suche; lokale/weitere Cloud-Backends offen |
| Strikte normalisierte Daten | [Datenverträge](../autohedge/data/contracts.py), [Qualitätsprüfung](../autohedge/data/quality.py) | Instrument-/Währungsidentität, UTC-Zeiten, explizite Volumeneinheiten, Lücken-/Altersprüfung übernehmen; Orderbuch-, Trade-, Pool- und On-Chain-Verträge fehlen |
| Echte lesende Datenadapter | [Jupiter](../autohedge/data/jupiter.py), [Exa](../autohedge/data/exa.py), [CoinGecko](../autohedge/data/coingecko.py) | Adapter mit Offline-HTTP-Fixtures vorhanden; keine authentifizierten Live-Contract-Tests. Jupiter braucht zusätzlich einen vertrauenswürdigen Blockzeitresolver; CoinGecko Pro passende Berechtigungen |
| Historische Candles und zeitgebundene Wiederholung | Pagination, unveränderlicher dateibasierter [HistoryStore](../autohedge/data/history.py) | Historien und Abrufprovenienz erhalten; kein Ersatz für Ereignisdatenbank, Portfolio-Ledger oder kausalen Strategie-Backtest |
| RSI, MACD und Volatilität in Python | Deterministische Decimal-[Indikatoren](../autohedge/indicators.py) mit Warm-up und Versionierung | Quant-Bausteine und Baselines weiterverwenden; noch kein Anomaly-Modell, Handelssignal oder Nachweis von Vorhersagekraft |
| Gleiche Faktenbasis für Rollen | [ResearchSnapshot und EvidenceReference](../autohedge/snapshots.py) mit neu berechneten Indikatoren, Qualitätsregeln und Inhalts-IDs | Snapshot und kompakter Agentenkontext erhalten. Inhalts-Hashes beweisen weder Echtheit noch Neuigkeit oder Unabhängigkeit verschiedener Quellen |
| Begrenzter Research-Ablauf ohne Handel | [ResearchReport](../autohedge/domain.py) erzwingt `execution_enabled=false`; begrenzte Aufrufe, Kontext und Antwortlängen | Bestehende Fehlerabbrüche erhalten. Das ist noch kein abgesicherter Live-Modus für manuell aufrufbare Legacy-Swap-Helfer |

## 2. Was gegenüber dem bisherigen Plan angepasst werden muss

| Bereich | Bisheriger Stand / bisherige Annahme | Verbindliche Anpassung |
| --- | --- | --- |
| Rollen | Vier Agentenobjekte in fünf festen Stufen: Director-These → Quant → Sentiment → Risk → Director-Synthese; nur Textausgaben | Sechs explizite Rollen: News Intelligence, Market Anomaly, On-Chain Intelligence, **Quant**, Trading Director und beratender Risk Manager. Quant bleibt eigenständig; Anomaly Detection verwendet seine Features, ersetzt ihn aber nicht |
| Ablauf | Jede Stufe läuft immer; spätere Rollen sehen die Director-These und alle Vorgänger | Ereignisrouter mit versionierter Strategie-Policy bestimmt erforderliche Rollen. Unabhängige Befunde zuerst, Director danach; fehlende Pflichtbefunde verhindern ein Handelssignal. Nicht jedes Ereignis benötigt alle sechs Modellaufrufe |
| Modellausgaben | `StageResult.content` ist begrenzter Freitext, trotz Pydantic-Report keine geprüfte Fachausgabe | Gemeinsame JSON-Verträge für Fakten, Quellen, Zeitstempel, Bewertung, Unsicherheit und Versionen; unzulässige oder unbelegte Inhalte werden verworfen |
| Signale | [Action/Signal](../autohedge/domain.py) kennen BUY/SELL/HOLD, Instrument, Erstellzeit, Begründung und Konfidenz; sie sind nicht in den Agentenlauf integriert | **NO TRADE** ergänzen; Evidenz, Ablaufzeit, Horizont und Invalidierungsbedingungen verbindlich machen. HOLD = Bestand bewusst halten; NO TRADE = keine ausreichend belegte neue Gelegenheit / keine Analysefreigabe. Beide erzeugen keine neue Order. Aktuelle Fehler werfen weiterhin Fehler, sie erzeugen noch kein typisiertes NO TRADE |
| Datenquellen und Reihenfolge | Schwerpunkt bisher Solana-Historie, Jupiter und Exa; weitere Feeds nur allgemein geplant | Nach Foundation zuerst News Collector, **Binance WebSocket und DEX Screener**. Coinbase, GeckoTerminal, Originalquellen und Solana-/Helius-Streams in getrennten Schritten ergänzen; bestehende Solana-Historie weiterverwenden |
| Asset-/Venue-Umfang | Solana-Spot als erster möglicher Ausführungsbereich | Intelligence darf CEX-/Makro-Daten einbeziehen. CEX-Symbol, Solana-Mint, Pool und Quote-Währung explizit zuordnen; Binance-/Coinbase-Datenfeeds autorisieren keine CEX-Trades. Keine stillschweigende Gleichsetzung von USD und Stablecoins |
| Modellrouting | Swarms/OpenAI verwendet G1.1/G1.2 mit je Rolle unterschiedlichen OpenAI-Modellen; Legacy-Variablen bleiben Fallbacks | Geprüfte Ollama-/weitere Cloud-Adapter ergänzen. Konfiguration oder transitive LiteLLM-Abhängigkeit allein sind keine laufenden Backends |
| Recherche und Kosten | Legacy-Research aktiviert Exa standardmäßig; Snapshot-Teams deaktivieren zusätzliche Suche bereits. Der Collector akzeptiert jeden passenden NewsProvider, verlangt standardmäßig News | RSS/Originalquellen werden die Grundversorgung, **Exa bleibt optionale Vertiefung**. Optionale Exa-Suche bedeutet nicht, dass eine News-Strategie ohne erforderliche News handeln darf. Bedarf und Frische je Strategie festlegen; keine stillschweigende Lockerung der Snapshot-Prüfungen |
| Aufrufgrenzen versus Kostenkontrolle | Modell-/Tool-Retries und Turns begrenzt; Laufdeadline vor/nach synchronen Aufrufen geprüft | Token-/Kostenverbrauch pro Rolle und Lauf, persistente Tages-/Monatsbudgets, sichere Analyse-Caches und begrenzte Eskalation fehlen. Deadline ist keine harte Unterbrechung eines hängenden Custom-Agenten |
| Speicherung | Historien als unveränderliche JSON-Dateien; JSON-Report nur bei konfiguriertem Ausgabepfad | Persistente Datenbank für Quellen, versionierte Ereignisse, Analysen, Budgets und Audit; später Portfolio/Orders/Fills. Vorhandene Historien referenzieren, keine parallele Neuentwicklung derselben Historienablage |
| Phasen | Bisher sechs Phasen, Routing erst unter Legacy 3.3 | Neue sieben Phasen gelten. Provider-Abstraktion gehört jetzt in Foundation; Ereignisdaten früh speichern, Live-Ausführung bleibt ganz am Ende |

## 3. Datenquellen: vorhanden, teilweise vorhanden, offen

Zugriff, Tarif, Nutzungsrechte und reale Payloads der neu genannten Quellen sind
noch nicht geprüft. Die Tabelle ist ein Implementierungsauftrag, keine bestätigte
Verfügbarkeit. Jede Quelle braucht einen eigenen lesenden Adapter und Tests;
bei The Block nur anbinden, wenn Feed und Nutzungsbedingungen geeignet sind.

| Quelle / Daten | Stand | Fehlender Anschluss |
| --- | --- | --- |
| Jupiter | Teilweise: Token-Metadaten und USD-Preise normalisiert | Produktiver Blockzeitresolver und Live-Vertragsprüfung; keine Historie oder Orderbuch-/Pool-Überwachung durch den Preisadapter |
| CoinGecko Pro Onchain | Teilweise: Solana-Token-OHLCV, Pagination, Archiv/Replay | Tarif-/Live-Prüfung. Der vorhandene Host ist nicht bereits eine direkte GeckoTerminal-Integration |
| Exa | Teilweise: datierte News-Suche und Research-Tool | Als optionale Recherche behalten; ersetzt weder kontinuierlichen News Collector noch Originalquellenprüfung |
| CoinDesk RSS / BlockBeats Newsflash RSS | Fehlt | Feeds, bedingte Abrufe, Zeitangaben, Herkunft, Limits, Persistenz und Deduplizierung |
| The Block / offizielle Projekt-Veröffentlichungen | Fehlt | Geeignete zulässige Quellen auswählen; Original-/Änderungsversion und Asset-Zuordnung speichern |
| Federal Reserve / SEC | Fehlt | Datierte offizielle Ereignisse erfassen; Makro-/Regulierungsereignisse dürfen mehrere Assets betreffen |
| Social Media | Fehlt | Eigene zulässige Provider, Quellenauthentizität und First-seen-/Versionsdaten; nicht aus Exa automatisch ableiten |
| Binance / Coinbase WebSockets | Fehlt im aktiven Paket | Trades/Book mit Sequenz-/Lückenprüfung, Snapshot-Recovery, Heartbeat, begrenztem Reconnect und Venue-/Symbolmapping; alter experimenteller BTC-Monitor zählt nicht |
| DEX Screener / GeckoTerminal | Fehlt als direkter Adapter | Pool-/Pair-/Chain-Identität, Volumen-/Liquiditätseinheiten, Preisbasis, Snapshots und Historisierung |
| Helius API, Webhooks, Streaming / Solana RPC, WebSockets | Fehlt | Authentisierung soweit nötig, Deduplizierung, Slots/Finalität/Reorg, echte Swaps versus Transfers/LP, Konzentration und Wallet-Cluster mit Unsicherheit |
| Bitquery | Optional später | Erst ergänzen, wenn ein belegter Bedarf und geprüfter Zugang bestehen |

Nachrichten haben bislang nur begrenzte Duplikatprüfung innerhalb eines Bundles
(Quell-ID/URL). Es fehlen ein persistenter First-seen-Verlauf, Syndizierungs- und
semantische Ereignis-Deduplizierung, Änderungsverläufe, Relevanz-/Asset-/Impact-
Klassifikation und Vergleich mit früherem Wissen. `EvidenceReference.known_at`
ist kein Ersatz für diesen Ereignisspeicher. Exa-Publikationszeiten sind geschätzt.

## 4. Weitere fehlende Kernfunktionen

| Bereich | Was noch fehlt / erforderliche Grenze |
| --- | --- |
| Anomaly Detection | Versionierte Preis-/Volumen-/Volatilitätsbaselines, aggressive Käufe/Verkäufe, Cross-Venue-Abweichungen, Book-Imbalance und Pool-/Liquiditätsrückgänge; statistischer Score zuerst, ML erst mit zeitlich getrenntem Nachweis, LLM interpretiert nur |
| Sechs Intelligence-Rollen | Strukturierte Befunde und Strategieregeln für Rollenbedarf, Korrelation/Unabhängigkeit, Quellenkonflikte und Manipulation; News-/On-Chain-/Anomaly-Agenten fehlen, Quant/Risk/Director sind bisher Textrollen |
| Signal Engine | Validierter Director-Vorschlag mit allen Pflichtfeldern, expliziter Abstention und Ablaufprüfung; kein erzwungener Trade, keine agentenbestimmte Ordermenge |
| Deterministische Risk Engine | Positions-, Tagesverlust-, Portfolio-/Konzentrationslimits, Liquidität, Slippage, Frische, Solana-Token-/Deployer-/Verkaufsroutenprüfung und persistenter Kill-Switch; kein Agent darf Freigabe oder Limitlockerung erzeugen |
| Portfolio und Ausführung | Dauerhafter Bestand, atomare Reservierung, Idempotenz gegen doppelte Orders, Order-/Fill-Ledger und Reconciliation. Zuerst ausschließlich PaperBroker; nach Signalerzeugung arbeiten Risiko und Dispatch ohne weitere LLM-Aufrufe |
| Legacy-Swap-Helfer | [ultra_tools.py](../autohedge/tools/ultra_tools.py) und [Registry](../autohedge/tools/tools_registry.py) enthalten manuell aufrufbare Signier-/Sendefunktionen ohne zentrale Risk-Freigabe; der Signaturaufbau verwirft weitere Signaturen. Im Standard-Agentenlauf nicht angebunden. Vor einer zukünftigen Ausführungsintegration sperren/ersetzen und negativ testen |
| Strategievalidierung | Kausale historische Markt- **und Ereignis**-Wiedergabe, realistische Gebühren/Spread/Slippage/Latenz/Fills, Out-of-sample und Walk-forward, Baseline-/Buy-and-Hold-Vergleich, Daten-/Modell-/Promptversionen. Vorhandenes Candle-Replay ist nur eine Grundlage; ein heutiger Backfill war gestern nicht bekannt, historisches LLM-Wissen kann ebenfalls leaken |
| Kennzahlen | Nettorendite, maximaler Drawdown, Profit Factor, Trefferquote, Erwartungswert je Trade, Signalqualität je Horizont und Ereignis→Signal→Ausführung-Latenz; keine belastbare Implementierung im aktiven Kern |
| Betrieb | Kontinuierlicher Collector/Router, bounded Queues, Recovery/Shutdown, Docker-/Compose-Deployment für Linux-VPS/Proxmox, Datenbankmigrationen/Backups, Feed-Reconnect, Überwachung von Datenlücken/Alter, Modell- und Kostenmetriken |
| Dashboard / Benachrichtigungen / Audit | Lesende Sicht auf Marktaktivitäten, Signale, Positionen und Kosten; definierte Ereignis-/Fehleralerts; durchgehender dauerhafter Audit-Trail für Entscheidungen und Fehler fehlt. run_id/UTC/Snapshot-IDs und optionale Reportdateien sind eine Grundlage, kein vollständiger Audit-Trail |
| Echtgeldmodus | Technisch abgesicherte Aktivierung, separate begrenzte Wallet, ausdrückliche gespeicherte Betreiberfreigabe, Transaktionsvalidierung und Versand-/Recovery-Tests fehlen; keine Live-Freigabe durch diesen Auftrag |

## 5. Neue Phasen und Zuordnung der bisherigen Arbeit

Die Präfixe **G1–G7** bezeichnen ab jetzt diese Zielphasen. Unpräfixierte IDs
wie `2.6` in älteren Texten sind **Legacy-IDs**, keine neue Phasennummerierung.

| Zielphase | Status heute | Bezug zum bisherigen Plan |
| --- | --- | --- |
| G1 Foundation | Stabilisierung, Modellvertrag, Rollenprofile und Swarms/OpenAI-Brücke vorhanden; weitere Provider offen | Legacy 1.1–1.6 erhalten; Grundlagen aus 3.1a/3.3 vorziehen |
| G2 Data Collection | Verträge, drei Read-Adapter, Historie und Snapshots vorhanden; kontinuierliche Collector/DB/WS fehlen | Legacy 2.1–2.5 erhalten; 2.6/2.8 konkretisieren und aufteilen |
| G3 AI Market Intelligence | Indikatoren vorhanden; lokale Analyse, News-Klassifikation, Anomaly Detection und Korrelation fehlen | Legacy 3.1a–3.1c und 3.3 |
| G4 On-Chain Intelligence | Fehlt | Legacy 2.7 und 3.1d |
| G5 Trading Intelligence | Research-Sequenz vorhanden; Ereignisrouting, sechs strukturierte Rollen und Signal Engine fehlen | Legacy 3.2/3.4, neue Quant-Abgrenzung und NO TRADE |
| G6 Risk Management & Paper-Trading | Nur Portfolio-/Signal-Basisschemas; integrierte Engine, Broker und Evaluation fehlen | Legacy 4.1–4.5 und 5.1–5.5 |
| G7 Production Readiness | Fehlt | Legacy 6.1–6.5 plus Docker, DB-Betrieb und Kosten-/Feed-Monitoring |

## 6. Priorisierte, kleine Arbeitspakete

Reihenfolge innerhalb einer Phase ist die Arbeitsreihenfolge. **Nächster
Implementierungsschritt ist G1.4**, kein Neustart der bereits getesteten Foundation.
Die späteren Gruppen sind ein Backlog; sie werden nicht gleichzeitig begonnen.
Jeder Schritt erhält passende Unit-/Integrationstests und einen kleinen
reviewbaren Commit/Feature-PR. Kein automatischer Merge, keine Live-Aktivierung.

| ID / Priorität | Geplanter Schritt | Abhängigkeit und konkrete Abnahme |
| --- | --- | --- |
| G1.1 / umgesetzt | Providerneutraler Modellaufruf-/Ergebnis-/Fehlervertrag samt Test-Fake; Usage, Modellidentität und Fristen erfasst | [Modellverträge](MODEL_CALLS.md); Timeout, unbekannte Usage, falsche IDs, ungültige/abgeschnittene/verweigerte Antworten und keine Retries geprüft; in G1.3 angebunden |
| G1.2 / umgesetzt | Provider-/Modellkonfiguration für alle sechs Rollen, alte Modellvariablen und optionale Provider-Schlüssel | [Rollenprofile](MODEL_CONFIGURATION.md): Präzedenz/Defaults, unabhängige Limits, Credential-Freiheit/Redaction und ungültige Konfiguration geprüft; OpenAI-Anbindung umgesetzt |
| G1.3 / umgesetzt | [Swarms/OpenAI-Pfad](OPENAI_RESEARCH.md) an Modellverträge, Profile und promptfreies Audit angebunden; Exa außerhalb des LLMs lesen | Fünf Calls, keine Modell-Retries/Tools; vollständiger Offline-HTTP-/SDK-Lauf und Fehlerabbrüche geprüft. Native Modell-API ungeprüft; keine Handelsfunktion |
| G1.4 / P1 | Ollama-Adapter mit denselben Verträgen ergänzen | G1.3; gemockter lokaler Server prüft gültige Ausgabe, Timeout, Ausfall und kaputtes JSON; kein automatischer unbudgetierter Cloud-Fallback |
| G1.5a–c / P2 | Anthropic, Gemini und DeepSeek jeweils in einem eigenen Schritt über LiteLLM ergänzen | G1.3; je Provider Konfiguration, Antwort-/Usage-Normalisierung, Auth-/Rate-Limit-/Schemafehler offline prüfen; echte Modellqualität separat evaluieren |
| G2.1 / P1 | Persistenten Quellen-/Ereignisvertrag und minimale Datenbank mit Migration einführen; SQLite als erster Ein-Prozess-Vorschlag | G1 abgeschlossen; Raw-Referenz, event_time/received_at/first_seen/known_at, Version und Quelle bleiben über Neustart erhalten; noch kein Broker-Ledger |
| G2.2a–b / P1 | RSS-Collector zunächst CoinDesk, danach BlockBeats mit je eigenen Fixtures | G2.1; Wiederabruf/Duplikat/fehlendes Datum/Edit/defekter Feed getestet. Keine erfundene Publikationszeit, Exa nicht erforderlich |
| G2.3 / P1 | News-Deduplizierung und Revisionen über Abrufe hinweg | G2.2; exakte Duplikate und Edits deterministisch; unklare semantische Gleichheit explizit. Repost ist kein unabhängiger Beleg |
| G2.4 / P1 | Binance-Trade-Stream für freigegebene Symbole mit Persistenz/Reconnect | G2.1; Playback prüft Duplikate, Trennung und Lücken. Book erst separat mit Snapshot-/Delta-Abgleich, nicht aus Trades erfinden |
| G2.5 / P1 | DEX-Screener-Pool-Snapshots normalisieren und speichern | G2.1; Chain/Pool/Mints/Währung und Einheiten geprüft; Fehler oder Staleness liefern keine Ersatzwerte |
| G2.6 / P2 | Weitere Quellen einzeln: Fed/SEC, Projekte, geeigneter The-Block-Feed, Coinbase-Book, GeckoTerminal, Social | G2.2–G2.5; pro Adapter Rechte/Zugang/Payload dokumentieren und Ausfälle testen. CEX-Book-Sequenzen und AMM-Liquidität bleiben getrennte Datenarten |
| G2.7 / P2 | Jupiter-Blockzeitauflösung und authentifizierte Read-only-Vertragsprüfungen | Bestehende Adapter; echte Zeit-/Einheiten-/Fenster-/Zugangsannahmen prüfen. Fehlender Zugang bleibt expliziter Blocker und wird nicht wegkonfiguriert |
| G3.1 / P1 | Persistente Budgetreservierung und Usage-/Kostenbuchung je Rolle, Tag und Monat | G1, G2.1; parallele Aufrufe/Neustarts überschreiten das Budget nicht, unbekannte Preise/Usage nach definierter Policy behandeln. Vor kontinuierlichen Modellaufrufen erforderlich |
| G3.2 / P1 | Analyse-Cache und begrenzte Eskalationsentscheidung für besonders relevante oder unklare Meldungen | G3.1; Schlüssel enthalten Eingabe-, Modell-, Prompt- und Schemaversion; alte/fremde Analysen nicht als neue Evidenz verwenden; Budget/Ausfall führt zu Abstention |
| G3.3 / P1 | AgentFinding-/News-Klassifikation mit Evidenz, Asset-Zuordnung und Neuigkeitsbewertung | G2.3, G3.2; gültiges JSON, belegte Referenzen und Unsicherheit, alte/ungeklärte News und Prompt-Injection getestet |
| G3.4 / P1 | Ersten statistischen OHLCV-Anomaly-Detektor implementieren | Bestehende Indikatoren; nur früher bekannte Baseline, reproduzierbarer Score, Warm-up und Datenlücken getestet. Quant interpretiert eigene Kennzahlen, Anomaly interpretiert den Score |
| G3.5 / P2 | Book-/Pool-/Cross-Venue-Detektoren und lokale ML jeweils getrennt ergänzen | G2.4–G2.6, G3.4; Einheiten/Zeitsynchronität und inkrementeller Zusatznutzen out-of-sample prüfen, keine erfundenen Imbalances |
| G4.1–3 / P1 | Zuerst RPC/Helius-Ereignisse, dann Swap-/Transfer-/LP-Klassifikation, danach Konzentration/Wallet-Muster | G2-Verträge; jeder Schritt separat. Doppelte/umgeordnete Webhooks, Finalität/Reorg, große interne Transfers und unsichere Wallet-Cluster prüfen |
| G5.1 / P1 | Ereignisrouter und Pflichtrollen-Policy neben der bisherigen Research-Sequenz | G3/G4; Ereignis-ID, Policy-Version und gemeinsamer Snapshot bestimmen Jobs. Nicht betroffene Rollen werden übersprungen, fehlende Pflichtrolle verhindert Entscheidung |
| G5.2 / P1 | Quant und beratenden Risk Manager in strukturierte Befunde migrieren | G5.1; sechs Rollen bleiben unterscheidbar, keine LLM-Positionsgröße, keinerlei Risk-Freigaberecht |
| G5.3 / P1 | Signalvertrag erweitern und Director/Signal Engine anbinden | G5.2; BUY/SELL/HOLD/NO TRADE, Referenzen, Frist/Horizont/Invalidierung, widersprüchliche/manipulierte Evidenz und fehlende Daten geprüft |
| G6.1–3 / P0 vor Ausführung | Ledger/Reservierung, dann reine Risk-Regeln, dann Token-/Liquiditätsprüfung und Kill-Switch | G5; je eigener Schritt mit Grenzwert-, Konkurrenz-, Neustart- und Umgehungstests; Legacy-Sendepfade dürfen keine Hintertür bleiben |
| G6.4–6 / P1 | PaperBroker, kausaler Backtest und Kennzahlen/Evaluation jeweils getrennt | G6.1–3; Kapital-/Token-Erhaltung, realistische Kosten/Fills, kein Look-ahead; alle Zielkennzahlen, Baselines und Walk-forward reproduzierbar |
| G6.7 / P1 | Read-only-Live-Daten mit Paper-Zyklen und Recovery verbinden | G6.4–6; Feed-Ausfall, doppelte Ereignisse, abgelaufene Signale, Kill-Switch und Neustart mit offenen virtuellen Orders geprüft |
| G7.1–3 / P2 | Docker/Betrieb/DB-Backups, dann Metriken/Alerts, dann lesendes Dashboard | G6; je eigener Schritt. Healthchecks, Feed-/Queue-/Modelllatenz, Positionen/Kosten und Wiederherstellung auf Linux prüfen |
| G7.4 / P0 vor Live | Erst nach bestandenem Paper-/Evaluations-Gate separat abgesicherte Live-Anbindung | G7.1–3; explizite Freigabe, begrenzte separate Wallet, Default aus, Transaktions-/Idempotenz-/Recovery-Prüfung; bis dahin kein Signieren/Senden |

## 7. Nachweis und Pflege

Für diesen Abgleich wurden Paket, Konfiguration, Agentenlauf, Datenadapter,
Persistenz, Tests, CI und die bisherigen Architektur-/Planungsdokumente geprüft.
Der ursprüngliche Review von PR #1 bleibt im Entwicklungsplan erhalten; der PR
ist bereits als `9027880` gemerged und wird nicht nochmals als offene Arbeit geführt.

Basisabgleich am 21. Oktober 2026: 139 Unit-, 5 Runtime-Integrations- und 1 Importtest
bestanden. Nach G1.1 waren es 160 Unit-/6 Integrationstests plus 1 Importtest.
Nach G1.2 waren es 178 Unit-/7 Integrationstests plus 1 Importtest.
Nach G1.3 auf Python 3.12 erneut ausgeführt:

- **198 Unit-Tests, 9 Runtime-Integrationstests und 1 Importtest bestanden.**
- Runtime-Tests verwenden installierte Swarms-Pakete mit Fake-Modellantworten und
  gesperrten Netzwerkverbindungen. Providerdaten stammen aus HTTP-Fixtures.
- Keine echten Modell-/Marktdatenaufrufe, Wallet-Zugriffe oder Orders; die
  Cloud-Umgebung meldet aktuell keine konfigurierten Secrets.

G1.1–G1.3 erweitern den Research-Kern um Modellverträge, Profile und einen OpenAI-
Transport, keine Handelsfunktion. Die Tests belegen keine der hier als fehlend markierten Funktionen. Nach jedem
Implementierungsschritt Status, Belege und Abnahmen dieses Dokuments aktualisieren;
neue Wünsche in der Zieldefinition beziehungsweise einem begründeten Änderungsstand
festhalten. Priorität bleibt: Intelligence und messbare Qualität aufbauen,
deterministische Ausführungsgrenzen technisch erzwingen, Paper vor Live.
