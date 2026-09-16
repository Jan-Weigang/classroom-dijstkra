import json
import os
import secrets
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path

import yaml
from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context


DATA_PATH = Path(__file__).with_name("game_data.yaml")
with DATA_PATH.open(encoding="utf-8") as data_file:
    GAME_CONFIG = yaml.safe_load(data_file)

if not isinstance(GAME_CONFIG, dict) or set(GAME_CONFIG) != {"nodes", "edges"}:
    raise ValueError("game_data.yaml darf oben nur nodes und edges enthalten")
if not isinstance(GAME_CONFIG["nodes"], dict) or not GAME_CONFIG["nodes"]:
    raise ValueError("game_data.yaml: nodes muss mindestens einen Knoten enthalten")
if not isinstance(GAME_CONFIG["edges"], list):
    raise ValueError("game_data.yaml: edges muss eine Liste sein")

NODES = list(GAME_CONFIG["nodes"])
start_nodes = [node for node in NODES if GAME_CONFIG["nodes"][node].get("start")]
goal_nodes = [node for node in NODES if GAME_CONFIG["nodes"][node].get("goal")]
if len(start_nodes) != 1 or len(goal_nodes) != 1:
    raise ValueError("game_data.yaml braucht genau einen Start- und einen Zielknoten")
START_NODE = start_nodes[0]
GOAL_NODE = goal_nodes[0]


def calculate_layout(node_ids, edges, start_node, goal_node):
    """Create a deterministic layered layout using only graph topology."""
    adjacency = {node: [] for node in node_ids}
    for index, edge in enumerate(edges):
        left, right = edge.get("from"), edge.get("to")
        if left not in adjacency or right not in adjacency:
            raise ValueError(
                f"game_data.yaml: Kante {index} verweist auf unbekannten Knoten"
            )
        if left == right:
            raise ValueError(f"game_data.yaml: Kante {index} ist eine Selbstkante")
        adjacency[left].append(right)
        adjacency[right].append(left)

    levels = {start_node: 0}
    queue = deque([start_node])
    while queue:
        node = queue.popleft()
        for neighbour in adjacency[node]:
            if neighbour not in levels:
                levels[neighbour] = levels[node] + 1
                queue.append(neighbour)

    unreachable = [
        node for node in node_ids if node not in levels and node != goal_node
    ]
    furthest = max(
        (level for node, level in levels.items() if node != goal_node), default=0
    )
    for node in unreachable:
        levels[node] = furthest + 1
    levels[goal_node] = max(
        [level for node, level in levels.items() if node != goal_node] or [0]
    ) + 1

    groups = {}
    for node in node_ids:
        groups.setdefault(levels[node], []).append(node)
    max_level = max(groups)
    largest_group = max(len(group) for group in groups.values())
    width = max(720, 180 * max_level + 180)
    height = max(440, 105 * (largest_group + 1))
    positions = {}
    for level, group in groups.items():
        x = 70 if max_level == 0 else 70 + (width - 140) * level / max_level
        for index, node in enumerate(group, start=1):
            positions[node] = {
                "x": round(x),
                "y": round(height * index / (len(group) + 1)),
            }
    return positions, [0, 0, width, height]


def compose_text(*parts):
    return " ".join(part.strip() for part in parts if part and part.strip())


positions, view_box = calculate_layout(
    NODES, GAME_CONFIG["edges"], START_NODE, GOAL_NODE
)
GAME_DATA = {
    "title": "Der letzte Donut",
    "intro": GAME_CONFIG["nodes"][START_NODE].get("intro", ""),
    "startNode": START_NODE,
    "goalNode": GOAL_NODE,
    "viewBox": view_box,
    "order": NODES,
    "nodes": deepcopy(GAME_CONFIG["nodes"]),
    "edges": deepcopy(GAME_CONFIG["edges"]),
}
for node, position in positions.items():
    GAME_DATA["nodes"][node].update(position)

for index, edge in enumerate(GAME_DATA["edges"]):
    edge["id"] = f"e{index}"
    left = GAME_DATA["nodes"][edge["from"]]
    right = GAME_DATA["nodes"][edge["to"]]
    forward_text = compose_text(left["exitText"], right["entryText"])
    backward_text = compose_text(right["exitText"], left["entryText"])
    edge["forward"] = {"text": forward_text, "w": len(forward_text)}
    edge["backward"] = {"text": backward_text, "w": len(backward_text)}

EDGES = {edge["id"]: edge for edge in GAME_DATA["edges"]}
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PLAYER_TTL = 25
TICK_SECONDS = float(os.getenv("GAME_TICK_SECONDS", "0.045"))
BROADCAST_STEPS = max(1, int(os.getenv("GAME_BROADCAST_STEPS", "10")))
GAME_DATA["playback"] = {
    "tickMilliseconds": round(TICK_SECONDS * 1000),
    "broadcastSteps": BROADCAST_STEPS,
}


