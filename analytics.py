"""SQLite ledger for normalized gifts and game acknowledgements."""
from contextlib import closing
import json
import sqlite3
import time
from uuid import uuid4


SCHEMA = """
CREATE TABLE IF NOT EXISTS live_sessions (
    id TEXT PRIMARY KEY, room TEXT NOT NULL, username TEXT NOT NULL, started REAL NOT NULL,
    last_seen REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS viewers (
    id TEXT PRIMARY KEY, username TEXT, nickname TEXT, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS viewer_teams (
    session TEXT REFERENCES live_sessions(id), viewer TEXT REFERENCES viewers(id),
    team TEXT NOT NULL, PRIMARY KEY(session, viewer)
);
CREATE INDEX IF NOT EXISTS viewer_teams_by_viewer ON viewer_teams(viewer,session);
CREATE TABLE IF NOT EXISTS gift_events (
    id TEXT PRIMARY KEY, event_key TEXT UNIQUE, session TEXT NOT NULL REFERENCES live_sessions(id),
    viewer TEXT NOT NULL REFERENCES viewers(id), received REAL NOT NULL, gift_id TEXT NOT NULL,
    name TEXT, icon TEXT, quantity INTEGER NOT NULL, diamond_value INTEGER,
    team TEXT, selected_team TEXT, round_id TEXT
);
CREATE INDEX IF NOT EXISTS gifts_by_time ON gift_events(received DESC);
CREATE INDEX IF NOT EXISTS gifts_by_session ON gift_events(session, received DESC);
CREATE INDEX IF NOT EXISTS gifts_by_viewer ON gift_events(viewer, received DESC);
CREATE TABLE IF NOT EXISTS gift_actions (
    gift TEXT PRIMARY KEY REFERENCES gift_events(id), action_id TEXT UNIQUE,
    action TEXT, status TEXT NOT NULL, detail TEXT, round_id TEXT, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS battle_events (
    id TEXT PRIMARY KEY, session TEXT REFERENCES live_sessions(id), recorded REAL NOT NULL,
    map TEXT, winner TEXT, reason TEXT, finisher TEXT
);
"""


