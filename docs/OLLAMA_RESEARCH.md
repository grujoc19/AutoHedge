# Lokale Modelle im Research (G1.4)

[OllamaProvider](../autohedge/ai/ollama.py) ergänzt den bestehenden
[Swarms-Research](OPENAI_RESEARCH.md). Jede aktive Rolle wählt über ihr
[Profil](MODEL_CONFIGURATION.md) ausdrücklich OpenAI oder Ollama. Die fünf
Stufen, Modellverträge, Snapshot-Prüfungen und promptfreien Stufen-Audits bleiben
gleich. Es werden keine zusätzlichen Pakete installiert, Modelle heruntergeladen
oder Server gestartet. Ein funktionierender Ollama-Server und passende bereits
installierte Modelle sind Voraussetzungen beim Betreiber.

## Vertrag und Fehlerverhalten

Ein Modellaufruf erzeugt genau einen nativen `POST /api/chat` über den vorhandenen
HTTP-Transport. `stream=false`, `think=false`, `truncate=false` und `shift=false`
fordern eine einzelne Antwort ohne zusätzliche Denk-Ausgabe, stilles Kürzen des
Eingabekontexts oder Kontextverschiebung an. `options.num_predict` übernimmt das
Tokenlimit der Rolle; der HTTP-Timeout übernimmt ihren Request-Timeout.
System-/User-/Assistant-Nachrichten bleiben getrennt. Native Ollama-Namen wie
`team/research:7b` werden unverändert gesendet; kein LiteLLM-Präfix wird ergänzt
oder entfernt. Das gemeldete Modell steht separat als `resolved_model` im Audit.

Nur `done=true`, Abschlussgrund `stop` und nicht leerer Assistant-Inhalt sind
erfolgreich. `length` wird als abgeschnittene Antwort verworfen. Ungültige oder
unvollständige Antworten, Tools, zusätzliche Denk-Ausgaben und gemeldete
`remote_host`-/`remote_model`-Antworten werden verworfen. Das ist keine semantische
Prüfung auf Refusal, Faktentreue oder Vorhersagekraft. Ein ungeeignetes Modell kann
auch bei gültigem Schema schlechte Research-Texte liefern.

`prompt_eval_count` und `eval_count` werden ausschließlich als nicht negative
Ganzzahlen angenommen. Fehlende Zähler bleiben unbekannt bzw. teilweise bekannt;
explizite 0 bleibt 0. Lokale Ausführung bedeutet keine erfundenen Nullkosten und
keine implementierte Budgetkontrolle. Native Antwortzeiten ersetzen nicht die
Zeitmessung der Anwendung.

Bei Timeout, Verbindungsfehler, HTTP-Fehler, kaputtem JSON, Modellmangel oder
ungültiger Ausgabe stoppt die aktuelle Research-Stufe. Es gibt keinen Retry,
Modell-Pull, weiteren Agentenaufruf oder Wechsel zu einer Cloud-API. Fehlertexte
enthalten keine Prompts, Antwortkörper oder Schlüssel. Wie bisher entsteht bei
einem fehlgeschlagenen Zyklus kein vollständiger Report; persistente Fehler-Audits
und Budgets bleiben spätere Arbeit.