class GameRoom:
    def __init__(self):
        self.players = {}
        self.board = {
            node: {"dist": None, "prev": None, "by": None} for node in NODES
        }
        self.board[START_NODE]["dist"] = 0
        self.highlight_nodes = set()
        self.phase = "choosing"
        self.step = 0
        self.paused_at_step = 0
        self.last_broadcast_step = 0
        self.version = 1

    def touch(self):
        self.version += 1

    def prune(self):
        cutoff = time.monotonic() - PLAYER_TTL
        before = len(self.players)
        self.players = {
            pid: player
            for pid, player in self.players.items()
            if player["last_seen"] >= cutoff
        }
        changed = len(self.players) != before
        if (
            changed
            and self.phase == "playing"
            and not any(p["status"] == "traversing" for p in self.players.values())
        ):
            self.phase = "choosing"
            self.paused_at_step = self.step
        if changed:
            self.touch()
        return changed

    def join(self, name):
        name = str(name or "").strip()[:24]
        if not name:
            abort(400, "Bitte einen Namen eingeben")
        pid = secrets.token_urlsafe(12)
        self.players[pid] = {
            "name": name,
            "node": START_NODE,
            "cost": 0,
            "status": "waiting",
            "edgeId": None,
            "direction": None,
            "progress": 0,
            "textEdgeId": None,
            "textDirection": None,
            "textProgress": 0,
            "path": [],
            "last_seq": 0,
            "last_seen": time.monotonic(),
        }
        self.touch()
        return pid

    def can_play(self):
        if self.phase != "choosing":
            return False
        active = [
            player
            for player in self.players.values()
            if player["status"] not in ("stopped", "finished")
        ]
        return bool(active) and all(
            player["status"] in ("ready", "traversing") for player in active
        )

    def snapshot(self):
        public_keys = (
            "name",
            "node",
            "cost",
            "status",
            "edgeId",
            "direction",
            "progress",
            "textEdgeId",
            "textDirection",
            "textProgress",
            "path",
        )
        return {
            "board": self.board,
            "players": {
                pid: {key: player[key] for key in public_keys}
                for pid, player in self.players.items()
            },
            "phase": self.phase,
            "canPlay": self.can_play(),
            "step": self.step,
            "pausedAtStep": self.paused_at_step,
            "highlightNodes": sorted(self.highlight_nodes),
        }

    def choose(self, player, edge_id):
        if self.phase != "choosing" or player["status"] not in ("waiting", "ready"):
            abort(409, "Aktuell kann kein Weg gewählt werden")
        edge = EDGES.get(edge_id)
        if not edge or player["node"] not in (edge["from"], edge["to"]):
            abort(400, "Ungültige Kante")
        player["status"] = "ready"
        player["edgeId"] = edge_id
        player["direction"] = (
            "forward" if player["node"] == edge["from"] else "backward"
        )
        player["progress"] = 0

    def play(self):
        if not self.can_play():
            abort(409, "Noch nicht alle wahlberechtigten Schüler haben gewählt")
        self.highlight_nodes.clear()
        for player in self.players.values():
            if player["status"] == "ready":
                player["status"] = "traversing"
                player["textEdgeId"] = player["edgeId"]
                player["textDirection"] = player["direction"]
                player["textProgress"] = 0
        self.phase = "playing"
        self.last_broadcast_step = self.step
        self.touch()

    def tick(self):
        if self.phase != "playing":
            return False

        self.step += 1
        arrivals = []
        for pid, player in self.players.items():
            if player["status"] != "traversing":
                continue
            edge = EDGES[player["edgeId"]]
            weight = edge[player["direction"]]["w"]
            player["progress"] = min(player["progress"] + 1, weight)
            player["textProgress"] = player["progress"]
            if player["progress"] >= weight:
                arrivals.append((pid, player, edge))

        if arrivals:
            self._finish_arrivals(arrivals)
            self.phase = "choosing"
            self.paused_at_step = self.step
            self.last_broadcast_step = self.step
            self.touch()
            return True

        if self.step - self.last_broadcast_step >= BROADCAST_STEPS:
            self.last_broadcast_step = self.step
            self.touch()
            return True
        return False

    def _finish_arrivals(self, arrivals):
        completed = []
        for _, player, edge in arrivals:
            left, right = edge["from"], edge["to"]
            weight = edge[player["direction"]]["w"]
            origin = player["node"]
            destination = right if origin == left else left
            completed.append(
                {
                    "player": player,
                    "edge_id": player["edgeId"],
                    "origin": origin,
                    "destination": destination,
                    "cost": player["cost"] + weight,
                }
            )

        best_by_node = {}
        for item in completed:
            node = item["destination"]
            current = best_by_node.get(node)
            if current is None or item["cost"] < current["cost"]:
                best_by_node[node] = item

        changed_nodes = set()
        for node, best in best_by_node.items():
            known = self.board[node]["dist"]
            if known is None or best["cost"] < known:
                self.board[node] = {
                    "dist": best["cost"],
                    "prev": best["origin"],
                    "by": best["player"]["name"],
                }
                changed_nodes.add(node)

        rejected_nodes = set()
        for item in completed:
            player = item["player"]
            node = item["destination"]
            final_best = self.board[node]["dist"]
            player["path"].append(item["edge_id"])
            player.update(
                node=node,
                cost=item["cost"],
                edgeId=None,
                direction=None,
                progress=0,
            )
            if item["cost"] <= final_best:
                player["status"] = "finished" if node == GOAL_NODE else "waiting"
            else:
                player["status"] = "stopped"
                rejected_nodes.add(node)

        self.highlight_nodes.update(changed_nodes | rejected_nodes)

    def action(self, pid, message):
        player = self.players.get(pid)
        if not player:
            abort(404, "Spieler nicht gefunden")
        player["last_seen"] = time.monotonic()

        seq = message.get("seq")
        if not isinstance(seq, int) or seq <= player["last_seq"]:
            return False
        player["last_seq"] = seq

        action = message.get("t")
        if action == "choose":
            self.choose(player, message.get("edgeId"))
        elif action == "leave":
            self.players.pop(pid, None)
        else:
            abort(400, "Unbekannte Aktion")
        self.touch()
        return True


