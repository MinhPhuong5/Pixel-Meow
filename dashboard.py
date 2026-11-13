"""Read-only localhost analytics listener."""
import asyncio
import csv
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

DASHBOARD_TZ = timezone(timedelta(hours=7))
ASSETS = Path(__file__).resolve().parent / "admin"
EXPORTS = {
    "senders": "viewer username nickname team quantity diamonds unknown last_sent",
    "gifts": "id event_key received session viewer username nickname gift_id name icon quantity diamond_value diamonds team selected_team action action_id status detail round_id",
    "sessions": "session first_gift last_gift events quantity diamonds unknown viewers executed failed",
}


def csv_cell(value):
    if not isinstance(value, str):
        return value
    if value.startswith(("\t","\r","\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def dashboard_app(pipe, port):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    ledger = pipe.ledger

    @app.middleware("http")
    async def local_only(request, call_next):
        host = request.headers.get("host")
        origin = request.headers.get("origin")
        if (host not in {f"127.0.0.1:{port}", f"localhost:{port}"} or
            (origin and origin != f"http://{host}") or
            request.headers.get("sec-fetch-site") in {"cross-site","same-site"} or
            request.method not in {"GET","HEAD"}):
            return Response(status_code=403)
        response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "Cross-Origin-Resource-Policy": "same-origin",
        })
        return response

    @app.get("/")
    async def index():
        return FileResponse(ASSETS / "index.html")

    @app.get("/static/{name}")
    async def static(name: str):
        if name not in {"dashboard.css","dashboard.js"}:
            raise HTTPException(404)
        return FileResponse(ASSETS / name)

    @app.get("/assets/gifts/{name}")
    async def gift_icon(name: str):
        path = pipe.gift_cache.image_path(name) if pipe.gift_cache else None
        if not path:
            raise HTTPException(404)
        return FileResponse(path)

    def options(request):
        q = request.query_params
        scope = q.get("scope","current")
        session = q.get("session") or None
        if not session and scope == "current":
            session = ledger.current or "_none"
        start = end = None
        try:
            if q.get("date") or scope == "today":
                day = date.fromisoformat(q["date"]) if q.get("date") else datetime.now(DASHBOARD_TZ).date()
                start = datetime.combine(day,datetime.min.time(),DASHBOARD_TZ).timestamp()
                end = datetime.combine(day+timedelta(days=1),datetime.min.time(),DASHBOARD_TZ).timestamp()
            page = min(1_000_000,max(0,int(q.get("page",0))))
            sender_page = min(1_000_000,max(0,int(q.get("sender_page",0))))
        except (ValueError, OverflowError):
            raise HTTPException(400,"Bộ lọc không hợp lệ") from None
        return dict(session=session,start=start,end=end,team=q.get("team") or None,
                    search=q.get("search","")[:100],sort=q.get("sort","quantity"),
                    viewer=q.get("viewer") or None,page=page,sender_page=sender_page)

    @app.get("/api/report")
    async def report(request: Request):
        query = options(request)
        revision = ledger.revision
        data = await asyncio.to_thread(ledger.report, **query)
        for row in data["events"]:
            row["icon"] = pipe.gift_cache.icon_url(row["gift_id"]) if pipe.gift_cache and pipe.mode == "live" else None
        return {**data, "current":ledger.current,"mode":pipe.mode,"connection":pipe.connection,"revision":revision}

    @app.get("/api/updates")
    async def updates(request: Request):
        async def stream():
            previous = None
            while not await request.is_disconnected():
                revision = (ledger.revision, pipe.connection, pipe.gift_cache.revision if pipe.gift_cache else 0)
                if revision != previous:
                    previous = revision
                    yield f"data: {revision[0]}\n\n"
                else:
                    yield "event: heartbeat\ndata: ok\n\n"
                await asyncio.sleep(.4)
        return StreamingResponse(stream(),media_type="text/event-stream")

    @app.get("/api/export/{kind}")
    async def export(kind: str, request: Request):
        if kind not in EXPORTS:
            raise HTTPException(404)
        rows = await asyncio.to_thread(ledger.report, **options(request), export=kind)
        output = StringIO(newline="")
        headers = EXPORTS[kind].split()
        writer = csv.writer(output)
        writer.writerow(headers)
        for row in rows:
            cells = []
            for key in headers:
                value = row.get(key)
                if key in {"received","last_sent","first_gift","last_gift"} and value is not None:
                    value = datetime.fromtimestamp(value,DASHBOARD_TZ).isoformat(timespec="seconds")
                cells.append(csv_cell(value))
            writer.writerow(cells)
        return Response("\ufeff"+output.getvalue(),media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":f'attachment; filename="pixel-meow-{kind}.csv"'})

    return app
