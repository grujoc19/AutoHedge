# Provider- und Modellkonfiguration je Rolle (G1.2)

G1.2 ergänzt [Settings](../autohedge/config.py) und unveränderliche
[Rollenprofile](../autohedge/ai/config.py) für alle sechs Zielrollen. Die Profile
werden aus Umgebungsvariablen/`.env` oder als typisierte Python-Konfiguration
geladen. Die [G1.1-Modellverträge](MODEL_CALLS.md) können aufgelöste Profile direkt
als Request-Parameter verwenden. Keine neuen Pakete, Provider-SDKs oder Modelle
werden dafür geladen oder aufgerufen.

**G1.3 ist inzwischen implementiert:** Der [Swarms/OpenAI-Pfad](OPENAI_RESEARCH.md)
verwendet die Profile seiner vier aktiven Rollen, einschließlich Modell, Limits
und Timeout. Ohne Profil bleiben die zwei alten Modellvariablen Fallbacks.
[G1.4](OLLAMA_RESEARCH.md) ergänzt Ollama als echten Adapter und gemischte
lokale/Cloud-Rollen. Andere aktive Backends werden vor Agentenkonstruktion mit
`ConfigurationError` abgewiesen; kein Profil wird ignoriert oder still durch
OpenAI ersetzt. Weitere Cloud-Adapter folgen in G1.5. OpenAI-Endpunkt:
`AUTOHEDGE_OPENAI_BASE_URL`, Default `https://api.openai.com/v1`, nur HTTPS ohne
eingebettete Credentials, Query oder Fragment. Ollama-Endpunkt:
`AUTOHEDGE_OLLAMA_BASE_URL`, Default `http://127.0.0.1:11434`, HTTP/HTTPS ohne
eingebettete Credentials, Query oder Fragment. Native Ollama-Modellkennungen
einschließlich Namespace und Tag bleiben unverändert.

## Rollen, Variablen und Defaults

| `AgentRole` / Rollenkennung | Variablenpräfix | Fallback ohne explizites Profil |
| --- | --- | --- |
| `NEWS_INTELLIGENCE` / `news_intelligence` | `AUTOHEDGE_NEWS_INTELLIGENCE_` | OpenAI + `AUTOHEDGE_SENTIMENT_MODEL` (`gpt-4o-mini`) |
| `MARKET_ANOMALY` / `market_anomaly` | `AUTOHEDGE_MARKET_ANOMALY_` | OpenAI + `AUTOHEDGE_MODEL` (`gpt-4.1`) |
| `ON_CHAIN_INTELLIGENCE` / `on_chain_intelligence` | `AUTOHEDGE_ON_CHAIN_INTELLIGENCE_` | OpenAI + `AUTOHEDGE_MODEL` |
| `QUANT` / `quant` | `AUTOHEDGE_QUANT_` | OpenAI + `AUTOHEDGE_MODEL` |
| `TRADING_DIRECTOR` / `trading_director` | `AUTOHEDGE_TRADING_DIRECTOR_` | OpenAI + `AUTOHEDGE_MODEL` |
| `RISK_MANAGER` / `risk_manager` | `AUTOHEDGE_RISK_MANAGER_` | OpenAI + `AUTOHEDGE_MODEL` |

Ein Fallback definiert lediglich, welches Profil bei einer Anfrage aufgelöst wird.
Er startet keinen Agenten. Insbesondere braucht reine Python-On-Chain-Erkennung
kein LLM; eine spätere optionale Interpretation darf separat konfiguriert werden.
Im heutigen Research entsprechen Director, Quant, Sentiment und Risk den Rollen
`trading_director`, `quant`, `news_intelligence` und `risk_manager`. Anomaly und
On-Chain sind noch nicht Teil dieses Ablaufs. Ihre Profile beeinflussen die
vier bestehenden Agenten nicht und erfordern keine zusätzlichen Keys für Research.

Jedes Präfix akzeptiert vier Suffixe:

| Suffix | Bedeutung / Grenzen |
| --- | --- |
| `PROVIDER` | `openai`, `anthropic`, `gemini`, `deepseek` oder `ollama`; Default `openai` |
| `MODEL` | Nicht leere Modellkennung, maximal 200 Zeichen; für jedes explizite Profil erforderlich |
| `MAX_OUTPUT_TOKENS` | Ganzzahl 1–32.768, Default 1.500 |
| `TIMEOUT` | Positive endliche Sekunden bis maximal 120; ohne Wert gilt `AUTOHEDGE_REQUEST_TIMEOUT` (Default 30) |

Sobald eine der vier Variablen vorhanden ist, muss auch `MODEL` vorhanden und
gültig sein. Leere Werte sind Fehler, keine stillen Rückfälle auf einen anderen
Provider. Bei einem Profil ohne `PROVIDER` gilt explizit OpenAI. Tokenlimits aus
Env werden aus Dezimalziffern gelesen; float-/bool-Werte werden nicht als Zähler
interpretiert. In der typisierten Konfiguration müssen Tokenlimits echte int
sein; bool ist ausgeschlossen. Timeout- und Tokenregeln stammen aus denselben
Typen wie `ModelRequest`.

Vertippte Rollenpräfixe mit diesen Suffixen sowie unbekannte Einstellungen unter
einem bekannten Rollenpräfix werden abgelehnt, statt kostenpflichtige Defaults
zu verwenden. Andere Umgebungsvariablen bleiben außerhalb dieser Konfiguration.

Beispiel für ein lokales News-Profil im bestehenden Research:

```dotenv
AUTOHEDGE_NEWS_INTELLIGENCE_PROVIDER=ollama
AUTOHEDGE_NEWS_INTELLIGENCE_MODEL=your-local-news-model
AUTOHEDGE_NEWS_INTELLIGENCE_MAX_OUTPUT_TOKENS=800
AUTOHEDGE_NEWS_INTELLIGENCE_TIMEOUT=20
```

Die [`.env.example`](../.env.example) lässt alle neuen Rollenprofile auskommentiert,
damit das bestehende Research weiter mit seinen bisherigen Variablen startet.
Ein einzelnes lokales Profil ersetzt nur diese Rolle. Für Research ohne Cloud-Key
alle vier aktiven Rollen auf Ollama setzen und zusätzliche Exa-Suche deaktivieren;
ein vollständiges Beispiel und Serveranforderungen stehen in [G1.4](OLLAMA_RESEARCH.md).

## Priorität und unveränderte Legacy-Variablen

Pro Variablenname überschreibt die Prozessumgebung die `.env`, auch bei leerem
Prozesswert. Unterschiedliche Variablen bleiben unterschiedliche Einstellungen:
ein explizites rollenbezogenes Modell aus `.env` hat Vorrang vor dem globalen
`AUTOHEDGE_MODEL` im Prozess. Nur der passende Rollenname überschreibt dieses
Profil. Nicht überschriebene Rollen erben weiterhin die bisherigen Main-/News-
Modelle. Ein nicht gesetzter Profil-Timeout erbt den aktuell konfigurierten
globalen Request-Timeout, auch nach einer Settings-Kopie.

`load_settings()` verändert `os.environ` nicht und führt keine Dotenv-Interpolation
aus. Syntax-/Validierungsfehler werden als sichere `ConfigurationError` ohne
Eingabewerte ausgegeben. Direkte ungültige Pydantic-Konstruktion kann dagegen wie
bisher eine `ValidationError` mit Eingabedetails erzeugen; diese nicht roh loggen.
`model_for()` validiert verschachtelte Profile erneut, einschließlich nicht
validierender `model_copy()`-Änderungen. Unbekannte Rollen/Provider sind Fehler.

## Schlüsselwahl und Schutz