def create_app(start_ticker=True) -> Flask:
    app = Flask(__name__)
    rooms = {}
    condition = threading.Condition(threading.RLock())
    stop_ticker = threading.Event()

    app.extensions["game_rooms"] = rooms
    app.extensions["game_condition"] = condition
    app.extensions["game_stop_ticker"] = stop_ticker

    def room_or_404(code):
        room = rooms.get(code.upper())
        if room is None:
            abort(404, "Raum nicht gefunden")
        return room

    def ticker():
        while not stop_ticker.wait(TICK_SECONDS):
            with condition:
                changed = False
                for room in rooms.values():
                    changed = room.prune() or changed
                    changed = room.tick() or changed
                if changed:
                    condition.notify_all()

    if start_ticker:
        threading.Thread(target=ticker, name="game-ticker", daemon=True).start()

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/game")
    def game_data():
        return jsonify(GAME_DATA)

    @app.post("/api/rooms")
    def create_room():
        with condition:
            while True:
                code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
                if code not in rooms:
                    break
            rooms[code] = GameRoom()
            return jsonify(room=code)

    @app.post("/api/rooms/<code>/join")
    def join_room(code):
        data = request.get_json(silent=True) or {}
        with condition:
            room = room_or_404(code)
            pid = room.join(data.get("name"))
            condition.notify_all()
            return jsonify(pid=pid, state=room.snapshot())

    @app.get("/api/rooms/<code>/state")
    def get_state(code):
        with condition:
            return jsonify(room_or_404(code).snapshot())

    @app.get("/api/rooms/<code>/events")
    def room_events(code):
        pid = request.args.get("pid", "")

        @stream_with_context
        def stream():
            last_version = -1
            while True:
                room_gone = False
                with condition:
                    room = rooms.get(code.upper())
                    if room is None:
                        room_gone = True
                        chunk = 'event: error\ndata: {"message":"Raum beendet"}\n\n'
                    else:
                        player = room.players.get(pid)
                        if player:
                            player["last_seen"] = time.monotonic()
                        if room.prune():
                            condition.notify_all()
                        if room.version == last_version:
                            condition.wait(timeout=10)
                        if room.version != last_version:
                            last_version = room.version
                            payload = json.dumps(
                                room.snapshot(), ensure_ascii=False, separators=(",", ":")
                            )
                            chunk = f"event: state\ndata: {payload}\n\n"
                        else:
                            chunk = ": keepalive\n\n"
                yield chunk
                if room_gone:
                    return

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/rooms/<code>/action")
    def player_action(code):
        data = request.get_json(silent=True) or {}
        with condition:
            room = room_or_404(code)
            if room.action(data.get("pid"), data):
                condition.notify_all()
            return jsonify(room.snapshot())

    @app.post("/api/rooms/<code>/play")
    def play(code):
        data = request.get_json(silent=True) or {}
        with condition:
            room = room_or_404(code)
            if data.get("pid") not in room.players:
                abort(403)
            room.players[data["pid"]]["last_seen"] = time.monotonic()
            room.play()
            condition.notify_all()
            return jsonify(room.snapshot())

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "0") == "1",
        threaded=True,
        use_reloader=False,
    )
