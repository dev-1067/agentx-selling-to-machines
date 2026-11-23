"""Voice-over for the pitch video: one clip per scene, with cue times and per-word timings for subtitles."""
import asyncio
import json
import re
import subprocess
from pathlib import Path

import edge_tts
import imageio_ffmpeg

from demo_script import RATE, SCENES, VOICE

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = HERE / "work" / "audio"
OUT.mkdir(parents=True, exist_ok=True)
FF = imageio_ffmpeg.get_ffmpeg_exe()
CUE = re.compile(r"\{(\w+)\}")


def numbers():
    o = json.loads((ROOT / "results" / "benchmark_official.json").read_text())
    cr = json.loads((ROOT / "results" / "classifier_report.json").read_text(encoding="utf-8"))
    lp = ROOT / "results" / "benchmark_llm.json"
    llm = json.loads(lp.read_text()) if lp.exists() else {"policy": 0, "truth": 0}
    spoken = lambda x: str(round(x, 1)).replace(".", " point ")
    return {"official_score": spoken(o["score"]), "official_baseline": spoken(o["baseline"]),
            "feasible_rate": str(round(100 * o["feasible_deal_rate"])),
            "llm_acc": ("%.1f" % (100 * cr["fresh_test_v2"]["accuracy"])).replace(".0", ""), "kit_acc": str(round(100 * cr["kit_lines"]["accuracy"])),
            "llm_policy": str(llm["policy"]), "llm_truth": str(llm["truth"]),
            "llm_score": spoken(llm.get("score", 0)), "llm_baseline": spoken(llm.get("baseline") or 0), "llm_n": str(llm.get("n", 0))}


def duration(path):
    err = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


async def one(name, text):
    cues, words, parts = {}, 0, []
    for part in re.split(r"(\{\w+\})", text):
        m = CUE.fullmatch(part)
        if m:
            cues[m.group(1)] = words
        else:
            parts.append(part)
            words += len(re.findall(r"[A-Za-z0-9']+", part))
    clean = re.sub(r"\s+", " ", "".join(parts)).strip()
    comm = edge_tts.Communicate(clean, VOICE, rate=RATE, boundary="WordBoundary")
    path = OUT / f"{name}.mp3"
    bounds = []
    with open(path, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                bounds.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    dur = duration(path)
    times = {c: (bounds[i][0] if i < len(bounds) else dur) for c, i in cues.items()}
    return {"audio": str(path), "duration": dur, "cues": times, "words": bounds, "text": clean}


async def main():
    nums = numbers()
    timing = {}
    for name, text, _ in SCENES:
        text = re.sub(r"\{(\w+)\}", lambda m: nums.get(m.group(1), m.group(0)) if m.group(1) in nums else m.group(0), text)
        for attempt in range(12):
            try:
                timing[name] = await one(name, text)
                break
            except Exception:
                if attempt == 11:
                    raise
                await asyncio.sleep(2 + attempt)
        print(f"{name:16s} {timing[name]['duration']:5.1f}s cues={ {k: round(v, 2) for k, v in timing[name]['cues'].items()} }")
    (HERE / "work" / "timing.json").write_text(json.dumps(timing, indent=1), encoding="utf-8")
    print("speech total", round(sum(t["duration"] for t in timing.values()), 1), "s")


asyncio.run(main())
