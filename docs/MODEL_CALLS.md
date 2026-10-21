# Providerneutrale Modellaufrufe (G1.1)

Der Baustein [autohedge.ai](../autohedge/ai/__init__.py) definiert den gemeinsamen
Vertrag für spätere Modelladapter. Er lädt weder Swarms/LiteLLM noch
Provider-SDKs, Konfiguration oder Credentials und führt beim Import keine
Netzwerkaufrufe aus. Es wurden keine neuen Abhängigkeiten hinzugefügt.

**Stand:** Verträge, validierte Aufrufgrenze und ein geskripteter Test-Fake sind
implementiert. Rollen-Konfiguration, echte OpenAI-/Ollama-/weitere Adapter und
Anbindung an die Research-Pipeline folgen in G1.2–G1.5. Die bestehenden
Research-Agenten verwenden diesen Pfad noch nicht. Das Modul unterstützt
zunächst Textantworten; Tool-Calls, Streaming und multimodale Inhalte sind
kein Teil dieses Vertrags. Der bestehende Exa-/Swarms-Pfad bleibt erhalten;
seine Migration muss lesende Recherche separat berücksichtigen.

## Verträge

Alle Modelle übernehmen die eingefrorenen, extra-Felder ablehnenden,
verschachtelt erneut validierten [Contract-Regeln](../autohedge/contracts.py).
UTC-bewusste Zeitstempel und UUIDs erlauben eine eindeutige Zuordnung.

| Vertrag | Bedeutung |
| --- | --- |
| `ModelTarget` | Providerkennung und angefragtes Modell; keine URL oder Credentials-Konfiguration. Kennungen sind erweiterbar, ihre Akzeptanz ist noch kein implementierter Provider |
| `ModelMessage` | System-/User-/Assistant-Text; keine Tool-Rolle und keine Ausführungsrechte |
| `ModelRequest` | Eindeutige `request_id`, Zielmodell, Nachrichten, `max_output_tokens`, `timeout_seconds` |
| `TokenUsage` | Expliziter Verfügbarkeitsstatus und separat gemeldete Input-/Output-Zähler |
| `ModelReply` | Adapter-normalisierte Antwort mit denselben Ziel-/Aufruf-IDs, optionalem `resolved_model`, Abschlussgrund, Inhalt und Usage |
| `ModelResult` | Nur eine korrelierte, vollständige, rechtzeitig zurückgekehrte Antwort; enthält Request/Reply sowie Start/Ende und monotone Laufdauer |

Defaults: 1.500 Output-Tokens und 30 Sekunden pro Aufruf; konfigurierbare Grenzen
1–32.768 Tokens und maximal 120 Sekunden. Nachrichten: höchstens 64, insgesamt
32.000 Zeichen, mindestens eine User-Nachricht; eine Systemnachricht nur an erster
Stelle. Antworttext ist nicht leer und maximal 20.000 Zeichen lang. Tokenzahlen
sind strikt ganzzahlig, nicht negativ; bool, float und numerische Strings werden
nicht still in Zähler umgewandelt.

`target` bleibt das angefragte Ziel, etwa ein Modellalias. `resolved_model` kann
die vom Anbieter gemeldete konkrete Version festhalten; ohne solche Information
bleibt es `None`. Ein abweichender Reply-Provider oder ein anderes angefragtes
Modell wird nicht still als Fallback akzeptiert.

## Usage: unbekannt ist nicht null

| Status | Zähler | `total_tokens` |
| --- | --- | --- |
| `known` | Input und Output explizit vorhanden, auch jeweils 0 zulässig | Summe |
| `partial` | Genau einer der beiden Zähler vorhanden | `None` |
| `unknown` | Beide unbekannt (`None`) | `None` |

`total_tokens` ist eine abgeleitete Python-Eigenschaft; gespeichert werden Status
und die beiden Zähler. Der Client schätzt keine Tokens aus Zeichenlängen und
erfindet keine Preise. Gemeldete Zähler belegen noch keine Rechnungsgenauigkeit;
anbieterabhängige Cache-/Reasoning-/Tarifdetails und persistente Kostenbudgets
folgen später. Der Adapter muss dokumentieren, wie er Anbieterzähler auf den
gemeinsamen Vertrag abbildet und das angefragte Output-Limit durchsetzt.

Gemeldeter Output oberhalb des Limits führt zu `output_limit`. Bei unbekanntem
Output-Zähler kann der Client diese Anzahl nicht unabhängig kontrollieren; die
Zeichengrenze bleibt aktiv. Der Client kann bereits entstandene Kosten nicht
verhindern oder rückgängig machen. Die spätere Budget-Policy darf Unknown-Usage
oder fehlgeschlagene Aufrufe deshalb nicht automatisch als kostenlos behandeln.

