"""Local LIVE/WebSocket server and mock terminal."""
import argparse
import asyncio
from collections import deque
from contextlib import asynccontextmanager, nullcontext
import json
import logging
import os
from pathlib import Path
import shlex
import socket
import webbrowser
import threading
import time
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from analytics import Ledger
from dashboard import dashboard_app
from gift_cache import GiftCache, resolve_mapping

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("pml")
PORT = 8080
DASHBOARD_PORT = 8081
UNITS = {"cat": {"basic", "tank", "archer", "ninja", "mecha"},
         "enemy": {"slime", "hog", "turtle", "crow", "boar"}}
FINISH_TEAMS = {"finish_battle_cat": "cat", "finish_battle_enemy": "enemy"}
TIERS = {"common": 0, "mid": 1, "high": 2}
MAX_PENDING = 64
ACTION_LIFETIME = 45
CHEER_WINDOW = 5
CHAT_TEAMS = {"!meow": "cat", "1": "cat", "!monster": "enemy", "2": "enemy"}
TEAM_SWITCH_COOLDOWN = 10


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Expected integer in [{minimum}, {maximum}]")
    return value


def valid_action(row):
    action, team, unit = row["action"], row.get("team"), row.get("unit")
    if action in FINISH_TEAMS:
        return FINISH_TEAMS[action] == team and unit is None and row.get("tier") == "high"
    if action == "spawn":
        return unit in UNITS.get(team, ())
    if action in ("heal_base", "buff_team"):
        return team in UNITS and unit is None
    if action in ("fire_cat_cannon", "fire_enemy_cannon"):
        return team == action.split("_")[1] and unit is None
    return action == "change_map" and team is None and unit is None


def load_mapping(path):
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or len(rows) > 250:
        raise ValueError("Expected up to 250 mapping rows")
    aliases, ids, finishers = set(), set(), set()
    for row in rows:
        if not valid_action(row):
            raise ValueError(f"Invalid action/team/unit in {row}")
        gift_id = row.get("gift_id")
        if gift_id is not None:
            integer(gift_id, 1, 2**53 - 1)
        alias = row.get("mock_alias", "")
        if not isinstance(alias, str) or len(alias) > 48 or (alias and alias in aliases):
            raise ValueError(f"Invalid or duplicate mock_alias {alias!r}")
        if alias:
            aliases.add(alias)
        if type(row.get("enabled")) is not bool:
            raise ValueError("enabled must be a boolean")
        if gift_id and row["enabled"]:
            if gift_id in ids:
                raise ValueError(f"Duplicate enabled gift ID {gift_id}")
            ids.add(gift_id)
        if row.get("gift_name") is not None and (
                not isinstance(row["gift_name"],str) or not row["gift_name"].strip()):
            raise ValueError("gift_name must be a non-empty string")
        if row.get("live_label") is not None and (
                not isinstance(row["live_label"],str) or len(row["live_label"]) > 24):
            raise ValueError("live_label must be a short string")
        if row.get("coin_price") is not None:
            integer(row["coin_price"],0,10**9)
        if row.get("price_pair") is not None and (
                not isinstance(row["price_pair"],str) or not row["price_pair"] or not alias):
            raise ValueError("price_pair requires a non-empty string and a mock_alias")
        if row["enabled"] and row["action"] in FINISH_TEAMS:
            if row["action"] in finishers:
                raise ValueError(f"Duplicate enabled finisher {row['action']}")
            finishers.add(row["action"])
        integer(row["quantity"], 1, {"spawn": 16, "heal_base": 30, "buff_team": 25}.get(row["action"], 1))
        integer(row["cooldown"], 0, 300)
        integer(row["priority"], -10, 10)
        if row["tier"] not in TIERS or not isinstance(row["display_label"], str) or len(row["display_label"]) > 64:
            raise ValueError("Invalid tier or display_label")
    return rows


