# Swarms/OpenAI an der Modellgrenze (G1.3)

Der bestehende Fünf-Stufen-Research verwendet jetzt [Modellverträge](MODEL_CALLS.md)
und [Rollenprofile](MODEL_CONFIGURATION.md). Swarms, Prompts, Conversation,
Orchestrator, Snapshot-Prüfungen und die frische Team-Factory bleiben erhalten.
Der Modelltransport und die Begrenzung der Aufrufe gehören der Anwendung.
Keine neuen Pakete oder zusätzlichen Modellaufrufe wurden eingeführt.

## Ablauf und Wiederverwendung

```text
ResearchOrchestrator -> SwarmsResearchAgent -> echter Swarms Agent
                    -> SwarmsModelBridge -> complete_model -> OpenAIProvider
                    -> bestehender HTTP-Transport -> Chat Completions
```

Vier Rollen laufen in fünf Stufen: Director-These, Quant, Sentiment, Risk,
Director-Synthese. Pro Stufe ist genau ein vollständiger Modellaufruf zulässig;
damit sind es fünf statt bisher bis zu sechs Modellaufrufe. Der Director wird
zwischen seinen beiden Stufen kontrolliert zurückgesetzt, was den Aufrufzähler
und Ergebnisnachweis betrifft; die Anwendung übergibt weiterhin alle vorherigen
Fachtexte. Swarms-Agenten bleiben pro Zyklus getrennte Objekte.

Die schmalen Runner unter [ai/swarms.py](../autohedge/ai/swarms.py) implementieren
weiterhin `run(task) -> str`. Das reale SDK-Objekt ist über `.agent` zugänglich,
die Modellgrenze über `.bridge`. Auch `build_director_agent()` liefert jetzt
diesen validierten Runner, damit wiederholte Aufrufe ihren Zustand zurücksetzen.
`AutoHedge.run()`/`run_report()` behalten ihre bisherige Fassade. Eigene
`team_factory`-Implementierungen bleiben möglich und sind vertrauenswürdiger Code.

Kein SDK-Agent erhält Tools, Signing-Funktionen, Fallback-Modelle oder Streaming.
Loops/SDK-Retries bleiben bei einem Versuch; Kontext-Kompression, Tool-Summary
und dynamische Modellwahl bleiben aus. Die Bridge validiert den tatsächlich
übergebenen Transcript einschließlich Systemprompt und Historie gegen das
32.000-Zeichen-Budget. Ein zweiter Modellversuch innerhalb derselben Stufe wird
vor dem Provider blockiert. Ein verschluckter Fehler oder ein SDK-Ergebnis ohne
passenden erfolgreichen Modellaufruf darf keinen Research-Erfolg erzeugen.

## OpenAI-Adapter

[OpenAIProvider](../autohedge/ai/openai.py) nutzt den vorhandenen
`request_json`-/httpx-Transport. Swarms' bisheriger LiteLLM-Wrapper wandelt
Antworten früh in Text um und kann fehlende Usage-Zähler als 0 darstellen.
Deshalb wird nur dieser Modelltransport ersetzt; Swarms bleibt Agenten-Runtime.
Native Abschlussgründe und optionale Zähler bleiben an der Grenze erhalten.

Ein Aufruf ist ein nicht gestreamter POST an
`https://api.openai.com/v1/chat/completions`, genau eine Choice, keine Tools.
`max_completion_tokens` und HTTP-Timeout stammen aus dem Rollenprofil. Nur der
OpenAI-Key wird als Bearer-Header verwendet, niemals als Prompt oder SDK-Default.
`AUTOHEDGE_OPENAI_BASE_URL` erlaubt einen ausdrücklich konfigurierten HTTPS-
Endpunkt, ohne Credentials, Query oder Fragment; Default ist
`https://api.openai.com/v1`. TLS-/Proxy-Einstellungen bleiben erhalten.

OpenAI-Profile der vier aktiven Rollen funktionieren jetzt, einschließlich
unterschiedlicher Modelle/Limits. Die bisherigen zwei Modellvariablen bleiben
Fallbacks. Ein führendes `openai/` wird ausschließlich für den nativen API-
Modellnamen entfernt; im Audit bleibt das angefragte Ziel erhalten. Andere
Providerpräfixe werden nicht automatisch geroutet. Noch nicht implementierte
Backends führen vor Agentenkonstruktion zur Ablehnung. Inaktive Anomaly-/On-Chain-
Profile erzeugen weiterhin keine zusätzlichen Agenten oder Modellaufrufe.

