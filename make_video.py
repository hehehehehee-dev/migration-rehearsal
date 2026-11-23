"""Assemble the recorded workbench states and narrate the actual results."""
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
import wave
import subprocess
import textwrap
from imageio_ffmpeg import get_ffmpeg_exe

ROOT = Path(__file__).resolve().parent
WORK = ROOT / ".video-work"
PAPER = "#eee8dc"
INK = "#201c17"
RED = "#852310"
OUTCOME_COLOR = {"blocked": RED, "passed": "#204832", "unclear": "#644200", "unsupported": "#233e75"}
SIZE = (1280,720)

def font(face,size):
    return ImageFont.truetype(str(Path("C:/Windows/Fonts")/face),size)

def draw_text(draw, x, y, text, size, color=INK, face="ariblk.ttf"):
    draw.text((x,y),text,font=font(face,size),fill=color)

def plate(label, headline, subhead, note, accent=INK):
    image=Image.new("RGB",SIZE,PAPER)
    d=ImageDraw.Draw(image)
    draw_text(d,64,39,"REHEARSAL  /  CHINOOK",19,INK,"consolab.ttf")
    d.line((64,86,1216,86),fill=INK,width=2)
    draw_text(d,64,123,label,25,INK,"consolab.ttf")
    draw_text(d,58,193,headline,80,accent)
    draw_text(d,65,374,subhead,27,INK,"arialbd.ttf")
    d.line((64,607,1216,607),fill=INK,width=2)
    draw_text(d,65,630,note,20,INK,"consolab.ttf")
    return image