class Pipeline:
    def __init__(self, mode, username, settings, ledger=None, gift_cache=None):
        self.ledger = ledger
        self.gift_cache = gift_cache
        self.live_mapping = {}
        self.mapping_report = {}
        self.icon_report = {}
        self.catalog_changed = asyncio.Event()
        self.room_id = None
        self.mode = mode
        self.username = username
        self.settings = settings
        self.connection = "mock" if mode == "mock" else "unconfigured"
        self.rows = []
        self.catalog = {}
        self.observed = {}
        self.catalog_current = False
        safe_user = "".join(c for c in username.lower() if c.isascii() and (c.isalnum() or c in "._"))
        legacy_cache = ROOT / "data" / f"catalog-{safe_user}.json"
        self.revision = 0
        self.pending = {}
        self.history = deque(maxlen=50)
        self.results = deque(maxlen=50)
        self.notices = deque(maxlen=24)
        self.seen = {}
        self.viewer_teams = {}
        self.team_notices = deque(maxlen=8)
        self.cheer_buckets = {team: deque() for team in UNITS}
        self.owner = None
        self.socket = None
        self.owner_last = 0
        self.state = {}
        self.catalog_errors = 0
        self.drop_socket = False
        if mode == "live" and gift_cache:
            self.catalog = gift_cache.catalog
            if not self.catalog and username:
                try:
                    self.catalog = {row["id"]:row for row in json.loads(legacy_cache.read_text("utf-8"))}
                except FileNotFoundError:
                    pass
                except (OSError,ValueError,TypeError,KeyError):
                    LOG.warning("Không đọc được catalog cũ")
        elif mode == "mock":
            self.catalog = {row["id"]: row for row in json.loads(
                (ROOT / "config" / "mock-catalog.json").read_text(encoding="utf-8-sig"))}

    def change_connection(self, status):
        if self.connection != status:
            LOG.info("TikTok status: %s", status)
            self.connection = status
            self.revision += 1
        if status != "connected" and self.catalog_current:
            self.catalog_current = False
            self.revision += 1

    def set_catalog(self, catalog, scope=None):
        # A full refresh supersedes metadata from the previous response.
        self.catalog = catalog
        self.observed.clear()
        self.catalog_current = scope is not None
        if self.gift_cache:
            self.gift_cache.set_catalog(catalog,scope)
            self.resolve_gifts()
        self.revision += 1
        LOG.info("Catalog TikTok: %d quà (%s)",len(catalog),scope or "danh mục công khai")

    def resolve_gifts(self):
        mapping, report = resolve_mapping(self.rows,self.catalog)
        if report != self.mapping_report:
            for key,item in report.items():
                if item["status"] != "resolved":
                    LOG.warning("Gift mapping %s: %s",key,item)
            self.mapping_report = report
        self.live_mapping = mapping

    async def sync_gift_icons(self):
        if not self.gift_cache:
            return
        rows = {ident:self.observed.get(ident) or self.catalog[ident] for ident in self.live_mapping
                if ident in self.observed or ident in self.catalog}
        self.icon_report = await self.gift_cache.sync_icons(rows)
        try:
            if self.gift_cache.catalog:
                await self.gift_cache.persist()
        except OSError:
            LOG.exception("Failed to write gift catalog cache")
        self.revision += 1
        LOG.info("Icon quà: %s",self.icon_report)

    def config_message(self):
        rows = []
        mappings = self.rows if self.mode == "mock" or not self.gift_cache else self.live_mapping.values()
        for row in mappings:
            ident = row.get("gift_id")
            metadata = (self.catalog.get(row.get("mock_alias")) if self.mode == "mock" else
                        self.observed.get(ident) or self.catalog.get(ident))
            if row["enabled"] and metadata:
                finisher = row["action"] in FINISH_TEAMS
                if finisher and not metadata.get("name"):
                    continue
                verified = (not finisher or self.mode == "mock" or ident in self.observed or
                            self.catalog_current or bool(self.gift_cache and ident in self.gift_cache.catalog))
                rows.append({**metadata, **{key: row.get(key) for key in
                             ("action", "team", "unit", "quantity", "tier", "display_label")},
                             "icon":self.gift_cache.icon_url(ident) if self.mode == "live" and self.gift_cache else None,
                             "live_label":row.get("live_label") if self.mode == "live" else None,
                             "priority": row["priority"], "verified":verified})
        rows.sort(key=lambda row: (TIERS[row["tier"]], -row["priority"], str(row["id"])))
        status = "missing"
        if self.mode == "mock":
            status = "mock"
        elif self.catalog_current:
            status = "verified"
        elif self.catalog:
            status = "cached"
        elif self.observed:
            status = "observed"
        return {"type": "config", "settings": self.settings, "connection": self.connection,
                "catalog": {"status": status, "rows": rows}}

    def choose_team(self, viewer, content, sender, event_key=None, username=None, nickname=None):
        team = CHAT_TEAMS.get(content.strip().casefold())
        if team is None or self.is_duplicate(event_key):
            return
        now = time.monotonic()
        previous = self.viewer_teams.get(viewer)
        if previous:
            previous_team, selected_at = previous
            if previous_team == team or now - selected_at < TEAM_SWITCH_COOLDOWN:
                return
            self.viewer_teams.pop(viewer)
        self.viewer_teams[viewer] = (team, now)
        if len(self.viewer_teams) > 20_000:
            self.viewer_teams.pop(next(iter(self.viewer_teams)))
        if self.ledger:
            session = self.ledger.session(viewer[0]) if isinstance(viewer, tuple) else self.ledger.current
            identity = viewer[1] if isinstance(viewer, tuple) else viewer
            self.ledger.select_team(session, identity, username or (identity if self.mode == "mock" else None),
                                    nickname, team)
        self.team_notices.append((now, sender, team))
        LOG.info("CHAT_TEAM sender=%r team=%s", sender, team)

    def add_likes(self, viewer, count, event_key=None):
        selection = self.viewer_teams.get(viewer)
        team = selection[0] if selection else None
        if team is None or count <= 0 or self.is_duplicate(event_key):
            return
        second = int(time.monotonic())
        buckets = self.cheer_buckets[team]
        if not buckets or buckets[-1][0] != second:
            self.cheer_rate(team, second)
            buckets.append([second, 0])
        buckets[-1][1] = min(100, buckets[-1][1] + count)

    def cheer_rate(self, team, second=None):
        if second is None:
            second = int(time.monotonic())
        buckets = self.cheer_buckets[team]
        while buckets and second - buckets[0][0] >= CHEER_WINDOW:
            buckets.popleft()
        return sum(count for _, count in buckets) / CHEER_WINDOW

    def cheer_rates(self):
        return {team: self.cheer_rate(team) for team in UNITS}

    def is_duplicate(self, key):
        if not key:
            return False
        now = time.monotonic()
        if self.seen.get(key, 0) > now:
            return True
        self.seen[key] = now + 600
        if len(self.seen) > 8192:
            self.seen.pop(next(iter(self.seen)))
        return False

    def notice(self, text):
        self.notices.append(str(text)[:170])

    def enqueue(self, row, count, sender, gift_id=None, gift_name=None, ledger_id=None):
        if len(self.pending) >= MAX_PENDING:
            LOG.warning("QUEUE_FULL sender=%r gift=%r action=%s", sender, gift_name, row["action"])
            self.notice(f"{sender} · Hàng đợi quà đã đầy")
            if ledger_id:
                self.ledger.action(ledger_id, None, "REJECTED", "Hàng đợi quà đã đầy")
            return
        kind = row["action"]
        now = time.time()
        action = {
            "id": uuid4().hex, "kind": kind, "team": row.get("team"), "unit": row.get("unit"),
            "quantity": min(100_000, count * row["quantity"]) if kind == "spawn" else row["quantity"],
            "cooldown": row.get("cooldown", 0), "priority": 0 if kind == "change_map" else row.get("priority", 0),
            "sender": sender, "gift_id": gift_id, "gift_name": gift_name, "repeat": count,
            "mapping_id": str(gift_id) if gift_id is not None else None,
            "expires_at": round((now + ACTION_LIFETIME) * 1000),
        }
        if kind in FINISH_TEAMS:
            action["round_id"] = self.state.get("round_id") if self.socket is not None else None
            if not action["round_id"]:
                detail = "Không có trận đang nhận quà kết liễu"
                self.finish({"action": action, "ledger_id": ledger_id}, "rejected", detail)
                self.notice(f"{sender} · {detail}")
                return
        self.pending[action["id"]] = {"action": action, "owner": None,
                                      "sent_at": 0, "accepted": False, "until": now + ACTION_LIFETIME,
                                      "ledger_id": ledger_id}
        if ledger_id:
            self.ledger.action(ledger_id, action["id"], "QUEUED", round_id=action.get("round_id"))
        LOG.info("QUEUED id=%s sender=%r gift=%r/%r x%d action=%s team=%s queue=%d",
                 action["id"], sender, gift_id, gift_name, count, kind, action["team"], len(self.pending))
        return action["id"]

    def gift(self, metadata, count, sender, *, source, event_key=None, streaking=False,
             room=None, viewer=None, username=None, nickname=None):
        gift_id = metadata["id"]
        if source == "live":
            existing = self.observed.get(gift_id) or self.catalog.get(gift_id, {})
            observed = {**existing, **{key: value for key, value in metadata.items() if value is not None}}
            if observed != existing:
                self.observed[gift_id] = observed
                self.revision += 1
                if self.gift_cache and gift_id in self.live_mapping:
                    self.catalog_changed.set()
        if streaking:
            LOG.info("STREAK sender=%r gift=%r x%d (in progress)", sender, gift_id, count)
            return
        if self.is_duplicate(event_key):
            LOG.info("DUPLICATE sender=%r gift=%r x%d", sender, gift_id, count)
            return
        key = "mock_alias" if source == "mock" else "gift_id"
        row = (self.live_mapping.get(gift_id) if source == "live" and self.gift_cache else
               next((r for r in self.rows if r["enabled"] and r.get(key) == gift_id),None))
        if row and source == "live" and self.gift_cache and row.get("coin_price") is not None:
            # A reported cost change holds the affected pair until mapping is reviewed.
            observed_price = metadata.get("coins")
            if observed_price is not None and observed_price != row["coin_price"]:
                self.catalog[gift_id] = {**self.catalog.get(gift_id,{}),**observed}
                self.gift_cache.catalog[gift_id] = self.catalog[gift_id]
                self.resolve_gifts()
                self.catalog_changed.set()
                self.revision += 1
                row = None
        count = max(1, count)
        ledger_id = None
        if self.ledger:
            session = self.ledger.session(room) if source == "live" else self.ledger.current
            identity = viewer or ("mock" if source == "mock" else "anonymous:" + uuid4().hex)
            selection = self.ledger.team(session, identity)
            value = metadata.get("diamonds")
            if value is None and (self.catalog_current or source == "mock"):
                value = self.catalog.get(gift_id, {}).get("diamonds")
            saved_metadata = dict(self.catalog.get(gift_id, {}))
            saved_metadata.update({key: value for key, value in metadata.items() if value is not None})
            saved_metadata["diamonds"] = value if type(value) is int and 0 <= value <= 10**9 else None
            ledger_id = self.ledger.record_gift(session, identity, username or (identity if source == "mock" else None),
                nickname, saved_metadata, count, event_key, row.get("team") if row else None,
                selection, self.state.get("round_id") if self.socket is not None else None,
                row["action"] if row else None)
            if ledger_id is None:
                return
        if not row:
            if ledger_id:
                self.ledger.action(ledger_id, None, "REJECTED", "Quà chưa được ghép hoặc đã tắt")
            LOG.info("UNMAPPED sender=%r gift=%r/%r x%d", sender, gift_id, metadata.get("name"), count)
            self.notice(f"{sender} · Quà chưa được ghép hành động")
            return
        self.enqueue(row, count, sender, gift_id, metadata.get("name"), ledger_id)

    def finish(self, record, status, detail):
        action = record["action"]
        if record.get("ledger_id"):
            outcome = {"executed":"EXECUTED", "rejected":"REJECTED"}.get(status,"FAILED")
            if status == "partial":
                detail = "Thực thi một phần · " + detail
            self.ledger.action(record["ledger_id"], action["id"], outcome, detail, record.get("round_id"))
        self.pending.pop(action["id"], None)
        self.history.append({"id": action["id"], "status": status, "detail": detail,
                             "action": action["kind"], "sender": action["sender"]})
        LOG.info("ACK id=%s sender=%r action=%s team=%s status=%s detail=%r queue=%d",
                 action["id"], action["sender"], action["kind"], action["team"], status, detail, len(self.pending))

    def message(self, player, message):
        kind = message.get("type")
        if kind == "ack":
            record = self.pending.get(message.get("id"))
            if record is None or record["owner"] != player:
                return
            record["round_id"] = message.get("round_id") or record.get("round_id")
            status, detail = message.get("status"), str(message.get("detail", ""))[:170]
            if message.get("final") is True and status in ("executed", "partial", "rejected"):
                self.finish(record, status, detail)
            elif status == "queued":
                if not record["accepted"]:
                    LOG.info("ACK queued id=%s detail=%r", message["id"], detail)
                    if record["action"]["kind"] == "change_map":
                        record["until"] = float("inf")
                record["accepted"] = True
        elif kind in ("state", "result") and isinstance(message.get("state"), dict):
            state = message["state"]
            if kind == "result":
                if self.ledger:
                    self.ledger.battle(state, message)
                if any(record["round_id"] == state.get("round_id") for record in self.results):
                    return
                self.results.append({"round_id": state.get("round_id"), "map": state.get("map"),
                                     "winner": message.get("winner"), "reason": message.get("reason"),
                                     "finisher": message.get("finisher")})
                LOG.info("RESULT map=%r winner=%r reason=%r wins=%r", state.get("map"),
                         message.get("winner"), message.get("reason"), state.get("wins"))
            elif self.state.get("map") != state.get("map") or state.get("time") == 0:
                LOG.info("ROUND map=%r", state.get("map"))
            audio = state.get("audio")
            if audio != self.state.get("audio"):
                if audio in ("suspended","interrupted"):
                    LOG.warning("Audio %s: focus the game source and press A, or use /?audio=setup",audio)
                elif audio in ("running","disabled","unavailable","closed"):
                    LOG.info("Game audio: %s",audio)
            fps = state.get("fps")
            if isinstance(fps, (int, float)) and fps < 48:
                LOG.warning("Low FPS: %s (cat=%s enemy=%s queued=%d)", fps, state.get("cat_units"),
                            state.get("enemy_units"), len(self.pending))
            self.state = state

    def expire(self):
        now = time.time()
        for record in list(self.pending.values()):
            if record["until"] <= now:
                self.finish(record, "expired", "Hết thời gian chờ xác nhận")
                self.notice(f'{record["action"]["sender"]} · Hết thời gian chờ xác nhận')

    def health(self):
        return {"mode": self.mode, "connection": self.connection, "game_connected": self.socket is not None,
                "queue": len(self.pending), "likes_per_second": self.cheer_rates(), "catalog": self.config_message()["catalog"]["status"],
                "catalog_count": len(self.catalog.keys() | self.observed.keys()), "game": self.state, "recent": list(self.history), "results": list(self.results)}


