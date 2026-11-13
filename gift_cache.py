"""Shared gift catalog and validated local image cache."""
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("pml")
MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 1024
IMAGE_NAME = re.compile(r"[1-9][0-9]{0,15}\.(png|webp|jpg|gif)")
FORMATS = {"PNG":"png","WEBP":"webp","JPEG":"jpg","GIF":"gif"}
CDN_DOMAINS = ("tiktokcdn.com", "tiktokcdn-us.com", "tiktokcdn-eu.com", "byteoversea.com")


def official_url(value):
    if not isinstance(value,str) or len(value) > 2048:
        return None
    try:
        url = urlsplit(value)
        if (url.scheme == "https" and not url.username and url.port in (None,443) and url.hostname
                and any(url.hostname.endswith("." + domain) for domain in CDN_DOMAINS)):
            return value
    except ValueError:
        pass
    return None


def atomic_write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_image(directory, ident, data):
    if len(data) > MAX_BYTES:
        raise ValueError("Icon exceeds byte limit")
    with Image.open(BytesIO(data)) as image:
        suffix = FORMATS.get(image.format)
        if not suffix or max(image.size) > MAX_SIDE or getattr(image,"n_frames",1) > 60:
            raise ValueError("Unsupported image format, dimensions or frame count")
        # Re-encode a decoded first frame into a static image, stripping extraneous metadata.
        image.load()
        frame = image.convert("RGB" if suffix == "jpg" else "RGBA")
        output = BytesIO()
        frame.save(output,format=image.format)
    if output.tell() > MAX_BYTES:
        raise ValueError("Decoded icon exceeds byte limit")
    name = f"{ident}.{suffix}"
    if not IMAGE_NAME.fullmatch(name):
        raise ValueError("Invalid gift ID")
    atomic_write(directory/name,output.getvalue())
    return name


class GiftCache:
    def __init__(self, data_dir, icon_dir=None):
        self.path = data_dir / "gift_catalog_cache.json"
        self.icons = icon_dir or ROOT/"assets"/"gifts"
        self.catalog = {}
        self.files = {}
        self.retry = {}
        self.scope = None
        try:
            payload = json.loads(self.path.read_text("utf-8"))
            self.catalog = {int(row["id"]):row for row in payload["gifts"]}
            self.files = {int(ident):row for ident,row in payload["icons"].items()}
            self.scope = payload.get("scope")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            LOG.warning("Không đọc được cache catalog quà %s",self.path)
        self.available_files = {ident for ident,entry in self.files.items()
                                if self.image_path(entry.get("file"))}
        self.revision = 0

    def image_path(self, name):
        if isinstance(name,str) and IMAGE_NAME.fullmatch(name):
            path = self.icons / name
            if path.is_file():
                return path
        return None

    def icon_url(self, ident):
        try:
            ident = int(ident)
        except (TypeError,ValueError):
            return None
        entry = self.files.get(ident,{})
        name = entry.get("file")
        if ident in self.available_files:
            return f"/assets/gifts/{name}?v={entry.get('version',0)}"
        return None

    def set_catalog(self, rows, scope):
        now = datetime.now(timezone.utc).isoformat()
        self.catalog = {ident:{**row,"updated_at":now} for ident,row in rows.items()}
        self.scope = scope

    async def persist(self):
        now = datetime.now(timezone.utc).isoformat()
        snapshot = {"updated_at":now,"scope":self.scope,"icons":self.files,
                    "gifts":[{**row,"updated_at":row.get("updated_at",now),"verification":"catalog",
                              "icon_path":self.icon_url(ident),
                              "icon_status":self.files.get(ident,{}).get("status","not_selected")}
                             for ident,row in self.catalog.items()]}
        payload = json.dumps(snapshot,ensure_ascii=False,indent=2).encode()
        await asyncio.to_thread(atomic_write,self.path,payload)

    async def sync_icons(self, rows):
        downloaded = reused = failed = 0
        async with httpx.AsyncClient(timeout=10,follow_redirects=False) as http:
            for ident,row in rows.items():
                url = official_url(row.get("icon"))
                previous = self.files.get(ident,{})
                # CDN query parameters may rotate independently of an image's resource path.
                source = [row.get("image_uri"),urlsplit(url).path] if url else None
                saved_source = previous.get("source")
                if isinstance(saved_source,str) and source and saved_source == (source[0] or source[1]):
                    saved_source = source
                if url and source == saved_source and self.image_path(previous.get("file")):
                    if previous.get("status") != "ready" or ident not in self.available_files or previous.get("source") != source:
                        self.files[ident] = {**previous,"source":source,"status":"ready"}
                        self.available_files.add(ident)
                        self.revision += 1
                    reused += 1
                    continue
                retry = self.retry.get(ident)
                if retry and retry[0] == source and retry[1] > time.monotonic():
                    failed += 1
                    continue
                try:
                    if not url:
                        raise ValueError("Catalog has no trusted image URL")
                    # TikTokLive 7.0.1 fetch_image_data buffers the whole response.
                    # Stream the same validated catalog URL to enforce the cap before decoding.
                    async with asyncio.timeout(15), http.stream("GET",url) as response:
                        response.raise_for_status()
                        length = response.headers.get("Content-Length")
                        if length and int(length) > MAX_BYTES:
                            raise ValueError("Icon exceeds byte limit")
                        data = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            data.extend(chunk)
                            if len(data) > MAX_BYTES:
                                raise ValueError("Icon exceeds byte limit")
                    name = await asyncio.to_thread(save_image,self.icons,ident,bytes(data))
                    self.files[ident] = {"file":name,"source":source,"status":"ready","version":time.time_ns()}
                    self.available_files.add(ident)
                    self.retry.pop(ident,None)
                    self.revision += 1
                    downloaded += 1
                except (OSError, ValueError, httpx.HTTPError, TimeoutError, Image.DecompressionBombError) as exc:
                    self.retry[ident] = (source,time.monotonic()+300)
                    status = "stale" if self.image_path(previous.get("file")) else "missing"
                    self.files[ident] = {**previous,"status":status}
                    self.revision += 1
                    failed += 1
                    LOG.warning("Không tải được icon quà %s (%s); %s",ident,type(exc).__name__,status)
        return {"downloaded":downloaded,"reused":reused,"failed":failed}