def workbench_still(path, footer, *, focus=False):
    image=Image.new("RGB",SIZE,PAPER)
    capture=Image.open(path).convert("RGB")
    if focus:
        capture=capture.crop((48, 319, 1232, 896))
    view=ImageOps.contain(capture,(1144,651),Image.Resampling.LANCZOS)
    image.paste(view,((1280-view.width)//2,12))
    d=ImageDraw.Draw(image)
    d.line((64,669,1216,669),fill=INK)
    draw_text(d,64,683,footer,16,INK,"consolab.ttf")
    return image

def read_report(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def live_plate(runs):
    model = runs[0][1]["model"].upper()
    shot=plate("LIVE AGENT  /  SYNAPSE CUSTOM", "Live agent trace", "", f"SAME LOCAL MODEL  /  {model}")
    draw=ImageDraw.Draw(shot)
    for x,label in ((65,"CANDIDATE"),(469,"COMPOSED"),(846,"SEPARATE TOOLS")):
        draw_text(draw,x,321,label,20,INK,"consolab.ttf")
    for y,(label,result) in zip((366,422,478,534),runs):
        draw.line((64,y-8,1216,y-8),fill=INK)
        draw_text(draw,65,y,label,24,INK,"arialbd.ttf")
        for x,side in ((469,"composed"),(846,"separate")):
            item=result[side]
            calls="call" if item["calls"]==1 else "calls"
            verdict=item["verdict"]
            draw_text(draw,x,y,f"{verdict.upper()}  /  {item['calls']} {calls}",22,
                      OUTCOME_COLOR.get(verdict,INK),"consolab.ttf")
    return shot


def boundary_plate():
    shot=plate("SCOPE REVIEW", "When SQL never runs", "", "NO RELEASE CLEARANCE IN EITHER CASE")
    draw=ImageDraw.Draw(shot)
    labels=((64,"Missing backfill scope","workbench-unclear.png"),
            (660,"Currency conversion","workbench-unsupported.png"))
    for x,label,filename in labels:
        draw_text(draw,x,340,label,24,INK,"arialbd.ttf")
        report=Image.open(WORK/filename).convert("RGB").crop((466,440,1196,624))
        report=ImageOps.contain(report,(554,170),Image.Resampling.LANCZOS)
        shot.paste(report,(x,389))
    return shot


def scenes():
    blocked=read_report("demo-blocked.json")
    verification=read_report("mcp-benchmark-results.json")
    pin=blocked["contract_sha256"]
    wrong=blocked["issues"]["price_cents_mismatch"]["count"]
    matched,total=verification["matched"],verification["total"]
    holdout=sum(row["split"]=="holdout" and row["matched"] for row in verification["results"])
    labels=(("Faulty conversion", "integration-results.json"),
            ("Corrected conversion", "integration-corrected-results.json"),
            ("Missing scope", "integration-unclear-results.json"),
            ("Currency conversion", "integration-unsupported-results.json"))
    runs=[(label,read_report(name)["live_comparison"]) for label,name in labels]
    for _,run in runs:
        for side in ("composed","separate"):
            reports=[call["result"] for call in run[side]["trace"] if "verdict" in call["result"]]
            if reports and reports[-1]["contract_sha256"] != pin:
                raise ValueError("Live evidence was recorded against another contract")
    yield (
        plate("RELEASE REVIEW","$0.99 → $0.00",f"SQLite committed on the copy. The rehearsal caught {wrong:,} wrong prices.",
              "ACTUAL REHEARSAL  /  PUBLIC CHINOOK DATABASE",RED),
        "SQLite committed the migration. Track one went from ninety nine cents to zero. The rehearsal caught that and thousands of other wrong prices.",
    )
    yield (
        workbench_still(ROOT/"workbench-preview.png","01  SQL COMMITTED. THE REHEARSAL BLOCKED THE RELEASE.",focus=True),
        "Here is the run. The foreign keys pass, but the price check blocks the release and shows examples from the catalog.",
    )
    yield (
        plate("CONTRACT PIN",pin[:12] + "…", "The tool verifies the same rules before SQL and before reporting.",
              f"SHA-256  /  {pin}"),
        "The price rules have a pinned hash. If the contract changes mid run, the tool withholds clearance.",
    )
    yield (
        workbench_still(WORK/"workbench-passed.png","02  THE CORRECTED PLAN PASSES THE SAME CONTRACT.",focus=True),
        "Multiply before rounding and rerun. Every price now matches the contract.",
    )
    yield (
        boundary_plate(),
        "An unspecified backfill needs clarification. Currency conversion is unsupported. Both outcomes stop before SQL runs.",
    )
    one=[run[1]["composed"]["calls"] for run in runs]
    many=[run[1]["separate"]["calls"] for run in runs]
    if all(c==1 for c in one):
        phrase="Synapse returned each outcome in one call. "
    else:
        phrase=f"The composed tool took {min(one)} to {max(one)} calls per run. "
    detail=(f"The same model took {many[0]} calls per run with the separate tools."
            if len(set(many))==1 else f"With separate tools, the same model took {min(many)} to {max(many)} calls per run.")
    if any(run[1]['separate']['verdict'] == 'incomplete' for run in runs):
        detail += ' The separate agent did not finish every case.'
    yield live_plate(runs), phrase+detail
    yield (
        plate("FIXED BENCHMARK",f"{matched} / {total} matched", "Both MCP paths checked the same candidates and pinned rules.",
              f"{holdout} HOLDOUT CASES MATCHED  /  SOURCE FIXTURE UNCHANGED"),
        f"Both tool setups matched {matched} of {total} cases, including all {holdout} holdouts. The original database stayed byte for byte identical.",
    )


def say(narration, target):
    script = ("Add-Type -AssemblyName System.Speech\n"
              "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
              "$voice.SelectVoice('Microsoft Zira Desktop')\n"
              "$voice.Rate = -1\n"
              "$voice.SetOutputToWaveFile('" + str(target).replace("'", "''") + "')\n"
              "$voice.Speak('" + narration.replace("'", "''") + "')\n"
              "$voice.Dispose()")
    subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",script], check=True)

def ffmpeg(*args):
    subprocess.run([get_ffmpeg_exe(),"-hide_banner","-loglevel","error","-y",*map(str,args)],check=True)

def timestamp(seconds):
    total = round(seconds * 1000)
    hours, total = divmod(total, 3600000)
    minutes, total = divmod(total, 60000)
    seconds, milliseconds = divmod(total, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def main():
    clips=[]
    captions=[]
    elapsed=0.0
    for i,(image,narration) in enumerate(scenes(),1):
        frame=WORK/f"review-{i}.png"
        voice=WORK/f"review-{i}.wav"
        clip=WORK/f"review-{i}.mp4"
        image.save(frame)
        say(narration,voice)
        with wave.open(str(voice)) as stream:
            duration=stream.getnframes()/stream.getframerate()+0.65
        ffmpeg("-loop",1,"-framerate",24,"-i",frame,"-i",voice,"-t",f"{duration:.3f}",
               "-af","adelay=160,apad","-c:v","libx264","-preset","fast","-crf",18,
               "-pix_fmt","yuv420p","-c:a","aac","-ar",48000,"-b:a","96k",clip)
        clips.append(f"file '{clip.name}'")
        start,end=timestamp(elapsed+0.16),timestamp(elapsed+duration-0.25)
        captions.append(f"{i}\n{start} --> {end}\n{textwrap.fill(narration,width=62)}")
        elapsed+=duration
    playlist=WORK/"review-clips.txt"
    playlist.write_text("\n".join(clips)+"\n",encoding="utf-8")
    subtitles=WORK/"rehearsal-captions.srt"
    subtitles.write_text("\n\n".join(captions)+"\n",encoding="utf-8")
    ffmpeg("-f","concat","-safe",0,"-i",playlist,"-i",subtitles,
           "-map","0:v","-map","0:a","-map","1:s","-c:v","copy","-c:a","copy","-c:s","mov_text",
           "-metadata:s:s:0","language=eng","-movflags","+faststart",
           ROOT/"migration-rehearsal-demo.mp4")

if __name__ == "__main__":
    main()
