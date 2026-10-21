# Gemini im Research (G1.5b)

[GeminiProvider](../autohedge/ai/gemini.py) ergänzt die bestehende Modellgrenze
über die Gemini Developer API mit API-Key.
Jede aktive Rolle kann Gemini wählen; OpenAI, Anthropic und Ollama bleiben
kombinierbar. Es bleiben fünf validierte Aufrufe je erfolgreichem Research-Zyklus
mit gebundenen Rollen-Systemprompts, Limits, Timeouts und promptfreien Audits.

## Formatierung und nativer Aufruf

Der Adapter nutzt ausschließlich LiteLLMs reine Nachrichtenkonvertierung
`_gemini_convert_messages_with_history` aus der bereits festgelegten Version
1.76.1. Ihr Ergebnis wird erneut validiert: nur Textteile, vollständige Erhaltung
von Rollen, Texten und Reihenfolge; Assistant wird zu `model`. Gleiche benachbarte
Rollen dürfen mehrere Teile einer Nachricht bilden. Die erste und letzte
Fachnachricht müssen User sein. Der gebundene Systemprompt geht separat in
`systemInstruction`; nicht vertrauenswürdiger Fachtext bleibt getrennt davon.

Die Anwendung sendet genau einen `POST /models/<native-model>:generateContent`
über den bestehenden HTTP-Transport. Keine Completion-Callbacks, Router, SDK-
Retries, Streams, Tools, Grounding, URL-Recherche oder Cache-Erstellung werden
aufgerufen. LiteLLMs vollständiger Request-/Response-Pfad wird nicht verwendet:
Die installierte Version setzt fehlende Zähler auf null und kann unbekannte
Abschlussgründe als `stop` behandeln. Die private Formatter-API braucht bei einem
späteren Paketupdate erneut Contract- und Integrationstests.

`generationConfig` setzt `candidateCount=1`, `maxOutputTokens` aus dem Rollenprofil,
`responseMimeType=text/plain` und `thinkingConfig={thinkingBudget: 0, includeThoughts: false}`.
**Das gewählte Modell muss deaktiviertes Thinking unterstützen.** Insbesondere
sind Modelle mit verpflichtendem Thinking nicht mit diesem ersten Adaptermodus
kompatibel. Ablehnungen der API werden nicht durch stilles Entfernen dieser
Einstellung oder ein größeres Budget umgangen. Ein Capability-Handshake und ein
separat budgetierter Thinking-Modus sind nicht implementiert. Reale Unterstützung
und Modellverfügbarkeit müssen gegen die gewählte API-/Modellversion geprüft werden.

## Antwort und Usage

Erfolg verlangt genau einen Kandidaten mit Index 0 (darf als Proto-Default fehlen),
Rolle `model`, nicht leerem Text und Abschlussgrund `STOP`. Textteile werden ohne
erfundene Trennzeichen verbunden; das gemeinsame Limit bleibt 20.000 Zeichen.
Prompt-/Safety-Blockaden werden als Refusal behandelt, `MAX_TOKENS` und
`CONTINUATION` als Truncation. Unbekannte Abschlussgründe, Tool-/Thinking-/Medienteile,
Grounding-/URL-Context und ungültige Antworten werden verworfen. Eine fehlende
`modelVersion` bleibt unbekannt und wird nicht aus dem angefragten Modell erfunden.

`promptTokenCount` enthält bereits Cache-Tokens; `cachedContentTokenCount` wird
nicht nochmals addiert. Ausgabe umfasst sichtbare Kandidaten plus Thoughts.
Fehlende Zähler bleiben unbekannt bzw. teilweise bekannt. Konsistente Gesamtzähler
können eindeutige Lücken schließen: Sind Gesamt minus Prompt exakt die bekannten
Kandidaten-Tokens, müssen die übrigen nicht negativen Komponenten null sein.
Eine fehlende Angabe wird sonst nicht pauschal zu 0. Explizite Nullwerte bleiben
0; bool, Strings, Bruchzahlen, negative und widersprüchliche Zähler sind ungültig.
Gemeldete Thinking-/Tool-Tokens und unerklärter Mehrverbrauch werden abgewiesen.
Auch bekannte sichtbare Ausgabe oberhalb des Limits wird bei sonst unvollständiger
Usage abgewiesen. Es gibt weiterhin keine Preisberechnung oder persistente Budgets.

Timeout, API-/Schemafehler und abgewiesene Antworten stoppen die Stufe ohne Retry,
Weiterführung oder Provider-Fallback. Sichere Fehler und normalisierte Usage bei
bekannten Fehlerfällen bleiben an der Modellgrenze; Rohtexte/Keys werden nicht
geloggt. Ein fehlgeschlagener Zyklus erzeugt keinen vollständigen Report.

## Konfiguration und Abnahme

```dotenv
GEMINI_API_KEY=your-key
AUTOHEDGE_GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
AUTOHEDGE_TRADING_DIRECTOR_PROVIDER=gemini
AUTOHEDGE_TRADING_DIRECTOR_MODEL=your-compatible-gemini-model
AUTOHEDGE_TRADING_DIRECTOR_MAX_OUTPUT_TOKENS=1500
AUTOHEDGE_TRADING_DIRECTOR_TIMEOUT=30
```

`GOOGLE_API_KEY` bleibt ein Alias für `GEMINI_API_KEY`. Nur der gewählte Key geht
als `x-goog-api-key` in den Header, niemals als URL-Query. Andere Cloud-/Exa-/Wallet-
Keys werden nicht übertragen. Die Base-URL darf nur HTTPS ohne Credentials,
Query oder Fragment verwenden; Proxy-/CA-/TLS-Einstellungen bleiben erhalten.
Native Modellkennungen und ein führendes `gemini/` sind erlaubt. Nach Entfernen
dieses Präfixes sind nur ASCII-Buchstaben/Ziffern sowie Punkt, Unterstrich und Bindestrich
zulässig, beginnend mit Buchstabe/Ziffer. `models/`, Pfade, Query, Fragment, Prozent-
Escapes und Methoden-Suffixe sind nicht erlaubt. Im Audit bleibt das angefragte Ziel.
Nicht überschriebene Rollen behalten ihre OpenAI-Defaults; Exa bleibt separat.

Grundlagen: native [v1beta-Protobuf-Definition](https://github.com/googleapis/googleapis/blob/master/google/ai/generativelanguage/v1beta/generative_service.proto)
und [Google-GenAI-Typen](https://github.com/googleapis/python-genai/blob/e1c4fc78ee29d45db7bb3e10f173bafa15aeb634/google/genai/types.py),
gelesen am 21. Oktober 2026. Offline-Tests prüfen Payload, System-/Text-Treue, Key-
Trennung, Zähler, Grenzen, Blockaden, Fehler und keine Fallbacks. Echte Swarms-Läufe
mit gesperrten Sockets und MockTransport prüfen Gemini allein sowie vier gemischte
Anbieter. Der Wheel-Test importiert den Adapter ohne Modell-SDK und baut ein Team
ohne Netzaufruf. Echte API-Zugänge, Modellqualität und Serververhalten sind ungeprüft.

Nächste Arbeit steht im [Zielabgleich](GOAL_ALIGNMENT.md): zunächst G1.5c DeepSeek,
danach persistente Data Collection. Signale, Risk Engine und Trading fehlen weiterhin.