class Ledger:
    def __init__(self, path, mode, username):
        self.path = path
        self.mode = mode
        self.username = username.casefold()
        self.current = None
        self.revision = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = self.connect()
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA synchronous=FULL")
        self.writer.executescript(SCHEMA)
        with self.writer:
            self.writer.execute("""UPDATE gift_actions SET status='FAILED',
                detail='Backend đã khởi động lại trước khi có xác nhận', updated=?
                WHERE status IN ('RECEIVED','QUEUED')""", (time.time(),))
        if mode == "mock":
            self.session(uuid4().hex)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def session(self, room):
        room = str(room)
        session = f"{self.username}:{room}" if self.mode == "live" else f"mock:{room}"
        if self.current == session:
            return session
        now = time.time()
        with self.writer:
            self.writer.execute("""INSERT INTO live_sessions VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen""",
                (session, room, self.username, now, now))
        self.current = session
        self.revision += 1
        return session

    def upsert_viewer(self, viewer, username, nickname, now):
        self.writer.execute("""INSERT INTO viewers VALUES(?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET username=coalesce(excluded.username,viewers.username),
            nickname=coalesce(excluded.nickname,viewers.nickname), updated=excluded.updated""",
            (viewer, username, nickname, now))

    def select_team(self, session, viewer, username, nickname, team):
        with self.writer:
            self.upsert_viewer(viewer, username, nickname, time.time())
            self.writer.execute("""INSERT INTO viewer_teams VALUES(?,?,?)
                ON CONFLICT(session,viewer) DO UPDATE SET team=excluded.team""", (session,viewer,team))
        self.revision += 1

    def team(self, session, viewer):
        row = self.writer.execute("SELECT team FROM viewer_teams WHERE session=? AND viewer=?",
                                  (session,viewer)).fetchone()
        return row["team"] if row else None

    def record_gift(self, session, viewer, username, nickname, metadata, count, event_key, team,
                    selected_team, round_id, action):
        key = json.dumps(event_key, ensure_ascii=False, separators=(",",":")) if event_key else None
        if key and self.writer.execute("SELECT 1 FROM gift_events WHERE event_key=?", (key,)).fetchone():
            return None
        ident = uuid4().hex
        now = time.time()
        with self.writer:
            self.upsert_viewer(viewer, username, nickname, now)
            self.writer.execute("INSERT INTO gift_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident,key,session,viewer,now,str(metadata["id"]),metadata.get("name"),metadata.get("icon"),
                 count,metadata.get("diamonds"),team,selected_team,round_id))
            self.writer.execute("INSERT INTO gift_actions VALUES(?,?,?,'RECEIVED',NULL,NULL,?)",
                                (ident,None,action,now))
        self.revision += 1
        return ident

    def action(self, gift, action_id, status, detail=None, round_id=None):
        with self.writer:
            self.writer.execute("""UPDATE gift_actions SET action_id=coalesce(?,action_id),
                status=?, detail=?, round_id=coalesce(?,round_id), updated=? WHERE gift=?""",
                (action_id,status,detail,round_id,time.time(),gift))
        self.revision += 1

    def battle(self, state, message):
        if not state.get("round_id"):
            return
        with self.writer:
            self.writer.execute("INSERT OR IGNORE INTO battle_events VALUES(?,?,?,?,?,?,?)",
                (state["round_id"],self.current,time.time(),state.get("map"),message.get("winner"),
                 message.get("reason"),message.get("finisher")))
        self.revision += 1

    def close(self):
        self.writer.close()

    def report(self, session=None, start=None, end=None, team=None, search="", sort="quantity",
               viewer=None, page=0, sender_page=0, export=None):
        where, params = [], []
        for value, clause in [(session,"g.session=?"),(start,"g.received>=?"),(end,"g.received<?"),
                              (viewer,"g.viewer=?")]:
            if value is not None:
                where.append(clause)
                params.append(value)
        if team:
            where.append("coalesce(t.team,g.selected_team,'none')=?")
            params.append(team)
        if search:
            where.append("instr(lower(coalesce(v.username,'')),lower(?))>0")
            params.append(search)
        source = """FROM gift_events g JOIN viewers v ON v.id=g.viewer
            JOIN gift_actions a ON a.gift=g.id
            LEFT JOIN viewer_teams t ON t.session=g.session AND t.viewer=g.viewer"""
        if where:
            source += " WHERE " + " AND ".join(where)
        total = "sum(g.quantity * g.diamond_value)"
        fields = f"""g.viewer, v.username, v.nickname,
            coalesce(t.team,g.selected_team,'none') team,
            sum(g.quantity) quantity,{total} diamonds,
            sum(CASE WHEN g.diamond_value IS NULL THEN g.quantity ELSE 0 END) unknown,
            max(g.received) last_sent"""
        order = "diamonds DESC, quantity DESC" if sort == "diamonds" else "quantity DESC, diamonds DESC"
        senders = f"SELECT {fields} {source} GROUP BY g.viewer ORDER BY {order},g.viewer"
        events = f"""SELECT g.id,g.event_key,g.received,g.session,g.viewer,v.username,v.nickname,
            g.gift_id,g.name,g.icon,g.quantity,g.diamond_value,g.diamond_value*g.quantity diamonds,
            g.team,g.selected_team,a.action,a.action_id,a.status,a.detail,coalesce(a.round_id,g.round_id) round_id
            {source} ORDER BY g.received DESC,g.id DESC"""
        with closing(self.connect()) as db:
            db.execute("BEGIN")
            def query(sql, extra=()):
                return [dict(row) for row in db.execute(sql, (*params,*extra))]
            if export == "senders":
                return query(senders)
            if export == "gifts":
                return query(events)
            if export == "sessions":
                return query(f"""SELECT g.session,min(g.received) first_gift,max(g.received) last_gift,
                    count(*) events,sum(g.quantity) quantity,{total} diamonds,
                    sum(CASE WHEN g.diamond_value IS NULL THEN g.quantity ELSE 0 END) unknown,
                    count(DISTINCT g.viewer) viewers,
                    sum(a.status='EXECUTED') executed,sum(a.status='FAILED' OR a.status='REJECTED') failed
                    {source} GROUP BY g.session ORDER BY first_gift DESC""")
            overview = query(f"""SELECT count(*) events,coalesce(sum(g.quantity),0) quantity,{total} diamonds,
                count(DISTINCT g.viewer) viewers,
                coalesce(sum(CASE WHEN g.diamond_value IS NULL THEN g.quantity ELSE 0 END),0) unknown,
                coalesce(sum(CASE WHEN g.team='cat' THEN g.quantity ELSE 0 END),0) cat,
                coalesce(sum(CASE WHEN g.team='enemy' THEN g.quantity ELSE 0 END),0) enemy,
                count(CASE WHEN a.status='EXECUTED' THEN 1 END) executed,
                count(CASE WHEN a.status IN ('RECEIVED','QUEUED') THEN 1 END) pending,
                count(CASE WHEN a.status IN ('FAILED','REJECTED') THEN 1 END) failed
                {source}""")[0]
            span = query(f"SELECT coalesce(max(g.received)-min(g.received),0) span {source}")[0]["span"]
            bucket = max(60, int((span / 80 + 59) // 60) * 60)
            series = query(f"""SELECT cast(g.received/{bucket} AS INTEGER)*{bucket} time,
                sum(g.quantity) quantity,{total} diamonds {source} GROUP BY time ORDER BY time""")
            skills = query(f"""SELECT a.action label,count(*) value {source}
                {'AND' if where else 'WHERE'} a.status='EXECUTED' AND a.action<>'spawn'
                GROUP BY a.action ORDER BY value DESC""")
            gifts = query(f"""SELECT coalesce(max(g.name),g.gift_id) label,sum(g.quantity) value {source}
                GROUP BY g.gift_id ORDER BY value DESC LIMIT 8""")
            rankings = {}
            for name, value in [("quantity","sum(g.quantity)"),("diamonds",total),
                ("cat","sum(CASE WHEN g.team='cat' THEN g.quantity END)"),
                ("enemy","sum(CASE WHEN g.team='enemy' THEN g.quantity END)"),
                ("finish","sum(CASE WHEN a.status='EXECUTED' AND a.action IN ('finish_battle_cat','finish_battle_enemy') THEN 1 END)")]:
                rankings[name] = query(f"""SELECT v.username,v.nickname,{value} value {source}
                    GROUP BY g.viewer HAVING value IS NOT NULL ORDER BY value DESC,g.viewer LIMIT 8""")
            sessions = [dict(row) for row in db.execute(
                "SELECT id,room,username,started FROM live_sessions ORDER BY started DESC")]
            return {"overview":overview,"senders":query(senders+" LIMIT 25 OFFSET ?", (sender_page*25,)),
                    "events":query(events+" LIMIT 50 OFFSET ?", (page*50,)), "series":series,
                    "skills":skills,"gifts":gifts,"rankings":rankings,"sessions":sessions}
