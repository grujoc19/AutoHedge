# AutoHedge – Entwicklungsplan für ein KI-gestütztes Trading-System

## 1. Projektziel

Entwickle meinen bestehenden AutoHedge-Fork (`grujoc19/AutoHedge`) zu einem modularen, autonomen KI-Trading-System weiter.

**Kernidee:** Wir wollen nicht versuchen, professionelle Sniper-Bots durch reine Ausführungsgeschwindigkeit zu schlagen. Stattdessen soll AutoHedge durch intelligente Marktbeobachtung, frühzeitige Anomalieerkennung und die Kombination verschiedener Informationsquellen interessante Trading-Gelegenheiten identifizieren.

Die KI übernimmt hauptsächlich Marktverständnis, Mustererkennung und strategische Bewertungen. Zeitkritische Aufgaben wie Risikoprüfung und Orderausführung müssen unabhängig von LLM-Antwortzeiten funktionieren.

**Grundprinzip:** AI for Intelligence, deterministic code for execution.

## 2. Bestehendes Projekt als Grundlage

Analysiere zuerst den vorhandenen Code, die Architektur und den bereits angelegten Draft-PR #1.

- Bestehende, funktionierende Komponenten möglichst weiterverwenden.
- Bekannte Fehler beheben und fehlende Funktionen ergänzen.
- Saubere Trennung zwischen Datenbeschaffung, KI-Agenten, Trading-Strategien, Risikomanagement und Orderausführung.
- Konfigurierbare Schnittstellen zwischen den Modulen.
- Logging, Fehlerbehandlung, automatisierte Tests und Monitoring integrieren.

Bitte keine vollständige Neuentwicklung ohne technische Begründung.

## 3. Market Intelligence Layer

Entwickle eine kontinuierliche Marktüberwachung mit mehreren unabhängigen Datenquellen.

### A. News Intelligence

Datenquellen:
- CoinDesk RSS
- BlockBeats Newsflash RSS
- The Block, sofern Feed und Nutzungsbedingungen geeignet sind
- Offizielle Veröffentlichungen von Krypto-Projekten
- Federal Reserve und SEC
- Optional Exa für zusätzliche Recherchen

Funktionen:
- Nachrichten automatisch abrufen und speichern.
- Duplikate erkennen.
- Veröffentlichungs- und Empfangszeitpunkte erfassen.
- Nachrichten durch ein KI-Modell nach Relevanz, Sentiment, betroffenen Assets und potenziellen Auswirkungen klassifizieren.
- Neue Informationen mit früheren Ereignissen vergleichen.
- Bei besonders relevanten Nachrichten eine vertiefte Analyse auslösen.

### B. Market Anomaly Detection

Datenquellen:
- Binance WebSocket API
- Coinbase WebSocket API
- DEX Screener
- GeckoTerminal
- Jupiter API

Zu erkennende Auffälligkeiten:
- Ungewöhnliche Handelsvolumen.
- Aggressive Kauf- oder Verkaufsaktivität.
- Orderbuch-Ungleichgewichte.
- Plötzlicher Liquiditätsrückgang.
- Ungewöhnliche Volatilität.
- Abweichungen zwischen verschiedenen Börsen.
- Auffällige Veränderungen bei DEX-Trading-Pools.

Verwende für die schnelle Erkennung primär Python, statistische Verfahren und geeignete lokale Machine-Learning-Modelle. LLMs sollen Auffälligkeiten anschließend interpretieren und mit anderen Informationen verknüpfen.

### C. On-Chain Intelligence

Datenquellen:
- Helius API, Webhooks und Streaming
- Solana RPC/WebSockets
- Optional später Bitquery

Funktionen:
- Relevante Wallets beobachten.
- Große Tokenbewegungen erkennen.
- Auffällige Wallet-Akkumulation analysieren.
- Liquiditätsänderungen in Pools untersuchen.
- Deployer- und Token-Risiken bewerten.
- Zusammenhänge zwischen Wallets und Handelsaktivitäten identifizieren.

