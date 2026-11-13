"""Analytics verification against two real listeners and an isolated ledger."""
import asyncio
from contextlib import closing, contextmanager
import csv
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ADMIN = "http://127.0.0.1:8081"
GAME = "http://127.0.0.1:8080"
sys.path.insert(0,str(ROOT))
from analytics import Ledger
from dashboard import csv_cell
from tiktok_listener import gift_metadata
from TikTokLive.proto.custom_proto import ExtendedGift


def read(url, headers=None):
    with urlopen(Request(url,headers=headers or {}),timeout=.7) as response:
        return response.read()


def report(query="scope=all"):
    return json.loads(read(ADMIN+"/api/report?"+query))


async def wait_for(check, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            value = check()
            if value:
                return value
        except OSError:
            pass
        await asyncio.sleep(.08)
    raise AssertionError("Timed out waiting for service or data")


@contextmanager
def backend(directory, mode="mock"):
    path = Path(directory)/f"{mode}.log"
    with path.open("w+",encoding="utf-8") as log:
        proc = subprocess.Popen([sys.executable,"-X","utf8","server.py",mode],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0,
            env={**os.environ,"PML_DATA_DIR":directory,"TIKTOK_USERNAME":"","GAME_AUDIO":"false","GAME_AUTO_CANNON":"false"})
        def send(*commands):
            proc.stdin.write("\n".join(commands)+"\n")
            proc.stdin.flush()
        try:
            yield proc,send
        except Exception:
            log.seek(0)
            print(log.read()[-6000:])
            raise
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.CTRL_BREAK_EVENT if os.name=="nt" else signal.SIGINT)
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(5)


def verify_ledger(directory):
    assert gift_metadata(ExtendedGift(),123)["diamonds"] is None
    assert gift_metadata({"id":123,"diamond_count":0})["diamonds"] == 0
    path = Path(directory)/"analytics-live.sqlite3"
    ledger = Ledger(path,"live","fixture")
    session = ledger.session("1001")
    ledger.select_team(session,"1","alfa","Cùng tên","cat")
    row = {"id":"123", "name":"Crown", "diamonds":100}
    first = ledger.record_gift(session,"1","alfa","Cùng tên",row,3,("group",1),"cat","cat","round","spawn")
    ledger.action(first,"action1","EXECUTED")
    assert ledger.record_gift(session,"1","alfa","Cùng tên",row,3,("group",1),"cat","cat",None,"spawn") is None
    ledger.record_gift(session,"2","beta","Cùng tên",{"id":"456"},2,None,"enemy",None,None,"spawn")
    ledger.close()
    ledger = Ledger(path,"live","fixture")
    assert ledger.session("1001") == session
    assert ledger.record_gift(session,"1","alfa","Cùng tên",row,3,("group",1),None,None,None,None) is None
    saved = ledger.report()
    assert saved["overview"] == {"events":2,"quantity":5,"diamonds":300,"viewers":2,"unknown":2,
                                "cat":3,"enemy":2,"executed":1,"pending":0,"failed":1}
    assert ledger.report(team="cat")["overview"]["quantity"] == 3
    assert ledger.report(team="none")["overview"]["quantity"] == 2
    assert csv_cell(" =SUM(A1)") == "' =SUM(A1)" and csv_cell("Mèo") == "Mèo"
    ledger.close()


