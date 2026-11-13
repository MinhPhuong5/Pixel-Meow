"""Exercise LIVE Web Audio in Edge with autoplay enforcement and real gameplay time."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import json

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8080/"


def verify(duration):
    with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile(mode="w+",encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable,"-X","utf8","server.py","mock"],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=log,stderr=log,text=True,
            env={**os.environ,"PML_DATA_DIR":directory,"GAME_AUDIO":"true","GAME_AUTO_CANNON":"true"})
        try:
            for _ in range(30):
                try:
                    urlopen(URL+"health",timeout=.4).close()
                    break
                except OSError:
                    time.sleep(.2)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(channel="msedge",args=["--autoplay-policy=document-user-activation-required"])
                page = browser.new_page(viewport={"width":1080,"height":1920})
                errors = []
                page.on("pageerror",lambda error: errors.append(str(error)))
                page.goto(URL+"?audio=setup&audio-test&seed=2")
                button = page.locator("#live-audio-start")
                assert button.is_visible()
                assert page.evaluate("pmlAudio.context.state") == "suspended"
                button.click()
                page.wait_for_function("pmlAudio.context.state==='running'")
                assert not button.is_visible()
                page.evaluate("""() => {
                    window.audioProof = {peak:0,samples:0};
                    const analyser = pmlAudio.context.createAnalyser();
                    pmlAudio.compressor.connect(analyser);
                    const buffer = new Float32Array(analyser.fftSize);
                    setInterval(() => {
                        analyser.getFloatTimeDomainData(buffer);
                        for (const x of buffer) audioProof.peak = Math.max(audioProof.peak,Math.abs(x));
                        audioProof.samples++;
                    },23);
                }""")
                for _ in range(duration//10):
                    page.wait_for_timeout(10000)
                if duration:
                    result = page.evaluate("""() => ({events:pmlAudio.events,peak:audioProof.peak,
                        voices:pmlAudio.peakVoices,contexts:pmlAudio.created,round:pmlLive.roundNumber})""")
                    print("Autonomous",result,flush=True)
                    for event in ("cat","enemy","boss","hit","death","cannonCat","cannonEnemy"):
                        assert result["events"].get(event), event
                    assert result["peak"] > .02 and result["peak"] < .98
                    assert result["voices"] <= 16 and result["contexts"] == 1
                    if duration >= 300:
                        assert result["events"].get("collapse") and result["round"] >= 1
                        assert result["events"].get("winCat") or result["events"].get("winEnemy")
                assert not errors, errors

                def command(line):
                    process.stdin.write(line+"\n")
                    process.stdin.flush()

                # Explicitly reset the fixture so previous battle cooldowns do not affect Mock assertions.
                round_id = page.evaluate("pmlLive.roundId")
                command("restart")
                page.wait_for_function("id => pmlLive.roundId !== id",arg=round_id)
                initial_cat_sounds = page.evaluate("pmlAudio.events.cat || 0")
                command("gift rose 1")
                page.wait_for_function("G.units.filter(u=>u.def.id==='basic').length>=3")
                assert page.evaluate("pmlAudio.events.cat") > initial_cat_sounds
                command("gift crown 1")
                page.wait_for_function("G.units.some(u=>u.def.id==='boar') && pmlAudio.events.gift")
                command("gift enemy-cannon")
                page.wait_for_function("pmlLive.enemyBeam")
                command("change-map")
                page.wait_for_function("pmlLive.mapQueue.length===1")
                command("finish cat")
                page.wait_for_function("pmlLive.finisher && pmlAudio.ducked")
                page.wait_for_function("G.over==='win' && pmlAudio.events.finisherImpact",timeout=7000)
                assert page.evaluate("pmlAudio.events.finisherCue===1 && pmlAudio.events.finisherLaunch===1 && pmlAudio.events.finisherImpact===1")
                page.wait_for_function("!G.over && !pmlAudio.ducked && pmlAudio.events.map",timeout=7000)
                page.evaluate("""() => {
                    const count = side => G.units.filter(unit => unit.side === side).length;
                    for (let i=count(0);i<24;i++) spawnUnit(0,PU[2],1);
                    for (let i=count(1);i<24;i++) spawnUnit(1,EU.crow,1);
                }""")
                page.wait_for_timeout(6000)
                result = page.evaluate("""() => ({
                    events:pmlAudio.events,peak:audioProof.peak,voices:pmlAudio.peakVoices,contexts:pmlAudio.created
                })""")
                assert result["voices"]<=16 and result["contexts"]==1 and result["peak"]<.98
                assert not errors, errors
                with urlopen(URL+"health",timeout=2) as response:
                    result["fps"] = json.load(response)["game"].get("fps")
                assert result["fps"] and result["fps"] >= 48
                assert result["events"].get("gift"), "Heavy gift cue"
                print("Mock and load",result,flush=True)
                page.close()
                clean = browser.new_page()
                clean.goto(URL)
                assert not clean.locator("#live-audio-start").is_visible()
                clean.locator("body").press("a")
                clean.wait_for_function("pmlAudio.context.state==='running'")
                browser.close()
        except Exception:
            log.seek(0)
            print(log.read()[-5500:])
            raise
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration",type=int,default=300)
    verify(parser.parse_args().duration)