async def watch_mapping(pipe, path):
    previous = None
    previous_error = None
    while True:
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp != previous:
                previous = stamp
                pipe.rows = load_mapping(path)
                if pipe.mode == "live" and pipe.gift_cache:
                    pipe.resolve_gifts()
                    pipe.catalog_changed.set()
                pipe.revision += 1
                previous_error = None
                LOG.info("Gift mapping loaded: %d rows, %d enabled LIVE IDs", len(pipe.rows),
                         sum(bool(r.get("gift_id")) and r["enabled"] for r in pipe.rows))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if str(exc) != previous_error:
                previous_error = str(exc)
                LOG.warning("Invalid gift mapping; keeping previous valid configuration: %s", exc)
        pipe.expire()
        await asyncio.sleep(1)


def mock_command(pipe, command):
    try:
        parts = shlex.split(command)
        if not parts:
            return
        nickname = parts[4] if len(parts) == 5 else None
        parts = [part.lower() for part in parts]
        if parts == ["status"]:
            LOG.info("STATUS %s", json.dumps(pipe.health(), ensure_ascii=False))
        elif len(parts) == 3 and parts[0] == "chat":
            _, viewer, content = parts
            pipe.choose_team(viewer, content, viewer)
        elif len(parts) == 3 and parts[0] == "like":
            _, viewer, count = parts
            pipe.add_likes(viewer, integer(int(count), 1, 1000))
        elif parts[0] in ("gift", "streak") and 2 <= len(parts) <= 5:
            gift_id = parts[1]
            metadata = pipe.catalog.get(gift_id, {"id": gift_id, "name": gift_id})
            count = integer(int(parts[2]), 1, 1000) if len(parts) >= 3 else 1
            viewer = parts[3] if len(parts) >= 4 else "mock"
            identity = {"viewer":viewer, "username":viewer, "nickname":nickname}
            sender = nickname or (viewer if viewer != "mock" else "Mock")
            if parts[0] == "streak":
                key = ("mock", uuid4().hex)
                pipe.gift(metadata, max(1, count-1), sender, source="mock", event_key=key, streaking=True, **identity)
                for _ in range(2):
                    pipe.gift(metadata, count, sender, source="mock", event_key=key, **identity)
            else:
                pipe.gift(metadata, count, sender, source="mock", **identity)
        elif len(parts) == 4 and parts[0] == "spawn":
            _, team, unit, qty = parts
            row = {"action": "spawn", "team": team, "unit": unit, "quantity": 1}
            if not valid_action(row):
                raise ValueError("Unknown unit/team")
            pipe.enqueue(row, integer(int(qty), 1, 1000), "Mock")
        elif len(parts) == 2 and parts[0] == "cannon" and parts[1] in UNITS:
            pipe.enqueue({"action": f"fire_{parts[1]}_cannon", "team": parts[1], "quantity": 1}, 1, "Mock")
        elif len(parts) == 2 and parts[0] == "finish" and parts[1] in UNITS:
            gift_id = f"finish-{parts[1]}"
            pipe.gift(pipe.catalog[gift_id], 1, "Mock", source="mock")
        elif parts == ["change-map"]:
            pipe.gift(pipe.catalog["map"], 1, "Mock", source="mock")
        elif parts == ["restart"]:
            pipe.enqueue({"action": "restart", "quantity": 1}, 1, "Mock")
        elif len(parts) == 2 and parts[0] == "connection" and parts[1] in (
                "unconfigured", "connecting", "connected", "disconnected", "offline", "mock"):
            pipe.change_connection(parts[1])
        elif parts == ["drop-ws"] and pipe.socket:
            pipe.drop_socket = True
        else:
            LOG.info("Mock: chat viewer !meow | like viewer 30 | gift rose 10 | streak rose 4 | spawn cat basic 5 | spawn enemy boar 1 | "
                     "cannon cat | cannon enemy | finish cat | finish enemy | change-map | status | restart | connection disconnected | drop-ws")
    except ValueError as exc:
        LOG.warning("Invalid mock command: %s", exc)


