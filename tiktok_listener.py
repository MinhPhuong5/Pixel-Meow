"""TikTokLive 7 catalog and gift adapter. The game can run independently of TikTok."""
import asyncio
from contextlib import suppress
import logging
import time
from gift_cache import official_url

from TikTokLive import TikTokLiveClient
from TikTokLive.client.errors import UserOfflineError
from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, GiftEvent, LikeEvent

LOG = logging.getLogger("pml")


def field(value, *keys):
    for key in keys:
        item = value.get(key) if isinstance(value, dict) else getattr(value, key, None)
        if item is not None and item != "":
            return item
    return None


def number(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) < 20:
        return int(value)
    return None


def secure_icon(value):
    urls = [value] if isinstance(value, str) else field(value, "url_list", "urlList") or []
    return next((url for url in urls if official_url(url)),None)


def gift_metadata(gift, gift_id=None):
    ident = gift_id or number(field(gift, "id", "gift_id", "giftId"))
    if not ident or not 0 < ident < 2**53:
        return None
    name = field(gift, "name", "gift_name", "giftName")
    kind = number(field(gift, "type", "gift_type", "giftType"))
    repeatable = field(gift, "streakable")
    image = field(gift, "icon", "icon_url", "iconUrl")
    icon = secure_icon(image)
    if not icon:
        image = field(gift,"image")
        icon = secure_icon(image)
    diamonds = number(field(gift, "diamond_count", "diamondCount"))
    # Proto3 exposes an omitted scalar as zero. JSON catalogs can distinguish an explicit zero.
    if diamonds == 0 and not isinstance(gift, dict):
        diamonds = None
    price = number(field(gift,"coin_price","coin_count","coinPrice"))
    if price is None:
        # TikTok exposes the gift denomination as diamond_count.
        price = diamonds
    available = field(gift,"available","is_available")
    locked = field(field(gift,"lock_info"),"lock")
    expires = number(field(gift,"expiration_timestamp"))
    if expires and expires > 10**11:
        expires /= 1000
    if locked is True or (expires and expires < time.time()):
        available = False
    return {"id": ident, "name": name.strip() if isinstance(name, str) and name.strip() else None,
            "coins":price,"available":available if type(available) is bool else None,
            "image_uri":field(image,"uri"),
            "image_width":number(field(image,"width")), "image_height":number(field(image,"height")),
            "icon": icon, "diamonds": diamonds,
            "gift_type": kind, "streakable": repeatable if type(repeatable) is bool else
            (kind == 1 if kind is not None else None)}


def normalize_catalog(payload):
    # TikTok's gift/list data has appeared as a gifts list and as nested gift panels.
    result = {}
    stack = [payload]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(reversed(value))
        elif isinstance(value, dict):
            row = gift_metadata(value)
            if row and row["name"] and any(key in value for key in
                                            ("diamond_count", "diamondCount", "image", "icon", "gift_type")):
                previous = result.get(row["id"], {})
                result[row["id"]] = {**previous, **{k: v for k, v in row.items() if v is not None}}
            else:
                stack.extend(reversed(list(value.values())))
    return result


def ingest_event(pipe, room_id, event):
    metadata = gift_metadata(event.gift, number(event.gift_id))
    if metadata is None:
        LOG.warning("TikTok gift received without an ID")
        return
    user = event.user
    sender = str(field(user, "nickname", "unique_id", "display_id") or "Người xem")[:48]
    sender = " ".join(sender.split()) or "Người xem"
    user_id = field(user, "id", "sec_uid", "unique_id", "display_id")
    streakable = metadata["streakable"]
    if streakable is None:
        known = pipe.observed.get(metadata["id"]) or pipe.catalog.get(metadata["id"], {})
        streakable = known.get("streakable")
    # A reconnect may replay the last final event with a new message ID but the same group ID.
    group = number(event.group_id)
    msg = number(field(event.common, "msg_id"))
    log = field(event, "log_id")
    key = None
    if not room_id:
        LOG.warning("TikTok gift received without a room ID")
    if streakable and group and user_id:
        key = (str(room_id), "group", str(user_id), metadata["id"], group)
    elif msg:
        key = (str(room_id), "message", msg)
    elif log:
        key = (str(room_id), "log", log, metadata["id"])
    pipe.gift(metadata, max(1, event.repeat_count or 1), sender, source="live", event_key=key,
              room=room_id, viewer=str(user_id) if user_id else None,
              username=field(user, "unique_id", "display_id"), nickname=field(user, "nickname"),
              streaking=streakable is True and not event.repeat_end)