Der Adapter prüft Modellkennung, eine einzelne Assistant-Choice mit Index 0,
Abschlussgrund, Inhalt und ganzzahlige nicht negative Usage-Zähler. Zusätzliche
API-Metadaten dürfen vorhanden sein. Doppelte JSON-Schlüssel, NaN, fremde
Tool-/Function-Calls, leere erfolgreiche Inhalte oder falsche Schemas werden
abgewiesen. `length` bedeutet Truncation; `content_filter` und ein Refusal-Feld
bedeuten Refusal. Bei Fehlern bleiben sicher normalisierte Zähler erhalten;
unlesbare Zähler sind unbekannt, nicht null. Kein Modell-POST wird wiederholt,
auch nicht bei 429/503 oder Timeout. Es gibt keinen Modell-Fallback.

## Exa als expliziter Datenabruf

Bei Legacy-Research mit `search_enabled=true` verwendet der Sentiment-Runner
vor seinem Modellaufruf einmal den vorhandenen Exa-News-Adapter. Die Query
stammt aus dem ursprünglichen Research-Task, nicht aus einer LLM-Anweisung.
Sie ist auf 5.000 Zeichen begrenzt; verwendete Query und Kürzungsflag stehen
im ergänzten Datenkontext, der ursprüngliche Research-Task bleibt vollständig.
Die vorhandenen maximal zwei Nachrichten mit Zeit-/Quellenprüfung werden als
`supplemental_news` übergeben. Es gibt keine zusätzliche LLM-Runde, keine
LLM-gesteuerte Tool-Ausführung und keine beliebig vielen Suchanfragen. Der
Exa-Transport behält seine separat begrenzten Read-Retries (maximal zwei Versuche).

Bei Fehlern stoppt die Stufe vor dem Sentiment-Modellaufruf. Ein gemeinsamer
eingefrorener Snapshot deaktiviert den zusätzlichen Abruf; außerdem erkennt
der Runner einen übergebenen Snapshot selbst. Er verwendet dann ausschließlich
die bereits eingefrorenen Daten. Die bestehende Datenaltersprüfung vor/nach
jeder Stufe bleibt erhalten. Exa-freies Research benötigt keinen Exa-Key.
Das ist weiterhin kein kontinuierlicher News Collector oder Neuigkeitsnachweis.

## Report und Grenzen

Jede erfolgreiche Standardstufe enthält einen promptfreien
`model_calls`-Eintrag ([ModelCallAudit](../autohedge/ai/audit.py)):
Rolle, eindeutige Request-ID, angefragtes Ziel, gemeldete Modellversion soweit
vorhanden, bekannte/teilweise/unbekannte Tokenzähler, Limits und Zeitmessung.
Die Domänenmodelle prüfen Stufenzuordnung, Zeitgrenzen und eindeutige Aufruf-IDs
im ganzen Report. Ein fremder bzw. alter Director-Aufruf kann nicht als aktueller
Nachweis wiederverwendet werden. Diese Metadaten werden nicht als vorherige
Fachbefunde zurück in Modellprompts kopiert.

Alte Reports und injizierte Fake-Rollen dürfen weiterhin leere `model_calls`
haben; daraus wird keine erfundene Usage abgeleitet. Die vorhandenen SDK-
Aggregatzähler sind nicht die Quelle für Abrechnung. Es gibt noch keine
Tarifberechnung, persistenten Budgets oder dauerhafte Audit-Datenbank. Bei einem
fehlgeschlagenen Zyklus gibt es wie bisher keinen vollständigen Report; spätere
Fehler-Audits dürfen entstandene oder unbekannte Kosten nicht als null behandeln.

Ein synchron hängender Provider wird nicht hart abgebrochen: HTTP-Timeout und
Prüfung der zurückgekehrten Laufzeit begrenzen den normalen Pfad. Der
Zyklus-Timeout wird weiterhin vor/nach Stufen geprüft. Modellfehler sind nach
außen sichere `ResearchError`-/`ConfigurationError`-Kategorien ohne Rohantworten
oder Credentials. Kein Agent bekommt Risk-Freigabe oder Ausführungsbefugnis.

Offline-Abnahme: HTTP-Fixtures für Payload/Auth/Timeout/Usage/Schema und Fehler,
echte Swarms-Zyklen mit gesperrten Sockets, ein vollständiger Standardlauf mit
fünf gemockten OpenAI-POSTs und einem Exa-Abruf sowie frühe Abbrüche ohne Retry.
Live-API-Zugang, reale Modellqualität und Abrechnungsgenauigkeit sind ungeprüft.
Nächster Schritt ist **G1.4: Ollama-Adapter** mit denselben Modellverträgen.