def terminal(pipe, loop):
    try:
        while True:
            loop.call_soon_threadsafe(mock_command, pipe, input())
    except (EOFError, OSError, RuntimeError):
        pass


def create_app(pipe, interactive=False, mapping_path=ROOT / "config" / "gifts.json"):
    @asynccontextmanager
    async def lifespan(app):
        tasks = [asyncio.create_task(watch_mapping(pipe, mapping_path))]
        if pipe.mode == "live" and pipe.gift_cache and not pipe.username:
            from tiktok_listener import public_catalog
            tasks.append(asyncio.create_task(public_catalog(pipe)))
        if pipe.mode == "live" and pipe.username:
            from tiktok_listener import listen
            tasks.append(asyncio.create_task(listen(pipe)))
        elif pipe.mode == "live":
            LOG.warning("TIKTOK_USERNAME is empty. Auto battle active; fill .env and restart for TikTok gifts.")
        if interactive and pipe.mode == "mock":
            LOG.info("Type 'help' for mock commands")
            threading.Thread(target=terminal, args=(pipe, asyncio.get_running_loop()), daemon=True).start()
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if pipe.ledger:
                for record in list(pipe.pending.values()):
                    if record.get("ledger_id"):
                        pipe.finish(record, "unconfirmed", "Backend đã dừng trước khi có xác nhận")

    app = FastAPI(lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")

    @app.get("/")
    @app.get("/game")
    async def game():
        return FileResponse(ROOT / "index.html")

    @app.get("/health")
    async def health():
        return pipe.health()

    @app.get("/catalog")
    async def catalog():
        return {"status": pipe.config_message()["catalog"]["status"],
                "scope":pipe.gift_cache.scope if pipe.gift_cache else None,
                "mapping":pipe.mapping_report,"icon_sync":pipe.icon_report,
                "gifts":[{**row,"icon_path":pipe.gift_cache.icon_url(ident) if pipe.gift_cache else None}
                         for ident,row in {**pipe.catalog,**pipe.observed}.items()]}

    @app.websocket("/ws")
    async def websocket(socket: WebSocket):
        host = socket.headers.get("host")
        player = socket.query_params.get("player", "")
        origin = socket.headers.get("origin")
        if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}") or origin != f"http://{host}" or len(player) != 36:
            await socket.close(code=1008)
            return
        await socket.accept()
        role, revision = None, -1
        next_cheers = 0
        try:
            while True:
                now = time.monotonic()
                if pipe.socket is None and (pipe.owner in (None, player) or now - pipe.owner_last > 5):
                    if pipe.owner != player:
                        # An action delivered to a destroyed browser tab may already have executed.
                        # Do not reapply it to a new battle instance.
                        for record in list(pipe.pending.values()):
                            if record["owner"] is not None and record["owner"] != player:
                                pipe.finish(record, "unconfirmed", "Nguồn cũ đã mất; không phát lại")
                    if pipe.owner != player:
                        pipe.state = {}
                    pipe.socket, pipe.owner = socket, player
                active = pipe.socket is socket
                if role != active:
                    role = active
                    revision = -1
                    await socket.send_json({"type": "owner" if active else "standby"})
                    LOG.info("Game WebSocket %s", "active" if active else "standby")
                if pipe.revision != revision:
                    revision = pipe.revision
                    await socket.send_json(pipe.config_message())
                if active:
                    if now >= next_cheers:
                        next_cheers = now + .5
                        await socket.send_json({"type": "cheers", "rates": pipe.cheer_rates()})
                    if pipe.drop_socket:
                        pipe.drop_socket = False
                        await socket.close(code=1012)
                        return
                    while pipe.team_notices:
                        selected_at, sender, team = pipe.team_notices.popleft()
                        if now - selected_at <= 5:
                            await socket.send_json({"type": "team_selected", "sender": sender, "team": team})
                    while pipe.notices:
                        await socket.send_json({"type": "notice", "text": pipe.notices.popleft()})
                    eligible = [r for r in pipe.pending.values() if now-r["sent_at"] >= 2 and
                                (not r["accepted"] or r["sent_at"] < 0)]
                    def dispatch_order(record):
                        action = record["action"]
                        if action["kind"] in FINISH_TEAMS:
                            return (0, 0, 0)
                        return (1, not record["accepted"], -action["priority"])

                    ordered = sorted(eligible, key=dispatch_order)
                    for record in ordered[:4]:
                        record["owner"], record["sent_at"] = player, now
                        await socket.send_json({"type": "action", "action": record["action"]})
                try:
                    message = await asyncio.wait_for(socket.receive_json(), timeout=.08)
                except TimeoutError:
                    continue
                if active and isinstance(message, dict):
                    pipe.message(player, message)
        except (WebSocketDisconnect, RuntimeError, ValueError, TypeError, OSError):
            pass
        finally:
            if pipe.socket is socket:
                pipe.socket = None
                pipe.owner_last = time.monotonic()
                for record in pipe.pending.values():
                    if record["owner"] == player:
                        record["sent_at"] = -10
                LOG.info("Game WebSocket disconnected; waiting for reconnect")

    return app


