# DeepSeek im Research (G1.5c)

[DeepSeekProvider](../autohedge/ai/deepseek.py) ergänzt den fünften konfigurierbaren
Anbieter. Jede aktive Rolle kann DeepSeek verwenden, kombiniert mit OpenAI,
Anthropic, Gemini oder Ollama. Die vier Rollen bleiben fünf aufeinanderfolgende
Stufen; je Stufe gibt es genau einen validierten Modellaufruf mit Rollenprompt,
Tokenlimit, Timeout und promptfreiem Audit. Es gibt keine Handelsausführung.

## Wiederverwendung und Request

OpenAI und DeepSeek verwenden jetzt dieselben nativen [Chat-Completions-Schemas
und Textprüfungen](../autohedge/ai/chat_completions.py). Der bestehende OpenAI-
Transport und seine Payload bleiben erhalten. DeepSeek ergänzt eigene Felder für
Cache-/Reasoning-Zähler und den Abschlussgrund `insufficient_system_resource`.
Die bisherigen OpenAI-Tests prüfen die ausgelagerte Verarbeitung mit.

Nur die reine Nachrichtenformatierung aus dem bereits festgelegten LiteLLM 1.76.1
wird verwendet. Rollen und Text müssen nach der Konvertierung erhalten bleiben;
zusätzliche Felder wie Tools werden abgelehnt. Die erste und letzte Fachnachricht
müssen User sein. Der gebundene Systemprompt bleibt als Systemnachricht erhalten.
Die vollständige LiteLLM-Completion wird nicht benutzt: Endpunkt, Key-Auswahl,
HTTP-Aufruf und Antwortprüfung bleiben unter Anwendungskontrolle. Insbesondere
wird nicht der `/beta`-Default oder die separate Env-Suche des installierten
LiteLLM-DeepSeek-Wrappers übernommen. Keine neuen Pakete oder Versionsupdates.

Ein Aufruf ist ein nicht gestreamter `POST /chat/completions` mit `max_tokens`
aus dem Rollenprofil und `thinking: {type: disabled}`. Es werden keine Tools,
Prefill-/Prefix-Modi oder automatischen Folgeaufrufe angefordert. Das gewählte
Modell muss den nicht denkenden Modus tatsächlich unterstützen. Der bekannte
Alias `deepseek-reasoner` ist auch mit Providerpräfix vor dem API-Aufruf gesperrt.
Für andere Modellnamen beweist das Request-Feld keine Server-Capability;
eine solche Prüfung und ein budgetierter Reasoning-Modus sind noch nicht vorhanden.

## Antwort und Usage

Nur eine Assistant-Choice mit Index 0, `stop` und nicht leerem Text kann erfolgreich
sein. `length` bedeutet Truncation, `content_filter` bzw. ein Refusal-Feld bedeutet
Refusal. `insufficient_system_resource` beendet die Stufe als nicht verfügbar,
ohne den teilweise gelieferten Text zu verwenden. Tool-/Function-Calls,
Reasoning-Inhalte oder positive Reasoning-Zähler und unbekannte Abschlussgründe
erzeugen keinen Erfolg. Die gemeinsame Antwortgrenze bleibt 20.000 Zeichen.

`prompt_tokens` enthält Cache-Tokens bereits. Wenn Hit und Miss vollständig
vorliegen, muss ihre Summe zum Prompt-Zähler passen; fehlt nur dessen Gesamtsumme,
kann sie exakt daraus berechnet werden. Gesamt minus bekannter Eingabe bzw.
Ausgabe darf den fehlenden anderen Zähler eindeutig ergänzen. Ansonsten bleiben
Zähler unbekannt oder teilweise bekannt; explizite 0 bleibt 0. `completion_tokens`
wird als gesamte native Ausgabe behandelt, Reasoning nicht nochmals addiert.
Bool, Strings, Bruchzahlen, negative und widersprüchliche Zähler werden verworfen.
Auch abgeleitete Ausgabezahlen unterliegen dem Rollenlimit.

