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

- `nodes` enthält Namen, Kurztexte sowie `entryText` und `exitText`.
- Genau ein Knoten bekommt `start: true`, genau einer `goal: true`.
- `edges` verbindet jeweils `from` und `to`; ein optionales `weight` überschreibt
  das sonst aus dem Text berechnete Gewicht (siehe unten). Feste
  Layoutangaben gibt es nicht.

Die Reihenfolge der Knoten in der YAML bestimmt die stabile Reihenfolge in der
Tabelle. Die Koordinaten berechnet Flask bei jedem Serverstart automatisch mit
einem deterministischen Feder-Layout (Fruchterman-Reingold) allein aus der
Graphtopologie – es gibt keine Ebenen und keine feste Verankerung von Start
oder Ziel, Kanten dürfen also frei zwischen beliebigen Knoten verlaufen.

Für einen Weg von A nach B setzt Flask den abgespielten Text aus `exitText` von A
und `entryText` von B zusammen. In Gegenrichtung verwendet es entsprechend den
Ausgangstext von B und den Eingangstext von A. Kanten-IDs werden beim
Serverstart automatisch aus der Reihenfolge erzeugt; das Gewicht je Richtung
ist standardmäßig die Textlänge, kann aber pro Kante mit dem optionalen Feld
`weight` überschrieben werden. Das ist mehr als nur Komfort: Sobald ein Knoten
sowohl eine direkte Kante als auch einen Umweg über einen Nachbarn zum selben
Ziel hat, kann der Umweg unter reiner Textlängen-Gewichtung nie günstiger sein
als die direkte Kante (der Umweg trägt zusätzlich noch die – nicht negative –
Textlänge des Zwischenknotens). Ein Graph, der eine echte Dijkstra-Relaxation
zeigen soll, braucht also so gut wie immer `weight`. Die Anzeige läuft davon
unabhängig: Der Fließtext wird über die gesamte Kantendauer hinweg proportional
eingeblendet, unabhängig davon, wie lang der Text im Verhältnis zum Gewicht
ist – kurzer Text auf einer teuren Kante wird langsam getippt, langer Text auf
einer günstigen Kante endet trotzdem exakt bei der Ankunft. Damit lassen sich
ohne Änderungen an HTML oder Python andere ungerichtete Netze aufbauen. Nach
Änderungen an der YAML-Datei Flask neu starten.

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