Wallet-Transfers, tatsächliche Swaps und Liquiditätsbewegungen müssen korrekt unterschieden werden.

## 4. Multi-Agent KI-Architektur

Erweitere die bestehende Swarms-Architektur um klar definierte Verantwortlichkeiten:

| Agent | Aufgabe |
|---|---|
| News Intelligence Agent | Nachrichten überwachen, einordnen und bewerten |
| Market Anomaly Agent | Ungewöhnliche Marktbewegungen untersuchen |
| On-Chain Intelligence Agent | Wallets, Token und Liquiditätsveränderungen bewerten |
| Quant Agent | Quantitative Kennzahlen und historische Muster analysieren |
| Trading Director | Erkenntnisse zusammenführen und Trading-Signale erstellen |
| Risk Manager | Risiken bewerten und Informationen für die regelbasierte Risk Engine bereitstellen |

Agenten sollen strukturierte JSON-Ausgaben mit Quellen, Zeitstempeln, Bewertungen und Unsicherheiten liefern.

Nicht jeder Agent muss bei jedem Ereignis ausgeführt werden. Stattdessen soll eine ereignisgesteuerte Orchestrierung entscheiden, welche Analysen notwendig sind.

## 5. Flexible KI-Provider und lokale Modelle

Implementiere eine providerunabhängige KI-Schnittstelle.

Unterstützung für:
- OpenAI
- Anthropic
- Google Gemini
- DeepSeek
- Lokale Modelle über Ollama
- Erweiterbarkeit über LiteLLM

Jeder Agent soll ein eigenes Modell erhalten können, konfigurierbar über Umgebungsvariablen oder Konfigurationsdateien.

**Kostenoptimierung:**
- Lokale Modelle für häufige Routineanalysen und News-Klassifizierung.
- Leistungsfähigere Cloud-Modelle für ausgewählte komplexe Fälle.
- Caching und Wiederverwendung vorhandener Analysen.
- Tokenverbrauch und Kosten pro Agent erfassen.
- Tages- und Monatsbudgets begrenzen.
- Keine unbegrenzten automatischen API-Retries.
- Optionale Webrecherche statt permanenter kostenpflichtiger Suchen.

Bei einem Modell- oder API-Ausfall muss das System kontrolliert reagieren, ohne automatisch riskante Handelsentscheidungen zu treffen.

## 6. Trading Signal Engine

Die Erkenntnisse aller Agenten sollen in einer zentralen Signal Engine zusammengeführt werden.

Mögliche Signale:
- BUY
- SELL
- HOLD
- NO TRADE

Jedes Signal benötigt:
- Betroffenes Asset.
- Relevante Daten und Ereignisse.
- Nachvollziehbare Begründung.
- Zeitpunkt und Gültigkeitsdauer.
- Unsicherheits- beziehungsweise Konfidenzbewertung.
- Vorgeschlagenen Zeithorizont.
- Bedingungen, unter denen das Signal ungültig wird.

Die Signal Engine soll keine unnötigen Handelsaktivitäten erzeugen. Wenn keine belastbare Gelegenheit vorliegt, ist NO TRADE das korrekte Ergebnis.

## 7. Deterministische Risk & Execution Engine

KI-Signale dürfen niemals direkt und ungeprüft Trades auslösen.

Implementiere eine unabhängige Risk Engine mit:
- Maximaler Positionsgröße.
- Maximalem Verlust pro Tag.
- Portfolio- und Konzentrationslimits.
- Liquiditätsprüfung.
- Slippage-Grenzen.
- Datenaktualitätsprüfung.
- Token-Sicherheitsprüfungen.
- Kill-Switch.
- Erkennung und Verhinderung doppelter Orders.

Die Execution Engine muss ohne zusätzliche LLM-Aufrufe arbeiten.

Zunächst ausschließlich Paper-Trading implementieren. Echte Handelsorders dürfen erst nach ausdrücklicher Aktivierung eines separat abgesicherten Live-Modus möglich sein.

## 8. Backtesting und Strategievalidierung