def ingest_audience(pipe, room_id, event):
    viewer = field(event.user, "id", "sec_uid", "unique_id", "display_id")
    if not viewer:
        return
    identity = (str(room_id), str(viewer))
    msg = number(field(event.common, "msg_id"))
    key = (*identity, event.get_type(), msg) if msg else None
    if isinstance(event, CommentEvent):
        sender = str(field(event.user, "nickname", "unique_id", "display_id") or "Người xem")
        pipe.choose_team(identity, event.content, " ".join(sender.split())[:48], key,
                         field(event.user,"unique_id","display_id"), field(event.user,"nickname"))
    else:
        pipe.add_likes(identity, event.count, key)


async def refresh_catalog(pipe, client, scope=None, once=False):
    while True:
        delay = 300
        try:
            payload = await asyncio.wait_for(client.web.fetch_gift_list(), timeout=12)
            catalog = normalize_catalog(payload)
            if not catalog:
                raise ValueError("Empty or unsupported gift catalog shape")
            if scope is not None:
                panel_ids = set()
                for page in payload.get("pages", []):
                    if not isinstance(page,dict) or page.get("page_type") != 1:
                        continue
                    panel_ids.update(number(field(gift,"id","gift_id")) if isinstance(gift,dict) else number(gift)
                                     for gift in page.get("gifts",[]))
                if panel_ids & catalog.keys():
                    for ident,row in catalog.items():
                        if row["available"] is None:
                            row["available"] = ident in panel_ids
            pipe.set_catalog(catalog,scope)
            pipe.catalog_errors = 0
        except Exception as exc:
            pipe.catalog_errors += 1
            if pipe.catalog_current:
                pipe.catalog_current = False
                pipe.revision += 1
            LOG.warning("Không lấy được catalog TikTok (%s); dùng cache gần nhất",type(exc).__name__)
            delay = min(300, 15 * 2 ** min(5,pipe.catalog_errors-1))
        await pipe.sync_gift_icons()
        if once:
            return
        try:
            await asyncio.wait_for(pipe.catalog_changed.wait(), timeout=delay)
            pipe.catalog_changed.clear()
            await pipe.sync_gift_icons()
        except TimeoutError:
            pass


async def public_catalog(pipe):
    client = TikTokLiveClient(unique_id=pipe.username or "tiktok")
    try:
        await refresh_catalog(pipe,client)
    finally:
        await client.web.close()


async def listen(pipe):
    LOG.info("TikTok username @%s (TikTokLive anonymous signer)", pipe.username)
    backoff = 3
    public_checked = False
    while True:
        pipe.change_connection("connecting")
        client = TikTokLiveClient(unique_id=pipe.username)
        catalog_task = None
        if not public_checked and pipe.gift_cache:
            public_checked = True
            startup = refresh_catalog(pipe,client,once=True) if not pipe.catalog else pipe.sync_gift_icons()
            catalog_task = asyncio.create_task(startup)
        started = time.monotonic()

        @client.on(ConnectEvent)
        async def connected(event):
            nonlocal catalog_task
            pipe.room_id = str(event.room_id)
            if pipe.ledger:
                pipe.ledger.session(event.room_id)
            pipe.change_connection("connected")
            startup_task = catalog_task

            async def room_catalog():
                if startup_task:
                    await startup_task
                await refresh_catalog(pipe,client,str(event.room_id))

            catalog_task = asyncio.create_task(room_catalog())

        @client.on(DisconnectEvent)
        async def disconnected(event):
            pipe.change_connection("disconnected")

        @client.on(CommentEvent)
        @client.on(LikeEvent)
        async def audience(event):
            ingest_audience(pipe, client.room_id, event)

        @client.on(GiftEvent)
        async def gift(event):
            try:
                ingest_event(pipe, client.room_id, event)
            except (AttributeError, TypeError, ValueError) as exc:
                LOG.warning("TikTok gift payload not supported (%s)", type(exc).__name__)

        try:
            # Fetch catalog in its own task so an unavailable gift-list endpoint does not block gifts.
            connection = await asyncio.wait_for(client.start(process_connect_events=False), timeout=35)
            await connection
            pipe.change_connection("disconnected")
        except UserOfflineError:
            pipe.change_connection("offline")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            pipe.change_connection("disconnected")
            response = getattr(exc, "response", None)
            LOG.warning("TikTok connection unavailable: %s (HTTP %s), retrying",
                        type(exc).__name__, getattr(response, "status_code", "-"))
        finally:
            if catalog_task is not None:
                catalog_task.cancel()
                with suppress(asyncio.CancelledError):
                    await catalog_task
            if not pipe.catalog:
                public_checked = False
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(client.disconnect(), timeout=8)
            # TikTokLive 7.0.1 client.close() calls run_until_complete internally.
            # Close its HTTP transport directly when running inside FastAPI's event loop.
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(client.web.close(), timeout=8)

        if time.monotonic() - started > 60:
            backoff = 3
        await asyncio.sleep(15 if pipe.connection == "offline" else backoff)
        backoff = min(60, backoff * 2)
