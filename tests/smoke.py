"""Backend + Edge smoke test. Run with python -m pip install playwright."""
import asyncio
from dataclasses import dataclass
import hashlib
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.request import urlopen

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8080"
sys.path.insert(0, str(ROOT))
from server import Pipeline, create_app, load_mapping
from fastapi.testclient import TestClient
from tiktok_listener import ingest_audience, ingest_event, normalize_catalog
from TikTokLive.events import CommentEvent, LikeEvent


def health():
    with urlopen(URL + "/health", timeout=1) as response:
        return json.load(response)


async def until(predicate, seconds=10):
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if result := predicate():
            return result
        await asyncio.sleep(.075)
    raise AssertionError("Condition timed out")


async def test_edge(process):
    def command(text):
        assert process.poll() is None
        process.stdin.write(text + "\n")
        process.stdin.flush()

    def ack(action, status, mark=0):
        return lambda: next((r for r in health()["recent"][mark:]
                             if r["action"] == action and r["status"] == status), None)

    def ready():
        try:
            return health()["mode"] == "mock"
        except OSError:
            return False

    await until(ready)
    with tempfile.TemporaryDirectory() as directory:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="msedge")
            page = await browser.new_page(viewport={"width":1080, "height":1920})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            await page.goto(URL + "/?manual&mock-test&seed=2")
            await expect(page.locator("#live-catalog")).to_have_text("Quà mô phỏng")
            await until(lambda: health()["game_connected"])
            await page.locator("#live-cat-list li").nth(4).wait_for()
            await expect(page.locator("#live-cat-list li")).to_have_count(5)
            await expect(page.locator("#live-enemy-list li")).to_have_count(5)
            await expect(page.locator("#live-skill-list li")).to_have_count(3)
            assert await page.locator("#live-gift-test, button:visible, #hud:visible, #menu:visible").count() == 0
            await expect(page.locator("#live-map")).to_have_text("Green Meadow")
            await page.keyboard.press("KeyW")
            await page.keyboard.press("Space")
            await page.keyboard.press("Escape")
            assert await page.evaluate("__pml.VIEW === 'battle' && !__pml.paused && !__pml.G.beam")
            dimensions = await page.evaluate("""() => {
                const r = id => $(id).getBoundingClientRect();
                return [cv.width,cv.height,r('cv').width/r('cv').height,r('live').height,
                    r('live-skill-list').bottom, r('live-field').bottom, r('live-cat-list').top];
            }""")
            assert dimensions[:2] == [320,180]
            assert abs(dimensions[2] - 16/9) < .001
            assert dimensions[4] <= dimensions[3] * .89 and dimensions[5] < dimensions[6], dimensions
            for state, label in [("unconfigured","Chưa cấu hình TikTok"), ("connecting","Đang kết nối TikTok"),
                                 ("connected","TikTok đã kết nối"), ("disconnected","TikTok mất kết nối")]:
                command("connection " + state)
                await expect(page.locator("#live-catalog")).to_have_text(label)
            command("connection mock")
            await expect(page.locator("#live-catalog")).to_have_text("Quà mô phỏng")
            assert await page.locator("#live-header, #live-clock").count() == 0
            await expect(page.locator(".live-support")).to_contain_text("BÌNH LUẬN ĐỂ CHỌN ĐỘI!")
            await expect(page.locator(".live-team-commands code")).to_have_text(["!meow","1","!monster","2"])
            command("like visitor 100")
            command('chat alice "xin chào !meow"')
            command("like alice 100")
            command("chat alice 1")
            await expect(page.locator("#live-team-notice")).to_contain_text("alice")
            await expect(page.locator("#live-team-notice")).to_contain_text("chọn phe Mèo")
            command("chat alice 2")
            command("like alice 50")
            await page.wait_for_function("pmlLive.spawnRate('cat') === 1.5")
            assert await page.evaluate("pmlLive.spawnRate('enemy')") == 1
            command("chat bob 2")
            await expect(page.locator("#live-team-notice")).to_contain_text("chọn phe Quái")
            if os.getenv("PML_TEST_SCREENSHOTS"):
                for width,height in [(1080,1920),(390,844)]:
                    await page.set_viewport_size({"width":width,"height":height})
                    await page.screenshot(path=Path(os.environ["TEMP"])/f"pml-team-{width}.png")
                    print(await page.evaluate("""() => ({
                        viewport:[innerWidth,innerHeight],
                        skillsBottom:Math.round($('live-skill-list').getBoundingClientRect().bottom),
                        commandSize:getComputedStyle(document.querySelector('.live-team-commands code')).fontSize
                    })"""), flush=True)
                await page.set_viewport_size({"width":1080,"height":1920})
            command("like bob 100")
            await page.wait_for_function("pmlLive.spawnRate('enemy') === 2")
            assert await page.evaluate("""() => {
                const old=[pmlLive.ai[0].remaining,pmlLive.ai[1].remaining];
                pmlLive.runAI('cat',.05);pmlLive.runAI('enemy',.05);
                const deltas=old.map((n,i)=>n-pmlLive.ai[i].remaining);
                return Math.abs(deltas[0]-.075)<1e-8 && Math.abs(deltas[1]-.1)<1e-8;
            }""")
            await page.wait_for_function("pmlLive.spawnRate('cat') === 1 && pmlLive.spawnRate('enemy') === 1")
            for text in ("spawn cat basic 5","spawn enemy slime 5","spawn cat mecha 1","spawn enemy boar 1"):
                command(text)
            await page.wait_for_function("pmlLive.spawnQueue.length === 4")
            await page.evaluate("() => __tick(1/60, 122)")
            await until(lambda: len([r for r in health()["recent"] if r["action"]=="spawn"]) == 4)
            counts = await page.evaluate("""() => Object.groupBy(__pml.G.units,u => u.def.id)
                && ['basic','slime','mecha','boar'].map(id=>__pml.G.units.filter(u=>u.def.id===id).length)""")
            assert counts == [6,6,1,1], counts
            command("cannon cat")
            command("cannon enemy")
            await until(ack("fire_cat_cannon","executed"))
            await until(ack("fire_enemy_cannon","executed"))
            # Track targets in each beam's exclusive area to detect accidental friendly fire.
            cannon_result = await page.evaluate("""() => {
                const cat = __pml.G.units.find(u=>u.def.id==='mecha');
                const enemy = __pml.G.units.find(u=>u.def.id==='boar');
                cat.x=225; enemy.x=80;
                const all=__pml.G.units;
                __pml.G.units=[cat,enemy];
                const before=[cat.hp,enemy.hp];
                __tick(1/60,24);
                __pml.G.units=all;
                return [__pml.G.beam?.hit, pmlLive.enemyBeam?.hit,before[0]-cat.hp,before[1]-enemy.hp,cat.x,enemy.x];
            }""")
            assert cannon_result[:4] == [True, True, 160, 160], cannon_result
            command("cannon cat")
            await until(ack("fire_cat_cannon","rejected"))
            await page.screenshot(path=Path(directory)/"live-1080.png")
            command("streak rose 10")
            await page.wait_for_function("pmlLive.spawnQueue.length === 1")
            assert await page.evaluate("pmlLive.spawnQueue[0].quantity") == 30
            await page.evaluate("() => __tick(1/60,150)")
            started = await until(ack("spawn","partial"))
            assert "16/30" in started["detail"], started
            for _ in range(4):
                command("change-map")
            await page.wait_for_function("pmlLive.mapQueue.length === 3")
            await until(ack("change_map","rejected"))
            await expect(page.locator("#live-map")).to_have_text("Green Meadow")
            await expect(page.locator("#live-next")).to_contain_text("Sunset Canyon")
            queued = await page.evaluate("pmlLive.mapQueue.map(x=>x.id)")
            socket_before = await page.evaluate("() => {window.testSocket=pmlLive.socket; return pmlLive.player;}")
            command("drop-ws")
            await page.wait_for_function("pmlLive.socket !== testSocket && pmlLive.socket.readyState === 1")
            assert await page.evaluate("pmlLive.player") == socket_before
            assert await page.evaluate("pmlLive.mapQueue.map(x=>x.id)") == queued
            command("like alice 50")
            await page.wait_for_function("pmlLive.spawnRate('cat') === 1.5")
            mark = len(health()["recent"])
            command("spawn enemy hog 2")
            await page.wait_for_function("pmlLive.spawnQueue.length > 0")
            await page.evaluate("() => __tick(1/60,35)")
            await until(ack("spawn","executed",mark))
            async def capture(label):
                target = os.getenv("PML_FINISH_SCREENSHOTS")
                for width,height in [(1080,1920),(390,844)]:
                    await page.set_viewport_size({"width":width,"height":height})
                    if target:
                        await page.screenshot(path=Path(target)/f"{label}-{width}.png")
                    bounds = await page.evaluate("""() => ({
                        bottom:Math.round(document.querySelector('.live-finish').getBoundingClientRect().bottom),
                        safe:Math.round($('live').getBoundingClientRect().bottom-
                            document.querySelector('.live-finish').getBoundingClientRect().bottom),
                        fieldWidth:$('live-field').getBoundingClientRect().width
                    })""")
                    assert bounds["bottom"] <= (1640 if width == 1080 else 668), bounds
                    if target:
                        print(label, width, bounds, flush=True)
                await page.set_viewport_size({"width":1080,"height":1920})

            await expect(page.locator("#live-finish-list li")).to_have_count(2)
            await expect(page.locator("#live-finish-list")).to_contain_text("Mock")
            await capture("battle")
            mark = len(health()["recent"])
            command("finish cat")
            command("finish enemy")
            await page.wait_for_function("pmlLive.finisher?.action.team === 'cat'")
            await until(ack("finish_battle_enemy","rejected",mark))
            command("cannon enemy")
            await until(ack("fire_enemy_cannon","rejected",mark))
            await page.evaluate("""() => {
                damage(G.pb,99999,false);
                if(G.pb.hp<=0) throw Error('Base damage bypassed finisher lock');
                __tick(1/60,105);
            }""")
            await expect(page.locator("#live-finisher")).to_contain_text("Mock")
            assert not ack("finish_battle_cat","executed",mark)()
            await capture("finish-cat")
            await page.evaluate("() => __tick(1/60,125)")
            await expect(page.locator("#live-result")).to_have_text("PHE MÈO CHIẾN THẮNG")
            assert await page.evaluate("G.eb.hp === 0 && G.pb.hp > 0")
            await until(ack("finish_battle_cat","executed",mark))
            await capture("winner-cat")
            await page.evaluate("() => __tick(1/60,310)")
            await expect(page.locator("#live-map")).to_have_text("Sunset Canyon")
            mark = len(health()["recent"])
            await until(lambda: health()["game"].get("map") == "Sunset Canyon")
            command("streak finish-enemy 3")
            await page.wait_for_function("pmlLive.finisher?.action.team === 'enemy'")
            await page.evaluate("() => __tick(1/60,105)")
            await capture("finish-enemy")
            await page.evaluate("() => __tick(1/60,125)")
            await expect(page.locator("#live-result")).to_have_text("PHE QUÁI CHIẾN THẮNG")
            assert await page.evaluate("G.pb.hp === 0 && G.eb.hp > 0")
            await until(ack("finish_battle_enemy","executed",mark))
            assert len([r for r in health()["recent"][mark:] if r["action"]=="finish_battle_enemy"]) == 1
            assert [r["winner"] for r in health()["results"]][-2:] == ["cat","enemy"]
            await capture("winner-enemy")
            await page.evaluate("() => __tick(1/60,310)")
            await expect(page.locator("#live-map")).to_have_text("Shadow Forest")
            await page.evaluate("() => {__pml.G.pb.hp=__pml.G.eb.hp=0; __tick(1/60,3)}")
            await expect(page.locator("#live-result")).to_have_text("HAI PHE HÒA")
            await page.evaluate("() => __tick(1/60,310)")
            await expect(page.locator("#live-map")).to_have_text("Green Meadow")
            await expect(page.locator("#live-wins")).to_have_text("1 · 1")
            assert await page.evaluate("pmlLive.mapQueue.length") == 0
            await page.set_viewport_size({"width":390,"height":844})
            await page.screenshot(path=Path(directory)/"live-phone.png")
            assert await page.evaluate("$( 'live-skill-list').getBoundingClientRect().bottom <= innerHeight")
            await page.reload()
            await expect(page.locator("#live-wins")).to_have_text("1 · 1")
            assert not errors, errors
            await page.evaluate("""() => {
                G.time=3600;
                __tick(1/60,2);
                if(G.over)throw Error('Round ended without a base defeat');
                G.eb.hp=0;
                __tick(1/60,310);
            }""")
            await expect(page.locator("#live-map")).to_have_text("Sunset Canyon")
            await page.goto(URL + "/game?manual&seed=2")
            await expect(page.locator("#menu")).to_be_visible()
            await page.evaluate("() => {__pml.newGame(0);__pml.deploy(0);__tick(1/60,180)}")
            assert await page.evaluate("__pml.G.units.some(u=>u.side===0)")
            assert not errors, errors
            await browser.close()