Entwickle eine belastbare Testumgebung, um zu prüfen, ob unsere KI-Signale tatsächlich einen Vorteil bieten.

Anforderungen:
- Historische Markt- und Ereignisdaten verwenden.
- Look-ahead Bias und Data Leakage vermeiden.
- Gebühren, Slippage und realistische Orderausführungen berücksichtigen.
- Out-of-Sample- und Walk-Forward-Tests.
- Vergleich mit einfachen regelbasierten Strategien und Buy-and-Hold.

Zu erfassende Kennzahlen:
- Nettorendite.
- Maximaler Drawdown.
- Profit Factor.
- Trefferquote.
- Erwarteter Ertrag pro Trade.
- Signalqualität über verschiedene Zeithorizonte.
- Zeit zwischen Ereignis, Signal und Orderausführung.

Eine profitable Strategie darf nicht einfach vorausgesetzt werden. Die Ergebnisse müssen reproduzierbar sein.

## 9. Betrieb und Monitoring

Das System soll später kontinuierlich auf einem Linux-VPS oder Proxmox-Server laufen können.

Einzuplanen:
- Docker-Unterstützung.
- Zentrale Konfiguration.
- Persistente Datenbank.
- Wiederverbindung bei WebSocket-Ausfällen.
- Erkennung fehlender oder veralteter Marktdaten.
- Dashboard für Marktaktivitäten, Signale, Positionen und Kosten.
- Benachrichtigungen bei wichtigen Ereignissen.
- Logs und Audit-Trail für sämtliche Entscheidungen.

## 10. Entwicklungsreihenfolge

Implementiere die Komponenten schrittweise und unabhängig testbar.

**Phase 1 – Foundation**

Bestehende Codebasis stabilisieren, Tests und modulare Architektur herstellen, KI-Provider abstrahieren.

**Phase 2 – Data Collection**

News Collector, Binance WebSocket und DEX Screener anbinden. Daten normalisieren und speichern.

**Phase 3 – AI Market Intelligence**

Lokale KI-Modelle, News-Analyse, Anomalieerkennung und Ereigniskorrelation implementieren.

**Phase 4 – On-Chain Intelligence**

Helius und Solana-Datenströme integrieren. Wallets, Swaps und Liquiditätsveränderungen analysieren.

**Phase 5 – Trading Intelligence**

Multi-Agent-Orchestrierung vervollständigen und strukturierte, nachvollziehbare Trading-Signale erzeugen.

**Phase 6 – Risk Management & Paper-Trading**

Deterministische Sicherheitsregeln, realistische Simulation und umfangreiche Strategievalidierung implementieren.

**Phase 7 – Production Readiness**

Monitoring, Dashboard, Deployment und gegebenenfalls abgesicherte Live-Trading-Anbindung entwickeln.

## 11. Arbeitsweise für Codex

Arbeite nicht alle Phasen gleichzeitig ab.

1. Analysiere den aktuellen Entwicklungsstand.
2. Identifiziere vorhandene und fehlende Komponenten.
3. Erstelle eine priorisierte Aufgabenliste.
4. Implementiere jeweils einen kleinen, abgeschlossenen Entwicklungsschritt.
5. Schreibe passende Unit- und Integrationstests.
6. Dokumentiere Änderungen und technische Entscheidungen.
7. Verwende Feature-Branches und Pull Requests.
8. Prüfe vor jedem Merge auf Regressionen und Sicherheitsprobleme.

**Beginne mit Phase 1.** Stelle zunächst die geplante Architektur, die wichtigsten technischen Entscheidungen und die ersten konkreten Implementierungsschritte vor. Starte danach mit der Umsetzung der Foundation, ohne bereits Echtgeld-Trading zu aktivieren.

**Langfristiges Ziel:** Ein kosteneffizientes, möglichst autonomes KI-Trading-System, das ungewöhnliche Marktaktivitäten frühzeitig erkennt, unterschiedliche Informationsquellen kombiniert und nur dann handelt, wenn eine überprüfbare Strategie und technische Sicherheitsregeln dies rechtfertigen.