async def verify_browser(directory):
    with backend(directory) as (proc,send):
        await wait_for(lambda: report()["mode"] == "mock")
        assert b'live-field' in read(GAME+"/") and b'/api/report' not in read(GAME+"/")
        for url in [GAME+"/api/report",ADMIN+"/game"]:
            try:
                read(url)
            except HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("HTTP listener routes overlap")
        try:
            read(ADMIN+"/api/report",{"Origin":GAME})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("Dashboard accepted a foreign Origin")
        extra = subprocess.run([sys.executable,"-X","utf8","server.py","live"],cwd=ROOT,
                               capture_output=True,timeout=5)
        assert extra.returncode and b'8080' in extra.stderr
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="msedge")
            context = await browser.new_context(viewport={"width":1440,"height":1050})
            game = await context.new_page()
            errors = []
            game.on("pageerror",lambda e: errors.append(str(e)))
            await game.goto(GAME+"/?mock-test&manual")
            await expect(game.locator("#live-catalog")).to_have_text("Quà mô phỏng")
            await wait_for(lambda: json.loads(read(GAME+"/health"))["game"].get("round_id"))
            page = await context.new_page()
            page.on("pageerror",lambda e: errors.append(str(e)))
            await page.goto(ADMIN)
            await expect(page.locator("#connection")).to_have_text("● Mô phỏng")
            send('chat alba !meow','chat béta !monster','gift rose 2 alba "Cùng tên"',
                 'streak goo 5 béta "Cùng tên"','gift new-gift 2 cindy "=1+1"','gift cat-cannon 1 alba')
            await expect(page.locator(".metric").nth(0).locator("strong")).to_have_text("10")
            await game.wait_for_function("pmlLive.spawnQueue.length === 2")
            await game.evaluate("() => __tick(1/60,180)")
            send("change-map")
            await game.wait_for_function("pmlLive.mapQueue.length === 1")
            send("finish cat")
            await game.wait_for_function("!!pmlLive.finisher")
            await game.evaluate("() => __tick(1/60,230)")
            await expect(game.locator("#live-result")).to_have_text("PHE MÈO CHIẾN THẮNG")
            await game.evaluate("() => __tick(1/60,310)")
            await expect(game.locator("#live-map")).to_have_text("Sunset Canyon")
            await wait_for(lambda: report()["overview"]["executed"] == 5)
            stats = report()
            assert stats["overview"] == {"events":6,"quantity":12,"diamonds":10457,"viewers":4,
                "unknown":2,"cat":4,"enemy":5,"executed":5,"pending":0,"failed":1},stats["overview"]
            assert stats["rankings"]["quantity"][0]["username"] == "béta"
            assert stats["rankings"]["diamonds"][0]["username"] == "mock"
            assert stats["rankings"]["finish"][0]["value"] == 1
            verified_rounds = [r for r in stats["events"] if r["status"]=="EXECUTED"]
            assert all(row["round_id"] and row["action_id"] for row in verified_rounds)
            await page.locator("#search").fill("alba")
            await expect(page.locator("#senders tr")).to_have_count(1)
            await expect(page.locator("#senders")).to_contain_text("@alba")
            await page.get_by_role("button",name="Xem chi tiết").click()
            await expect(page.locator("#gifts-heading")).to_contain_text("@alba")
            await page.get_by_role("button",name="Xem mọi người gửi").click()
            await page.locator("#search").fill("")
            await page.locator("#scope").select_option("all")
            await expect(page.locator(".metric").nth(0).locator("strong")).to_have_text("12")
            screenshot = os.getenv("PML_ANALYTICS_SCREENSHOT")
            if screenshot:
                await page.evaluate("window.scrollTo(0,0)")
                await page.screenshot(path=screenshot,full_page=True)
            csv_text = read(ADMIN+"/api/export/senders?scope=all").decode("utf-8-sig")
            assert "Cùng tên" in csv_text and "'=1+1" in csv_text
            records = list(csv.DictReader(io.StringIO(read(ADMIN+"/api/export/gifts?scope=all").decode("utf-8-sig"))))
            assert len(records) == 6 and any(row["diamond_value"]=="" for row in records)
            await page.close()
            send(*("gift unconfigured 1 viewer"+str(i) for i in range(300)))
            await wait_for(lambda: report()["overview"]["events"]==306)
            page = await context.new_page()
            await page.goto(ADMIN)
            await expect(page.locator(".metric").nth(1).locator("strong")).to_have_text("306")
            await context.set_offline(True)
            await expect(page.locator("#connection")).to_contain_text("Mất kết nối")
            send("gift rose 1 alba")
            await wait_for(lambda: report()["overview"]["events"]==307)
            await context.set_offline(False)
            await expect(page.locator(".metric").nth(1).locator("strong")).to_have_text("307",timeout=15000)
            assert not errors,errors
            await browser.close()
    with backend(directory) as (proc,send):
        await wait_for(lambda: report()["overview"]["events"]==307)
        assert report("scope=current")["overview"]["events"] == 0
        assert report()["overview"]["pending"] == 0
    with backend(directory,"live") as (proc,send):
        await wait_for(lambda: report()["mode"]=="live")
        assert report()["overview"]["events"] == 0
    with closing(sqlite3.connect(Path(directory)/"analytics-mock.sqlite3")) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("SELECT count(*) FROM battle_events").fetchone()[0] == 1


async def main():
    with tempfile.TemporaryDirectory() as directory:
        verify_ledger(directory)
    with tempfile.TemporaryDirectory() as directory:
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1",8081))
            occupied.listen()
            attempt = subprocess.run([sys.executable,"-X","utf8","server.py","mock"],cwd=ROOT,
                capture_output=True,timeout=5,env={**os.environ,"PML_DATA_DIR":directory})
            assert attempt.returncode and b"port 8081" in attempt.stderr
            assert not list(Path(directory).glob("*.sqlite3"))
        await verify_browser(directory)
    print("PASS: two listeners, API isolation, game, SSE reconnect, CSV, SQLite restart, 300-event burst and mode isolation")


if __name__ == "__main__":
    asyncio.run(main())