def resolve_mapping(rows, catalog):
    """Resolve enabled rows without changing the user's mapping file."""
    resolved, report = {}, {}
    names = {}
    for item in catalog.values():
        if item.get("name"):
            names.setdefault(item["name"].strip().casefold(),[]).append(item)
    for row in rows:
        if not row["enabled"]:
            continue
        key = row.get("mock_alias") or f'{row["action"]}:{row.get("team")}:{row.get("unit")}'
        ident, name, price = row.get("gift_id"), row.get("gift_name"), row.get("coin_price")
        entry = report[key] = {"name":name,"expected_coins":price}
        candidates = [catalog[ident]] if ident in catalog else names.get(name.strip().casefold(),[]) if name and not ident else []
        if not candidates:
            entry["status"] = "missing" if ident or name else "unconfigured"
            # Explicit legacy IDs remain usable while the catalog is unavailable.
            if ident and price is None:
                resolved[key] = {**row,"gift_id":ident}
            continue
        if len(candidates) != 1 or (not ident and price is None):
            entry["status"] = "confirmation_required"
            continue
        gift = candidates[0]
        entry.update(id=gift["id"],name=gift.get("name"),catalog_coins=gift.get("coins"))
        if price is not None and gift.get("coins") != price:
            entry["status"] = "price_mismatch" if gift.get("coins") is not None else "price_unknown"
        elif gift.get("available") is False:
            entry["status"] = "unavailable"
        else:
            entry["status"] = "resolved"
            resolved[key] = {**row,"gift_id":gift["id"]}
    pairs = {}
    for row in rows:
        if row["enabled"] and row.get("price_pair"):
            pairs.setdefault(row["price_pair"],[]).append(row["mock_alias"])
    for keys in pairs.values():
        entries = [report[key] for key in keys]
        prices = [entry.get("catalog_coins") for entry in entries]
        if len(keys) != 2 or any(key not in resolved for key in keys) or None in prices or prices[0] != prices[1]:
            for key in keys:
                if resolved.pop(key,None):
                    report[key]["status"] = "pair_needs_review"
    duplicates = {}
    for key,row in resolved.items():
        duplicates.setdefault(row["gift_id"],[]).append(key)
    for keys in duplicates.values():
        if len(keys) > 1:
            for key in keys:
                resolved.pop(key)
                report[key]["status"] = "confirmation_required"
    # If a duplicate invalidated one side, hold its partner too.
    for keys in pairs.values():
        if any(key not in resolved for key in keys):
            for key in keys:
                if resolved.pop(key,None):
                    report[key]["status"] = "pair_needs_review"
    return {row["gift_id"]:row for row in resolved.values()}, report