@dataclass
class GiftPayload:
    gift: dict
    gift_id: int = 123456
    user: dict = None
    group_id: int = 5
    repeat_count: int = 5
    repeat_end: int = 0
    common: dict = None


def test_adapter():
    # Synthetic IDs exercise the schema without asserting a live TikTok gift ID.
    gift = {"id":123456,"name":"Synthetic gift","type":1,"diamond_count":50,
            "image":{"url_list":["https://p16-webcast.tiktokcdn.com/gift.webp"]}}
    catalog = normalize_catalog({"data":{"gift_panel":[{"gifts":[gift]}]}})
    assert catalog[123456]["icon"] == "https://p16-webcast.tiktokcdn.com/gift.webp"
    assert catalog[123456]["diamonds"] == 50
    pipe = Pipeline("live","",{})
    pipe.rows = load_mapping(ROOT/"config"/"gifts.json")
    pipe.rows[0]["gift_id"] = 123456
    pipe.catalog = catalog
    assert pipe.config_message()["catalog"]["status"] == "cached"
    pipe.change_connection("connected")
    pipe.catalog_current = True
    assert pipe.config_message()["catalog"]["rows"][0]["name"] == "Synthetic gift"
    event = GiftPayload(gift=gift,user={"id":1,"nickname":"Viewer"})
    ingest_event(pipe,123,event)
    assert not pipe.pending
    event.repeat_end = 1
    ingest_event(pipe,123,event)
    ingest_event(pipe,123,event)
    assert len(pipe.pending) == 1
    assert next(iter(pipe.pending.values()))["action"]["quantity"] == 15
    pipe.rows[0]["enabled"] = False
    assert not pipe.config_message()["catalog"]["rows"]
    pipe.change_connection("disconnected")
    assert pipe.config_message()["catalog"]["status"] == "cached"