Cache-/Reasoning-Details werden geprüft, bisher aber nur als aggregierte Usage im
Audit aufbewahrt. Es gibt keine Preisberechnung oder persistente Budgets; genaue
Abrechnung verschiedener Cache-Tarife benötigt später zusätzliche Audit-Felder.
Bei bekannten Refusal-/Truncation-/Ressourcenfehlern bleiben normalisierte Zähler
erhalten. Timeout, API-/Schemafehler oder unzulässige Antworten stoppen die Stufe
ohne Retry/Fallback und ohne vollständigen Report. Rohantworten und Keys gelangen
nicht in Fehlertexte oder Logs; Fehler-Audits werden noch nicht dauerhaft gespeichert.

## Konfiguration und offene API-Prüfung

```dotenv
DEEPSEEK_API_KEY=your-key
AUTOHEDGE_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
AUTOHEDGE_TRADING_DIRECTOR_PROVIDER=deepseek
AUTOHEDGE_TRADING_DIRECTOR_MODEL=your-non-thinking-deepseek-model
AUTOHEDGE_TRADING_DIRECTOR_MAX_OUTPUT_TOKENS=1500
AUTOHEDGE_TRADING_DIRECTOR_TIMEOUT=30
```

Der Base-URL-Default steht im Beispiel; `/chat/completions` wird angehängt.
Overrides verlangen HTTPS ohne Credentials, Query oder Fragment. Nur der
DeepSeek-Key geht als Bearer-Header an diesen Endpunkt; ein OpenAI-Key ersetzt
ihn nicht. Nur ein führendes `deepseek/` wird vor dem Senden entfernt; fremde
Providerpräfixe bzw. weitere Slashes sind Fehler. Das angefragte Ziel bleibt im
Audit erhalten, die vom Server gemeldete Modellkennung wird separat gespeichert.
Nicht überschriebene Rollen behalten OpenAI-Defaults; Exa bleibt separat.

Stand 9. November 2026: DeepSeeks [offizielles Repository](https://github.com/deepseek-ai/DeepSeek-V3#5-chat-website--api-platform)
beschreibt eine OpenAI-kompatible API. Der installierte LiteLLM-Adapter und dessen
reine Textformatierung wurden lokal gelesen. Die [aktuelle API-Dokumentation](https://api-docs.deepseek.com/api/create-chat-completion)
lieferte aus dieser Umgebung HTTP 403 und konnte nicht erneut abgeglichen werden.
Endpoint, Thinking-Parameter und Usage-Semantik sind deshalb explizite
Contract-Fixture-Annahmen bis zur Prüfung gegen die reale API. Es wurden keine
authentifizierten Modellaufrufe vorgenommen; Modellverfügbarkeit, Parametertreue,
Qualität, Latenz und Abrechnung bleiben ungeprüft.

Offline-Tests prüfen native Payload, System-/Text-Treue, ausgewählten Key, Limits,
Cache-/Reasoning-/Gesamtzähler, Schemafehler und Fehlerabbrüche ohne Fallback.
Echte Swarms-Läufe mit gesperrten Sockets und MockTransport prüfen DeepSeek allein
und gemischte Läufe, die zusammen alle fünf Anbieter abdecken. Pro Zyklus sind
höchstens vier unterschiedliche Anbieter möglich, weil es vier Rollen gibt.
Der Wheel-Test prüft Import und Teamaufbau außerhalb des Checkouts ohne Netzaufrufe.

Nächste Arbeit laut [Zielabgleich](GOAL_ALIGNMENT.md): G2.1, ein persistenter
Quellen-/Ereignisvertrag mit minimaler Datenbank und Migrationen. Die Modell-
Foundation ist offline umgesetzt; das ist kein Nachweis produktiver API-Verträge,
profitabler Strategien oder abgesicherter Ausführung.