Der Adapter folgt dem nativen [Ollama-API-Vertrag](https://github.com/ollama/ollama/blob/2cd63f7a15039ccd790e97b31edc091fa1707c56/api/types.go)
und der [Serverimplementierung](https://github.com/ollama/ollama/blob/2cd63f7a15039ccd790e97b31edc091fa1707c56/server/routes.go),
gelesen am 21. Oktober 2026. Die eingesetzte Serverversion muss insbesondere
`truncate=false` und `shift=false` tatsächlich unterstützen; ältere Server können
unbekannte Request-Felder ignorieren. AutoHedge führt bisher keinen Versions- oder
Capability-Handshake durch. Kontextgröße, Modell-Template und Verhalten bei
zu langem Input müssen vor produktivem Research gegen die konkrete Installation
geprüft werden. Ein Offline-Test beweist diese Servereigenschaften nicht.

## Konfiguration

`AUTOHEDGE_OLLAMA_BASE_URL` hat den Default `http://127.0.0.1:11434`.
Der Adapter hängt `/api/chat` an. HTTP für einen ausdrücklich konfigurierten
lokalen bzw. vertrauenswürdigen Netzwerk-Endpunkt und HTTPS sind erlaubt;
eingebettete Credentials, Query und Fragment sind ausgeschlossen. Andere
Daten-/Cloud-URLs verlangen weiterhin HTTPS. Kein Cloud-, Exa- oder Wallet-Key
wird an Ollama weitergegeben. Proxy-, CA- und TLS-Konfiguration bleiben erhalten.
`127.0.0.1` bezeichnet die Maschine bzw. den Container des Python-Prozesses;
ein separater Ollama-Container benötigt einen erreichbaren konfigurierten Host.

Beispiel für vollständig lokales Research ohne zusätzliche Exa-Suche:

```dotenv
AUTOHEDGE_SEARCH_ENABLED=false
AUTOHEDGE_OLLAMA_BASE_URL=http://127.0.0.1:11434
AUTOHEDGE_TRADING_DIRECTOR_PROVIDER=ollama
AUTOHEDGE_TRADING_DIRECTOR_MODEL=your-installed-local-model
AUTOHEDGE_QUANT_PROVIDER=ollama
AUTOHEDGE_QUANT_MODEL=your-installed-local-model
AUTOHEDGE_NEWS_INTELLIGENCE_PROVIDER=ollama
AUTOHEDGE_NEWS_INTELLIGENCE_MODEL=your-installed-local-model
AUTOHEDGE_RISK_MANAGER_PROVIDER=ollama
AUTOHEDGE_RISK_MANAGER_MODEL=your-installed-local-model
```

Alle vier aktiven Rollen müssen explizit gesetzt sein, wenn kein OpenAI-Key
verwendet werden soll. Nicht überschriebene Rollen behalten ihre OpenAI-Defaults.
Für einen Cloud-Director und lokale Fachrollen nur das Director-Profil auf
`openai` plus native Modellkennung ändern und `OPENAI_API_KEY` konfigurieren.
Damit bleiben zwei ausdrücklich gewählte Cloud-Aufrufe (These und Synthese).
Bei `search_enabled=true` bleibt ein Exa-Key für den separaten News-Abruf nötig;
ein vorhandener Snapshot verwendet weiterhin keine zusätzliche Suche.

Ollama kann selbst Cloud-Modelle anbieten. Für lokale Ausführung muss der
**Ollama-Server** Cloud-Funktionen deaktivieren (`OLLAMA_NO_CLOUD=1`) und lokale
Modelle bereitstellen. Die Anwendung ändert diese Serverkonfiguration nicht.
Die Ablehnung gemeldeter Remote-Antworten erfolgt erst nach dem Aufruf und kann
eine bereits serverseitig ausgelöste Cloud-Anfrage nicht verhindern. Auch ein
beliebiger vorgeschalteter Proxy wird durch den Adapter nicht attestiert.

## Abnahme und weitere Arbeit

Unit-Tests verwenden HTTP-Fixtures für native Payload, Endpunkt, Schlüsseltrennung,
Limits, Usage, fehlerhafte Antworten und Ausfälle. Runtime-Tests mit echtem Swarms
und gesperrten Netzwerkverbindungen führen fünf lokale bzw. gemischte Modell-POSTs
über MockTransport aus und prüfen Rollenrouting und Audit. Ein lokaler Fehler
nach einer erfolgreichen Cloud-These stoppt den Lauf ohne Cloud-Fallback.
Der installierte Wheel wird zusätzlich außerhalb des Checkouts geprüft.

Nicht geprüft: echter Ollama-Server, lokale Modellqualität, RAM/VRAM-Bedarf,
reale Latenz und Server-Capabilities. Es gibt weiterhin keine strukturierte
Signal- oder Risk-Engine und keine Handelsausführung. Nächster Schritt ist
**G1.5a: Anthropic**, danach Gemini und DeepSeek jeweils separat.