def env_flag(key, default):
    return os.getenv(key, default).strip().lower() in ("true", "1", "yes", "on")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("mock", "live"))
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    username = os.getenv("TIKTOK_USERNAME", "").strip().lstrip("@")
    if username and (len(username) > 32 or any(not (c.isascii() and (c.isalnum() or c in "._")) for c in username)):
        parser.error("TIKTOK_USERNAME must be a TikTok username")
    audio_defaults = {"master":1,"combat":.95,"spawn":1,"skill":.9,"battle":.95,"gift":.85,"ambient":.35}
    try:
        audio_mix = {group:float(os.getenv(f"GAME_VOLUME_{group.upper()}",default))
                     for group,default in audio_defaults.items()}
        if not all(0 <= value <= 1 for value in audio_mix.values()):
            raise ValueError
    except ValueError:
        parser.error("GAME_VOLUME_* must be numbers between 0 and 1")
    settings = {"audio": env_flag("GAME_AUDIO", "true"), "audio_mix":audio_mix,
                "auto_cannon":env_flag("GAME_AUTO_CANNON","true"), "vfx": env_flag("GAME_VFX", "true")}
    listeners = []
    try:
        for port in (PORT, DASHBOARD_PORT):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listeners.append(listener)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", port))
                listener.listen()
            except OSError as exc:
                parser.exit(1, f"Cannot start: port {port} on 127.0.0.1 is unavailable ({exc}).\n")
        data_dir = Path(os.getenv("PML_DATA_DIR", ROOT / "data"))
        ledger = Ledger(data_dir / f"analytics-{args.mode}.sqlite3", args.mode, username)
        try:
            pipe = Pipeline(args.mode, username, settings, ledger, GiftCache(data_dir))
            asyncio.run(serve_both(pipe, listeners, args.open_browser))
        except KeyboardInterrupt:
            pass
        finally:
            ledger.close()
    finally:
        for listener in listeners:
            listener.close()