## Ein Aufruf, keine versteckten Versuche

Ein Adapter implementiert `ModelProvider.complete(request) -> ModelReply` und
normalisiert Herstellerdaten und Fehler. Der Aufrufer verwendet
`complete_model(provider, request)`. Diese Grenze:

1. Validiert auch per `model_copy` veränderte Requests vor einem Provideraufruf.
2. Prüft injizierbare UTC- und monotone Uhren und ruft den Provider genau einmal auf.
3. Validiert die Antwort erneut und bindet sie an Aufruf-ID und Zielmodell.
4. Prüft Zeitregression/Frist, vollständigen Abschluss und gemeldete Output-Grenze.
5. Liefert einen `ModelResult` oder wirft einen sicheren `ModelCallError`.

Keine automatischen Retries, Cloud-Eskalationen, Caches, Tool-Aufrufe oder
Logausgaben. SDK-Retries muss der jeweilige Adapter ebenfalls deaktivieren.
Ein synchron hängender Aufruf lässt sich hier nicht hart unterbrechen. Der
Adapter muss den Request-Timeout an den SDK-/HTTP-Aufruf weitergeben; eine
verspätete Rückkehr wird verworfen, nicht nachträglich als Erfolg akzeptiert.

Fehlercodes: `invalid_request`, `invalid_response`, `response_mismatch`,
`output_limit`, `timeout`, `unavailable`, `authentication`, `rate_limited`,
`provider_failure`, `truncated`, `refused` und `invalid_clock`.
`length` und `refusal` sind normalisierte Abschlussgründe, aber keine nutzbaren
Ergebnisse. Fehler erben von `AutoHedgeError`; ihre Meldung enthält nur den
festgelegten Code, nicht Herstellertext, Prompt, URL oder Credentials.
Adapter sollten semantische Fehler als `ModelCallError` melden. Zusätzlich werden
Python-Timeout-/Verbindungsfehler sowie bestehende `ProviderError`-Statuscodes
401/403/429 normalisiert; unbekannte Exceptions werden zu `provider_failure`.

`ModelCallError.request_id` gehört dem tatsächlichen Request. Bei validierter,
zugeordneter Reply bleiben die gemeldeten Zähler auch bei Timeout nach Rückkehr,
Truncation, Refusal, Output-Überschreitung oder fehlerhafter Abschlussuhr in
`error.usage` erhalten. Bei unlesbaren oder fremden Antworten ist die Usage
unbekannt; sie wird nicht aus beliebigen Rohdaten extrahiert. Ein bereits vom
Adapter sicher normalisierter Fehler kann ebenfalls bekannte/teilweise Usage
mitliefern. Dauerhafte Fehler-Audits und Budgetbuchung sind noch nicht integriert.

Requests/Replies verbergen ihren Inhalt in `repr`, enthalten ihn aber bewusst in
expliziter JSON-Serialisierung. Keine Schlüssel in Nachrichten ablegen und keine
rohen Objekte/JSONs protokollieren. `repr` und Secret-Redaction sind kein allgemeiner
Schutz gegen absichtliches Loggen vertraulicher Inhalte. Validierte Textausgabe ist
auch noch kein geprüftes JSON-AgentFinding oder Handelssignal.

## Offline-Abnahme

[Fake und Fixtures](../tests/unit/model_fixtures.py) liegen nur im Testverzeichnis,
nicht als Produktions-Fallback im Paket. Tests prüfen Verfügbarkeitsstatus,
Roundtrip/Unveränderlichkeit, gefälschte Kopien, Daten-/Zeichen-/Tokengrenzen,
fremde Modell-/Aufruf-IDs, Timeout, Refusal/Truncation, Uhrfehler, sichere
Exceptions und genau einen Aufruf. Ein separater Runtime-Test importiert den
Baustein ohne Credentials, verbietet Socket-Verbindungen und prüft, dass keine
Modell- oder Signier-SDKs geladen werden. Der Wheel-Test importiert die neuen
Verträge außerhalb des Checkouts.

```bash
.venv/bin/python -m unittest discover -s tests/unit -p 'test_model*.py' -v
```

Nächster Schritt: G1.2, Provider-/Modellkonfiguration für alle sechs Rollen unter
Erhalt der bisherigen Modellvariablen. G1.1 allein ändert keine Handelsentscheidung,
keine Agentenberechtigung und aktiviert keine entfernten Modelle.