def test_audience():
    pipe = Pipeline("live","",{})
    from TikTokLive.proto.custom_proto import ExtendedUser
    viewer = ExtendedUser(id=10, nickname="Viewer")
    with patch("server.time.monotonic", return_value=100) as clock:
        ingest_audience(pipe, 42, LikeEvent(user=viewer, count=100, total=10000))
        for invalid in ("xin !meow", "!monster nhé", "1 2", "12"):
            ingest_audience(pipe, 42, CommentEvent(user=viewer, content=invalid))
        assert not pipe.viewer_teams
        assert pipe.cheer_rates() == {"cat":0,"enemy":0}
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content=" !MeOw "))
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content=" 1 "))
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content=" 2 "))
        ingest_audience(pipe, 42, LikeEvent(user=viewer, count=25, total=10001))
        assert pipe.cheer_rates() == {"cat":5,"enemy":0}
        assert len(pipe.team_notices) == 1
        clock.return_value = 109.9
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content="2"))
        assert pipe.viewer_teams[("42", "10")][0] == "cat"
        clock.return_value = 110
        pipe.change_connection("disconnected")
        pipe.change_connection("connected")
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content="2"))
        ingest_audience(pipe, 42, LikeEvent(user=viewer, count=75, total=10050))
        ingest_audience(pipe, 43, LikeEvent(user=viewer, count=100, total=10050))
        assert pipe.cheer_rates() == {"cat":0,"enemy":15}
        assert len(pipe.team_notices) == 2
        clock.return_value = 120
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content=" !MoNsTeR "))
        assert len(pipe.team_notices) == 2
        ingest_audience(pipe, 42, CommentEvent(user=viewer, content="1"))
        assert pipe.viewer_teams[("42", "10")][0] == "cat"


