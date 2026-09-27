# Liveticker

Ein Ticker pro Vorlage (eidgenössisch und kantonal), geschrieben von einer
Agent-Session, moderiert im Admin.

## Aufbau

Die Arbeitsteilung ist der Kern: **Code entscheidet, wann hingeschaut wird, das
Modell entscheidet, was gesagt wird.** Ein LLM als Filter über ~25 Vorlagen im
Minutentakt wäre langsam, teuer und inkonsistent — Schwellenwerte sind das nicht.

```
neue Resultate
  → abst.tasks.predict_results_task        (bestehende Pipeline)
  → ticker.tasks.refresh_ticker_state
        state.build_snapshot()             Influx + Postgres → ein kompaktes dict
        → TickerState                      gecacht, damit Lesen billig ist
        → events.detect()                  deterministisch, reine Funktion
        → TickerEvent (pending)

Agent-Session (anderer Rechner)
  → bin/ticker status / brief              liest nur den Cache
  → bin/ticker post / dismiss              schreibt

Moderation
  → /admin/ticker/tickerpost/              bearbeiten, zurückziehen
```

`TickerState` ist der Grund, dass der Agent im Minutentakt pollen kann: ein
Snapshot kostet mehrere Influx-Abfragen plus die Ridge-Regression für die
Residuen, deshalb wird er serverseitig gebaut und nur gelesen.

### Dateien

| Datei | Aufgabe |
|---|---|
| `state.py` | Snapshot bauen (die einzige Stelle, die Influx kennt) |
| `events.py` | Signifikanz-Erkennung, rein, ohne Django |
| `service.py` | Event-Buchhaltung, posten, moderieren |
| `api.py` | die Schnittstelle für den Agenten (Bearer-Token) |
| `tasks.py` | serverseitiger Refresh, hängt an `predict_results_task` |
| `demo.py` | Szenario-Generator für lokale Entwicklung |
| `AGENT.md` | Arbeitsanweisung für die Agent-Session |
| `bin/ticker` | Client, nur stdlib, läuft überall |

### Events

| Kind | Severity | Auslöser |
|---|---|---|
| `final` | 3 | Vorlage abgeschlossen |
| `decided` | 3 | 10–90-Band vollständig auf einer Seite von 50 % |
| `flip` | 3 | Hochrechnung wechselt die Seite |
| `first_signal` | 2 | erste Hochrechnung ab 3 % ausgezählt |
| `city_in` | 2 | grosse Gemeinde (≥ 3 % der Stimmberechtigten) definitiv |
| `staende_split` | 2 | Volksmehr und Ständemehr laufen auseinander |
| `shift` | 1 | ≥ 1,5 Pp. Bewegung seit dem letzten Post |
| `outlier` | 1 | ≥ 4 Pp. Abweichung von der Erwartung, ≥ 3000 Stimmberechtigte |

Schwellenwerte stehen als Konstanten oben in `events.py` und `state.py`.

Ein Event wird pro Vorlage und Key genau einmal angelegt. Es bleibt `pending`,
bis es gepostet oder verworfen wird — das ist der Mechanismus, der verhindert,
dass dieselbe Beobachtung bei jedem Durchgang wieder auftaucht. Sobald eine
Vorlage `finished` ist, werden alle offenen Events ausser `final` automatisch
verworfen.

## Betrieb

`.env` auf dem Server:

```
TICKER_API_TOKEN=<langes Zufallstoken>
TICKER_TIME_ZONE=Europe/Zurich    # Default, Ticker-Zeiten sind immer Schweizer Zeit
```

Öffentliche Seiten: `/ticker/`, `/ticker/<YYYY-MM-DD>/`, `/ticker/vorlage/<id>/`.
Moderation: `/admin/ticker/tickerpost/`.

Der Refresh hängt an `predict_results_task`, läuft also genau dann, wenn neue
Resultate da sind. Dazu alle 5 Minuten `refresh_active_ticker_states` als Netz
für Zustandswechsel ohne neue Resultate (vor allem `finished`).

Snapshots von Hand neu rechnen:

```sh
python manage.py ticker_refresh --date 2026-09-27
```

## Lokal entwickeln

Kein InfluxDB nötig: das Demo-Szenario schreibt Snapshots direkt in
`TickerState`, alles dahinter verhält sich identisch.

```sh
# Infrastruktur (eigener Projektname, damit die Volumes isoliert sind)
docker compose -p abst-dev \
  -f .devcontainer/docker-compose.yml \
  -f .devcontainer/docker-compose.ports.yml up -d db redis

export DATABASE_URL=postgres://postgres:postgres@127.0.0.1:5433/postgres
export TICKER_API_TOKEN=devtoken123

python manage.py migrate
python manage.py ticker_demo --seed --step 1   # Demo-Abstimmungstag anlegen
python manage.py runserver 127.0.0.1:8111

# in einer zweiten Shell
export TICKER_API_URL=http://127.0.0.1:8111/api/ticker
export TICKER_API_TOKEN=devtoken123
bin/ticker status
```

`ticker_demo --step 0..5` fährt den Sonntag durch: keine Resultate, erste
Hochrechnung, Städte kommen rein, Entscheidung, Endresultat. Die Werte sind pro
Vorlage deterministisch (Seed auf `vorlagen_id`), Schritte sind wiederholbar.

```sh
python manage.py ticker_demo --next
python manage.py test ticker
```

Aufräumen: `docker compose -p abst-dev -f .devcontainer/docker-compose.yml -f .devcontainer/docker-compose.ports.yml down -v`
