# Anthropic im Research (G1.5a)

[AnthropicProvider](../autohedge/ai/anthropic.py) ergänzt OpenAI und Ollama an
derselben Modellgrenze. Jede aktive Rolle kann Anthropic wählen. Pro erfolgreichem
Research bleiben es fünf Aufrufe mit Limits, Timeouts und promptfreiem Audit.
Fehler stoppen die Stufe ohne Retry, Folgeaufruf oder Provider-Fallback.

## LiteLLM und Transport

Der Adapter verwendet den Text-Formatter `anthropic_messages_pt` aus LiteLLM
**1.76.1**, jetzt auch direkt auf diese bereits installierte Version festgelegt.
Paketversionen wurden nicht aktualisiert. Der Import erfolgt erst beim Aufruf.
Nur User-/Assistant-Textblöcke sind erlaubt; die Reihenfolge aller Rollen und Texte
muss erhalten bleiben. Gleiche aufeinanderfolgende Rollen dürfen mehrere Blöcke
in einer Nachricht bilden. Erste und letzte Fachnachricht müssen User sein;
Assistant-Prefill ist nicht aktiviert. Systemanweisungen gehen separat in `system`.

Dabei wurde eine Lücke aus G1.3 behoben: Swarms lässt den Systemprompt weg, weil
der Modellwrapper ihn ergänzen soll. Die Bridge bindet jetzt den bekannten
Rollenprompt der Team-Factory, ergänzt ihn genau einmal und lehnt einen
abweichenden Systemprompt ab. Das gilt auch für OpenAI und Ollama; das
Gesamtlimit von 32.000 Zeichen zählt die Anweisungen mit, ohne Kürzung.

Der vollständige LiteLLM-`completion`-Pfad wird nicht verwendet: Seine installierte
Anthropic-Normalisierung setzt fehlende Zähler auf null und addiert Cache-Erstellung
nicht zur Eingabe. Native Antwortprüfung und HTTP bleiben deshalb bei der Anwendung.
Dies konkretisiert die geplante LiteLLM-Anbindung; ein späteres Paketupdate muss
die interne Formatter-API erneut testen. LiteLLM-Router und Completion-Callbacks
werden nicht aufgerufen. Bestehende SDK-Importe und die Offline-Einstellungen
für Telemetrie/lokale LiteLLM-Kostentabelle bleiben relevant.

Ein Aufruf ist genau ein nicht gestreamter `POST /v1/messages`, mit `max_tokens`
und HTTP-Timeout aus dem Profil. Nur der Anthropic-Key geht als `x-api-key` an
den Endpunkt, neben `anthropic-version: 2023-06-01` und Request-ID. Tools,
Stop-Sequenzen, erweitertes Thinking und Prompt-Caching werden nicht angefordert.

## Antworten und Usage

Erfolg verlangt eine Assistant-`message`, Modellkennung, `end_turn` und nicht leeren
Text. Textblöcke werden ohne erfundene Trennzeichen zusammengefügt, insgesamt
maximal 20.000 Zeichen. `max_tokens` und `model_context_window_exceeded` bedeuten
Truncation, `refusal` bedeutet Refusal. Tools, Thinking, `tool_use`, `pause_turn`,
unerwartete Stop-Sequenzen und gemeldete Server-Tool-Nutzung erzeugen keinen Erfolg.
Falsche Schemas, unbekannte Abschlussgründe, doppelte JSON-Schlüssel und NaN werden
verworfen. Normalisierte Usage bleibt bei bekannten Fehlern erhalten.

Gesamteingabe = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`.
Nur wenn alle drei als nicht negative Ganzzahlen vorliegen, gilt die Summe als
bekannt. Fehlt eine Komponente, bleibt die Gesamteingabe unbekannt; bekannte
`output_tokens` machen Usage dann teilweise bekannt. Explizite 0 bleibt 0;
bool, Bruchzahlen, negative Werte und Strings sind ungültig. Preise werden nicht
berechnet; das aggregierte Audit reicht für genaue Cache-Tarife noch nicht aus.

Der native [Nachrichtenvertrag](https://github.com/anthropics/anthropic-sdk-python/blob/5738a256035e35285857fde0e89ae9b11b805f2f/src/anthropic/types/message.py)
und [Usage-Vertrag](https://github.com/anthropics/anthropic-sdk-python/blob/5738a256035e35285857fde0e89ae9b11b805f2f/src/anthropic/types/usage.py)
wurden am 21. Oktober 2026 gelesen. Reale Berechtigungen, Modellverfügbarkeit,
Qualität, Latenz und Abrechnung sind nicht live geprüft.

## Konfiguration und Abnahme

```dotenv
ANTHROPIC_API_KEY=your-key
AUTOHEDGE_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
AUTOHEDGE_TRADING_DIRECTOR_PROVIDER=anthropic
AUTOHEDGE_TRADING_DIRECTOR_MODEL=your-anthropic-model
AUTOHEDGE_TRADING_DIRECTOR_MAX_OUTPUT_TOKENS=1500
AUTOHEDGE_TRADING_DIRECTOR_TIMEOUT=30
```

Der Base-URL-Default steht im Beispiel; `/messages` wird angehängt. Overrides
erfordern HTTPS ohne Credentials, Query oder Fragment. Nur ein führendes
`anthropic/` wird vom Modellnamen entfernt; im Audit bleibt das angefragte Ziel.
Fremde Providerpräfixe werden abgewiesen. OpenAI ersetzt keinen fehlenden
Anthropic-Key. Nicht überschriebene Rollen behalten OpenAI-Defaults; Exa bleibt
separat konfiguriert. Lokale Rollen können [Ollama](OLLAMA_RESEARCH.md) verwenden.

Offline-Tests prüfen Payload, System-/Text-Treue, Limits, Cache/fehlende Zähler,
Auth-/Rate-Limit-/Schema-/Timeout-Fehler und Abbrüche ohne Fallback. Echte Swarms-
Läufe mit gesperrten Sockets und MockTransport prüfen Anthropic allein sowie drei
gemischte Backends und Audits. Der Wheel-Test importiert den Adapter ohne
Modell-SDK und baut anschließend ein Anthropic-Team ohne Netzwerkzugriff auf.

Nächster Schritt: **G1.5b, Gemini**, danach DeepSeek separat. Budgets, persistente
Fehler-Audits, strukturierte Intelligence-Rollen und Handelsausführung fehlen noch.