def test_finisher_catalog():
    pipe = Pipeline("live","",{})
    pipe.rows = load_mapping(ROOT/"config"/"gifts.json")
    row = next(row for row in pipe.rows if row["action"] == "finish_battle_cat")
    row["gift_id"] = 900001  # Synthetic fixture, never written to live config.
    pipe.catalog = {900001: {"id":900001,"name":"Fixture A","icon":"https://example.com/a.png","diamonds":1000},
                    900002: {"id":900002,"name":"Fixture B","icon":"https://example.com/b.png","diamonds":2000}}
    assert pipe.config_message()["catalog"]["rows"][0]["verified"] is False
    pipe.catalog_current = True
    before = pipe.config_message()["catalog"]["rows"][0]
    row["gift_id"] = 900002
    after = pipe.config_message()["catalog"]["rows"][0]
    assert (before["name"],before["icon"],before["diamonds"]) == ("Fixture A",None,1000)
    assert (after["name"],after["icon"],after["diamonds"]) == ("Fixture B",None,2000)
    assert after["verified"] and after["team"] == "cat"


def test_finisher_hot_reload():
    pipe = Pipeline("mock","",{})
    original = load_mapping(ROOT/"config"/"gifts.json")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)/"gifts.json"
        path.write_text(json.dumps(original), encoding="utf-8")
        with TestClient(create_app(pipe, mapping_path=path), base_url=URL) as client:
            with client.websocket_connect("/ws?player=00000000-0000-0000-0000-000000000001",
                                          headers={"host":"127.0.0.1:8080","origin":URL}) as ws:
                def config():
                    for _ in range(8):
                        message = ws.receive_json()
                        if message["type"] == "config":
                            return message
                    raise AssertionError("No refreshed gift mapping")

                config()
                row = next(row for row in original if row["action"] == "finish_battle_cat")
                row["mock_alias"] = "updated-crown"
                pipe.catalog["updated-crown"] = {"id":"updated-crown","name":"Miện thử · Mock",
                                                "icon":"https://example.com/mock-crown.png","diamonds":2500}
                path.write_text(json.dumps(original), encoding="utf-8")
                updated = next(row for row in config()["catalog"]["rows"] if row["action"] == "finish_battle_cat")
                assert updated["name"] == "Miện thử · Mock"
                assert updated["icon"] is None and updated["diamonds"] == 2500
                assert updated["team"] == "cat" and updated["verified"]


async def main():
    assert hashlib.sha256((ROOT/"original"/"index.html").read_bytes()).hexdigest() == (
        "13872636129bc807aa5597a7fafb3a347f8f24eea007d082a2a4895ade477c4c")
    test_adapter()
    test_audience()
    test_finisher_catalog()
    test_finisher_hot_reload()
    try:
        health()
    except OSError:
        pass
    else:
        raise SystemExit("Stop the current server on port 8080 before running this test.")
    with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-X", "utf8", "server.py", "mock"],cwd=ROOT,
                                   stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,
                                   env={**os.environ,"PML_DATA_DIR":data_dir,"GAME_AUDIO":"false","GAME_AUTO_CANNON":"false","GAME_VFX":"true"})
        try:
            await test_edge(process)
        except Exception:
            log.seek(0)
            print(log.read())
            raise
        finally:
            process.terminate()
            process.wait(10)
    print("PASS: mock terminal, browser, both cannons, streak/dedup, finishers, queue, three maps, result and reconnect")


if __name__ == "__main__":
    asyncio.run(main())
