# Der letzte Donut – Flask-App

Eine kleine Flask-App für das gemeinsame Dijkstra-Spiel. Ein Schüler erstellt
einen Raum und teilt den Code, alle anderen treten bei. Es gibt keine gesonderte
Lehrkraftansicht.

Flask verwaltet Räume, Texte, Spielzustand und den gemeinsamen Step-Counter.
Graph und Texte liefert `/api/game`. Flask zählt intern jedes Zeichen, sendet
den laufenden Zustand aber nur alle zehn Schritte (etwa 2,2-mal pro Sekunde)
sowie sofort bei Play, Ankunft und Pause. Der Browser verteilt jedes empfangene
Zeichenpaket gleichmäßig über den Servertakt, damit der Text flüssig erscheint;
der verbindliche Fortschritt bleibt trotzdem vollständig auf dem Server. Nur
Wegwahl und Play gehen als normale HTTP-Anfragen an Flask. Es gibt keine externen
Broker oder JavaScript-Abhängigkeiten.

## Spielablauf

1. Alle wahlberechtigten Schüler wählen einen Weg.
2. Ein beliebiger verbundener Schüler drückt `Play`.
3. Der Server erhöht für alle Wege denselben Step-Counter. Die Browser zeigen
   lediglich den vom Server gemeldeten Textausschnitt.
4. Sobald ein Weg einen Knoten erreicht, pausiert Flask alle Texte atomar und
   sendet den exakten `pausedAtStep` sowie jeden Wegfortschritt per SSE.
5. Ein neuer Bestwert kommt in die Tabelle und darf weiterlaufen. Ein gleich
   guter Weg darf ebenfalls weiterlaufen; nur ein tatsächlich längerer Weg
   scheidet aus: Jemand anderes war schneller, der Donut ist weg.
6. Nur neu angekommene Schüler wählen erneut; bereits laufende Wege bleiben an
   ihrer Textposition stehen. Danach kann wieder jeder `Play` drücken.

Neue Bestwerte und Knoten, an denen ein längerer Weg ausscheidet, werden in der
Tabelle rot markiert. Die Markierung bleibt bis zum nächsten `Play` sichtbar.
Die Tabelle nennt außerdem die Person, die den aktuellen Bestwert eingetragen
hat. Wird der Wert später unterboten, wird auch dieser Name ersetzt.

Vor dem Erstellen oder Betreten eines Raums ist ein Name erforderlich. Flask
prüft dies zusätzlich auf dem Server, sodass sich die Vorgabe nicht durch einen
direkten API-Aufruf umgehen lässt.

## Starten

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Danach auf dem Server-Rechner `http://127.0.0.1:5000` öffnen. Die App lauscht
standardmäßig auf allen Netzwerkschnittstellen. Die lokale IP findest du unter
Fedora mit:

```bash
hostname -I
```

Auf den Schüler-Geräten dann diese IP verwenden, zum Beispiel
`http://192.168.1.23:5000`. Alle Geräte müssen im selben erreichbaren Netzwerk
sein.

Auf Fedora muss der Port gegebenenfalls in der Firewall freigegeben werden:

```bash
sudo firewall-cmd --add-port=5000/tcp
sudo firewall-cmd --runtime-to-permanent
```

## Routen

- `/` – Anwendung
- `/health` – einfacher Statuscheck
- `/api/game` – serverseitige Graph-, Text- und Gewichtsdaten
- `/api/rooms/...` – interne Raum- und Spielschnittstelle

Die Räume liegen im Arbeitsspeicher. Ein Neustart des Flask-Prozesses beendet
laufende Spiele. Für diese kleine App bitte nur einen Gunicorn-Worker verwenden,
da mehrere Prozesse keinen gemeinsamen Arbeitsspeicher teilen.

## Eigenes Netz definieren

Das gesamte Netz liegt in `game_data.yaml`. Auf der obersten Ebene sind nur
`nodes` und `edges` erlaubt:

- `nodes` enthält Namen und Kurztexte für Tabelle und Graph (`name`, `short`,
  `blurb`); genau ein Knoten bekommt `start: true` (der zusätzlich ein
  `intro` für den Startbildschirm trägt), genau einer `goal: true`.
- `edges` verbindet jeweils `from` und `to` und trägt den gesamten
  abgespielten Text direkt: `forwardText` für den Weg von `from` nach `to`,
  `backwardText` für die Gegenrichtung. Feste Layoutangaben gibt es nicht.

Die Reihenfolge der Knoten in der YAML bestimmt die stabile Reihenfolge in der
Tabelle. Die Koordinaten berechnet Flask bei jedem Serverstart automatisch mit
einem deterministischen Feder-Layout (Fruchterman-Reingold) allein aus der
Graphtopologie – es gibt keine Ebenen und keine feste Verankerung von Start
oder Ziel, Kanten dürfen also frei zwischen beliebigen Knoten verlaufen.

Kanten-IDs werden beim Serverstart automatisch aus der Reihenfolge erzeugt;
das Gewicht je Richtung ist ganz regulär die Länge von `forwardText`
beziehungsweise `backwardText`. Beide Felder sind pro Kante vollständig
eigenständig – anders als in einer früheren Version, in der sich der Text
aus Ausgangstext des einen Knotens, einem optionalen Kantenzusatz und
Eingangstext des anderen Knotens zusammensetzte. Das führte zu zwei
Problemen: Formulierungen aus dem Knotentext tauchten zwangsläufig auf jeder
Kante an diesem Knoten wieder auf (Dopplungen), und ein Umweg über einen
Zwischenknoten konnte rechnerisch nie günstiger sein als eine direkte Kante
zum selben Ziel, weil er zusätzlich die (nicht negative) Textlänge des
Zwischenknotens trug – ein Graph mit echter Dijkstra-Relaxation kam ohne
Workaround nicht aus. Mit eigenständigem Text pro Kante und Richtung
entfällt beides: jede Kante ist frei wählbar, unabhängig von jeder anderen.
Damit lassen sich ohne Änderungen an HTML oder Python andere ungerichtete
Netze aufbauen. Nach Änderungen an der YAML-Datei Flask neu starten.

Die Aktualisierungsrate lässt sich optional ändern:

```bash
GAME_BROADCAST_STEPS=10 GAME_TICK_SECONDS=0.045 python app.py
```

## Produktion

Optional mit Gunicorn starten. SSE benötigt genügend Threads; gleichzeitig
muss wegen des In-Memory-Zustands genau ein Worker verwendet werden:

```bash
pip install gunicorn
gunicorn --worker-class gthread --workers 1 --threads 64 -b 0.0.0.0:5000 app:app
```