class LocalServer(uvicorn.Server):
    def capture_signals(self):
        # The shared runner owns process signals for both listeners.
        return nullcontext()


async def serve_both(pipe, listeners, open_browser):
    game = LocalServer(uvicorn.Config(create_app(pipe, interactive=True), host="127.0.0.1", port=PORT,
        ws_max_size=16_384, access_log=False, timeout_graceful_shutdown=3))
    admin = LocalServer(uvicorn.Config(dashboard_app(pipe, DASHBOARD_PORT), host="127.0.0.1",
        port=DASHBOARD_PORT, access_log=False, timeout_graceful_shutdown=3))
    with uvicorn.Server.capture_signals(game):
        tasks = [asyncio.create_task(service.serve([listener]))
                 for service,listener in zip((game,admin),listeners)]
        try:
            while not (game.started and admin.started) and not any(task.done() for task in tasks):
                await asyncio.sleep(.05)
            if game.started and admin.started:
                LOG.info("Game http://127.0.0.1:%d/ | Dashboard http://127.0.0.1:%d/",PORT,DASHBOARD_PORT)
                setup = "?audio=setup" if pipe.mode == "mock" and pipe.settings["audio"] else ""
                game_url = f"http://127.0.0.1:{PORT}/{setup}"
                if setup:
                    LOG.info("Mock audio setup: %s (click the audio button if prompted)", game_url)
                if open_browser:
                    def open_pages():
                        webbrowser.open(game_url)
                        if env_flag("OPEN_DASHBOARD", "false"):
                            webbrowser.open(f"http://127.0.0.1:{DASHBOARD_PORT}/", new=2)
                    threading.Thread(target=open_pages, daemon=True).start()
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            game.should_exit = admin.should_exit = True
            await asyncio.gather(*tasks)


if __name__ == "__main__":
    main()
