"""Offline regression checks for catalog matching, image validation and cache fallback."""
import asyncio
from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from PIL import Image, UnidentifiedImageError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gift_cache import GiftCache, resolve_mapping, save_image

URL = "https://p16-webcast.tiktokcdn.com/gift.webp"


async def verify(directory):
    rows = [
        {"enabled":True,"mock_alias":"cat","gift_name":"Rose","coin_price":1,"price_pair":"basic"},
        {"enabled":True,"mock_alias":"enemy","gift_name":"Ice Cream","coin_price":1,"price_pair":"basic"},
    ]
    catalog = {
        1:{"id":1,"name":"Rose","coins":1,"icon":URL},
        2:{"id":2,"name":"Ice Cream","coins":1,"icon":URL},
    }
    assert set(resolve_mapping(rows,catalog)[0]) == {1,2}
    catalog[3] = {"id":3,"name":"ROSE","coins":2}
    mapping, report = resolve_mapping(rows,catalog)
    assert not mapping and report["cat"]["status"] == "confirmation_required"
    del catalog[3]
    catalog[1]["coins"] = 5
    mapping, report = resolve_mapping(rows,catalog)
    assert not mapping and report["cat"]["status"] == "price_mismatch"
    assert report["enemy"]["status"] == "pair_needs_review"
    catalog[1]["coins"] = 1

    image = BytesIO()
    Image.new("RGBA",(16,16),"pink").save(image,"WEBP")
    calls = []
    original_client = httpx.AsyncClient

    def response(request):
        calls.append(str(request.url))
        if request.url.path == "/changed.webp":
            return httpx.Response(503)
        return httpx.Response(200,content=image.getvalue())

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(response),**kwargs)

    path = Path(directory)
    with patch("gift_cache.httpx.AsyncClient", client):
        cache = GiftCache(path,path/"icons")
        cache.set_catalog(catalog,None)
        assert (await cache.sync_icons(catalog))["downloaded"] == 2
        await cache.persist()
        cache = GiftCache(path,path/"icons")
        assert (await cache.sync_icons(catalog))["reused"] == 2
        assert len(calls) == 2
        cached_url = cache.icon_url(1)
        catalog[1]["icon"] = URL.replace("gift.webp","changed.webp")
        assert (await cache.sync_icons(catalog))["failed"] == 1
        assert cache.icon_url(1) == cached_url and cache.files[1]["status"] == "stale"
        catalog[2]["icon"] = "https://tiktokcdn.com.example.org/icon.webp"
        assert (await cache.sync_icons(catalog))["failed"] == 2
        assert len(calls) == 3
        assert cache.image_path("../1.webp") is None
        try:
            save_image(cache.icons,3,b"<html>not an icon</html>")
        except UnidentifiedImageError:
            pass
        else:
            raise AssertionError("Accepted HTML as an icon")
        assert not list(cache.icons.glob("3.*"))


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        asyncio.run(verify(directory))
    print("PASS: unique name and price, balanced pairs, cache restart, stale fallback, CDN and image validation")