| Backend | Benötigter Schlüssel bei `require_model_credentials(role)` |
| --- | --- |
| OpenAI | `OPENAI_API_KEY`; Alias `AUTOHEDGE_OPENAI_API_KEY` bleibt erhalten |
| Anthropic | `ANTHROPIC_API_KEY` |
| Gemini | `GEMINI_API_KEY`; Alias `GOOGLE_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Ollama | Kein Cloud-Key |

`Settings.model_api_key(role)` liefert nur den gewählten `SecretStr`, für Ollama
immer `None`. Ein vorhandener OpenAI-Key ersetzt keinen fehlenden Anthropic-,
Gemini- oder DeepSeek-Key. Die Schlüssel werden erst bei expliziter Prüfung der
betreffenden Rolle verlangt; andere Provider dürfen unkonfiguriert bleiben.
Das beweist weder Zugriff noch Modellverfügbarkeit. Es wurden keine API-Zugänge
oder Hardware-/Modellanforderungen live validiert.

Aliase gelten je Quelle; kanonische Prozesswerte gewinnen gegen Prozess-Aliase,
auch wenn der kanonische Wert leer ist. Ein Prozess-Alias gewinnt gegen einen
kanonischen `.env`-Wert. Leere/aus Leerzeichen bestehende Schlüssel werden zu
`None`, auch wenn sie als `SecretStr` übergeben werden. Alle neuen Modellschlüssel
sind aus Settings-Repr/Serialisierung ausgeschlossen und Teil der bestehenden
Log-Redaction. Credentials gehören nicht in Rollenprofile oder Modellnamen.
`Settings.headers()` bleibt ein Helfer für Datenprovider; Modelladapter verwenden
ihre eigenen Transportverträge. Ollama erhält keinen Cloud-Schlüssel.

Exa ist ein separater Datenanbieter. Lokale Modellkonfiguration verlangt keinen
Exa-Key; der bisherige Research benötigt bei aktivierter Suche weiterhin Exa und
für seine OpenAI-Modelle OpenAI. Nicht unterstützte aktive Backends lösen zuerst
die oben beschriebene Ablehnung aus und keinen unerwarteten Modellaufruf.

## Verwendung im Anwendungscode

```python
from autohedge.ai import AgentRole, ModelMessage, ModelRequest
from autohedge.config import load_settings

settings = load_settings()
role = AgentRole.NEWS_INTELLIGENCE
config = settings.model_for(role)  # Keine Credentials-/SDK-/Netzwerkprüfung.
settings.require_model_credentials(role)
request = ModelRequest(
    messages=(ModelMessage(role="user", content="Assess supplied evidence."),),
    **config.model_dump(),
)
# Erst ein passender, geprüfter Adapter darf complete_model(provider, request) nutzen.
```

Für strukturierte Python-Konfiguration:
`Settings(role_models=RoleModels(quant=RoleModelOverride(target=ModelTarget(...))))`.
`RoleModelConfig` ist das aufgelöste, unveränderliche Profil mit Pflicht-Timeout;
`RoleModelOverride` erlaubt einen geerbten Timeout. Es gibt noch keinen separaten
YAML-/JSON-Dateilader; `.env` und injizierte Settings sind die Konfigurationswege.

Offline-Tests prüfen alle sechs Rollen und ihre Unabhängigkeit, alte Defaults,
Präzedenz und leere Werte, Credential-Auswahl/Redaction, ungültige Kopien und
frühe Ablehnung nicht implementierter Backends. Ein Prozess mit gesperrtem Netzwerk und ohne Cloud-Keys
löst sechs lokale Profile auf und führt damit nur einen geskripteten Testadapter
aus; Swarms und Modell-SDKs werden dabei nicht geladen. Der installierte Wheel
wird zusätzlich außerhalb des Checkouts geprüft.

Die [G1.3-Anbindung](OPENAI_RESEARCH.md) und der [G1.4-Ollama-Adapter](OLLAMA_RESEARCH.md)
sind umgesetzt. Zusätzliche echte Swarms-Zyklen mit HTTP-Fixtures prüfen lokale
und gemischte Rollen sowie Fehler ohne Cloud-Fallback. Profile allein starten
weiterhin kein Modell. Nächster Schritt: **G1.5a**, Anthropic.
