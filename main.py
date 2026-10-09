"""
Horror channel automation.
Usage:  python main.py short   |   python main.py long
Pipeline: Gemini story -> edge-tts Hindi voice -> AI images -> ffmpeg video -> YouTube upload
"""
import asyncio
import datetime
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import time
import urllib.parse

import requests

MODE = sys.argv[1] if len(sys.argv) > 1 else "short"
assert MODE in ("short", "long"), "mode must be short or long"

WORK = pathlib.Path("work")
WORK.mkdir(exist_ok=True)
HISTORY_FILE = pathlib.Path("history.json")

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
]
PRIVACY = os.environ.get("PRIVACY", "public")  # public / unlisted / private
DRY_RUN = os.environ.get("DRY_RUN") == "1"  # build video but do not upload

VOICES = ["hi-IN-MadhurNeural", "hi-IN-SwaraNeural"]

if MODE == "short":
    W, H = 1080, 1920
    TARGET_WORDS, SCENES = 120, 6
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 66, 420, 4
else:
    W, H = 1280, 720
    TARGET_WORDS, SCENES = 680, 14
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 44, 50, 8

SETTINGS = [
    "सुनसान रेलवे स्टेशन की आख़िरी रात", "पुरानी हवेली का बंद कमरा", "गाँव का सूखा कुआँ",
    "रात की आख़िरी बस", "अधूरा बना होटल", "पहाड़ पर अकेला होमस्टे", "बंद पड़ी स्कूल की लाइब्रेरी",
    "हॉस्टल का तीसरा माले का कमरा", "जंगल का कच्चा रास्ता", "पुराना रेडियो जो अपने आप चलता है",
    "खाली अस्पताल का वार्ड", "नदी किनारे का पुराना घाट", "हाईवे का सुनसान ढाबा",
    "पुराना सिनेमा हॉल", "लिफ़्ट जो रात में एक अनजान मंज़िल पर रुकती है", "नई किराये की कोठी का तहख़ाना",
    "कोहरे में डूबा गाँव", "पुरानी तस्वीरों वाली एल्बम", "रात की ड्यूटी वाला चौकीदार", "खंडहर बनी बावड़ी",
]
STRUCTURES = [
    "अंत में चौंकाने वाला मोड़ (twist) जो शुरुआत की किसी छोटी बात से जुड़ता है",
    "कहानी एक डायरी या चिट्ठी के रूप में, जिसका आख़िरी पन्ना डरा देता है",
    "सुनाने वाला ख़ुद नहीं जानता कि वह जिंदा है या नहीं, अंत में सच खुलता है",
    "कहानी जहाँ से शुरू हुई वहीं लौट आती है, पर अब कुछ बदल चुका है (loop)",
    "एक आम आवाज़ या आदत धीरे-धीरे डरावनी बनती जाती है",
    "एक सीधा-सादा नियम जिसे तोड़ने पर क्या होता है",
]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- history
def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(h):
    HISTORY_FILE.write_text(json.dumps(h[-200:], ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- story
def build_prompt(setting, structure, recent_titles):
    length_note = (
        f"लगभग {TARGET_WORDS} शब्द, कुल {SCENES} दृश्य (scenes)। 60 सेकंड से कम में सुनाई जा सके।"
        if MODE == "short"
        else f"लगभग {TARGET_WORDS} शब्द, कुल {SCENES} दृश्य (scenes)। सुनाने में करीब 5 मिनट लगें।"
    )
    return f"""तुम एक अनुभवी हिंदी हॉरर कहानीकार हो। एक बिल्कुल नई, मौलिक, काल्पनिक हॉरर कहानी लिखो।

स्थान/माहौल: {setting}
कहानी की बनावट: {structure}
लंबाई: {length_note}

ज़रूरी नियम (YouTube विज्ञापन-अनुकूल रहने के लिए):
- डर और तनाव माहौल, आवाज़ों, परछाइयों और अनिश्चितता से बनाओ। ख़ून-ख़राबा, गोर, शरीर के अंगों का वर्णन, यातना नहीं।
- कोई असली व्यक्ति, असली घटना, असली जगह की बदनामी, धर्म/जाति पर टिप्पणी नहीं।
- आत्महत्या, ख़ुद को नुकसान, यौन सामग्री, बच्चों को नुकसान, गाली-गलौज नहीं।
- कहानी की पहली पंक्ति ऐसी हो कि सुनने वाला तुरंत रुक जाए (हुक)।
- हर दृश्य के अंत में हल्का सस्पेंस, और आख़िर में दमदार मोड़ या अधूरा सवाल।
- सरल, बोलचाल की हिंदी (देवनागरी), छोटे वाक्य, जो बोलने में अच्छे लगें।
- ये शीर्षक पहले आ चुके हैं, इनसे अलग रखो: {recent_titles}

सिर्फ़ यह JSON लौटाओ, और कुछ नहीं:
{{
 "title": "हिंदी शीर्षक, 70 अक्षर तक, रहस्यमय पर सच्चा (कोई झूठा दावा नहीं)",
 "description": "2-3 पंक्ति का हिंदी विवरण, जो कहानी का अंत न बताए",
 "tags": ["8-12 टैग हिंदी/अंग्रेज़ी"],
 "scenes": [
  {{"narration": "इस दृश्य में बोला जाने वाला हिंदी पाठ", "image_prompt": "English visual description of the scene: dark cinematic atmosphere, moody lighting, no gore, no blood, no text, no logos, no real people"}}
 ]
}}"""


def call_gemini(prompt):
    last_err = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"},
        }
        for attempt in range(3):
            try:
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=180)
                if r.status_code == 200:
                    data = r.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    return text
                last_err = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                log("Gemini error:", last_err)
                if r.status_code in (404, 400):
                    break  # model name problem, try next model
                time.sleep(10 * (attempt + 1))
            except Exception as e:  # noqa
                last_err = f"{model}: {e}"
                log("Gemini exception:", last_err)
                time.sleep(10)
    raise RuntimeError(f"Gemini failed: {last_err}")


def parse_story(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        story = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        story = json.loads(m.group(0))
    scenes = [s for s in story["scenes"] if s.get("narration") and s.get("image_prompt")]
    if len(scenes) < 3:
        raise ValueError("too few scenes")
    story["scenes"] = scenes
    return story


def make_story(history):
    recent = [h["title"] for h in history[-30:]]
    recent_settings = [h.get("setting") for h in history[-8:]]
    choices = [s for s in SETTINGS if s not in recent_settings] or SETTINGS
    setting = random.choice(choices)
    structure = random.choice(STRUCTURES)
    for attempt in range(3):
        try:
            story = parse_story(call_gemini(build_prompt(setting, structure, recent)))
            words = sum(len(s["narration"].split()) for s in story["scenes"])
            log(f"Story ok: '{story['title']}' scenes={len(story['scenes'])} words={words}")
            min_words = TARGET_WORDS * (0.55 if MODE == "long" else 0.5)
            if words < min_words:
                log("Story too short, retrying")
                continue
            story["setting"] = setting
            return story
        except Exception as e:  # noqa
            log("Story parse problem:", e)
    raise RuntimeError("Could not produce a valid story")


# ---------------------------------------------------------------- voice
def probe_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    )
    return float(out.decode().strip())


async def _tts(text, voice, out):
    import edge_tts

    # slower and slightly deeper = creepier
    com = edge_tts.Communicate(text, voice, rate="-10%", pitch="-4Hz")
    await com.save(str(out))


def make_voice(scenes, voice):
    files = []
    for i, sc in enumerate(scenes):
        out = WORK / f"voice_{i:02d}.mp3"
        for attempt in range(4):
            try:
                asyncio.run(_tts(sc["narration"], voice, out))
                if out.exists() and out.stat().st_size > 1000:
                    break
            except Exception as e:  # noqa
                log("TTS retry:", e)
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"TTS failed for scene {i}")
        files.append(out)
    return files


# ---------------------------------------------------------------- images
def fetch_image(prompt, idx, seed):
    full = (
        prompt
        + ", dark horror atmosphere, cinematic, moody lighting, highly detailed, no text, no watermark, no gore"
    )
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(full)
        + f"?width={W}&height={H}&nologo=true&model=flux&seed={seed + idx}"
    )
    out = WORK / f"img_{idx:02d}.jpg"
    for attempt in range(4):
        try:
            r = requests.get(url, timeout=120)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                out.write_bytes(r.content)
                return out
            log(f"image {idx} http {r.status_code}")
        except Exception as e:  # noqa
            log(f"image {idx} error: {e}")
        time.sleep(8 * (attempt + 1))
    return None


def make_images(scenes):
    seed = random.randint(1, 10**6)
    paths, failed = [], 0
    for i, sc in enumerate(scenes):
        p = fetch_image(sc["image_prompt"], i, seed)
        if p is None:
            failed += 1
            # reuse previous good image rather than uploading a blank frame
            p = paths[-1] if paths else None
        paths.append(p)
    if failed > max(1, len(scenes) * 0.3) or paths[0] is None:
        raise RuntimeError(f"Too many image failures ({failed}/{len(scenes)}); aborting to protect channel quality")
    # fill any leading gaps
    first_good = next(p for p in paths if p)
    return [p or first_good for p in paths]


# ---------------------------------------------------------------- video
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log("FFMPEG ERROR:", r.stderr[-1500:])
        raise RuntimeError("ffmpeg failed")


def make_clip(img, audio, idx, zoom_in):
    dur = probe_duration(audio) + 0.5
    frames = int(dur * 25) + 1
    if zoom_in:
        z = "min(zoom+0.0012,1.25)"
    else:
        z = "if(eq(on,0),1.25,max(zoom-0.0012,1.0))"
    vf = (
        f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps=25,"
        f"vignette=PI/5,noise=alls=7:allf=t,fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.4:.2f}:d=0.4,format=yuv420p"
    )
    out = WORK / f"clip_{idx:02d}.mp4"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(img), "-i", str(audio),
        "-vf", vf, "-af", "apad=pad_dur=0.5",
        "-t", f"{dur:.2f}", "-r", "25",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        str(out),
    ])
    return out, dur


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_subs(scenes, durs, path):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Noto Sans Devanagari,{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,60,60,{MARGIN_V},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines, t0 = [], 0.0
    for sc, dur in zip(scenes, durs):
        words = sc["narration"].split()
        chunks = [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), CHUNK_WORDS)]
        speech = max(dur - 0.5, 0.5)
        total_chars = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            d = speech * len(c) / total_chars
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{c}")
            t += d
        t0 += dur
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def build_video(images, voices, scenes):
    clips, durs = [], []
    for i, (img, aud) in enumerate(zip(images, voices)):
        c, d = make_clip(img, aud, i, zoom_in=(i % 2 == 0))
        clips.append(c)
        durs.append(d)
        log(f"clip {i + 1}/{len(images)} done ({d:.1f}s)")
    listfile = WORK / "clips.txt"
    listfile.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(joined)])

    total = sum(durs)
    subs = WORK / "subs.ass"
    make_subs(scenes, durs, subs)

    # self-made ambient drone (no copyright issues); frequency varies per video
    f1 = random.choice([40, 45, 50, 55, 60])
    final = WORK / "final.mp4"
    fc = (
        f"[0:a]volume=1.0[a0];"
        f"[1:a]lowpass=f=400,volume=0.55[n];"
        f"[2:a]volume=0.25,tremolo=f=0.3:d=0.6[s];"
        f"[a0][n][s]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a];"
        f"[0:v]ass={subs.as_posix()}[v]"
    )
    run([
        "ffmpeg", "-y", "-i", str(joined),
        "-f", "lavfi", "-t", f"{total:.1f}", "-i", "anoisesrc=color=brown:sample_rate=44100:amplitude=0.4",
        "-f", "lavfi", "-t", f"{total:.1f}", "-i", f"sine=frequency={f1}:sample_rate=44100",
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(final),
    ])
    log(f"Final video: {total:.0f}s -> {final}")
    if MODE == "short" and total > 59:
        log("WARNING: short is longer than 59s; it may not count as a Short")
    return final, total


# ---------------------------------------------------------------- upload
def upload(video_path, story, thumb):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    title = story["title"].strip()
    desc = story["description"].strip()
    desc += "\n\n⚠️ यह कहानी पूरी तरह काल्पनिक है। इसे बनाने में AI (कहानी, आवाज़ और चित्र) की मदद ली गई है।"
    desc += "\n👉 ऐसी और कहानियों के लिए चैनल को सब्सक्राइब करें।"
    tags = [str(t)[:30] for t in story.get("tags", [])][:15]
    if MODE == "short":
        title = (title[:88] + " #Shorts") if "#shorts" not in title.lower() else title[:100]
        desc += "\n\n#Shorts #HindiHorror #HorrorStory"
        tags += ["shorts"]
    else:
        title = title[:100]
        desc += "\n\n#HindiHorror #HorrorStory #हॉररकहानी"

    body = {
        "snippet": {
            "title": title,
            "description": desc,
            "tags": tags,
            "categoryId": "24",
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": {
            "privacyStatus": PRIVACY,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        status, resp = req.next_chunk()
        if status:
            log(f"upload {int(status.progress() * 100)}%")
    vid = resp["id"]
    log(f"UPLOADED: https://youtu.be/{vid}  (privacy={resp.get('status', {}).get('privacyStatus', PRIVACY)})")

    if MODE == "long" and thumb:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
        except HttpError as e:
            log("Thumbnail skipped (channel may need phone verification):", str(e)[:150])
    return vid


# ---------------------------------------------------------------- main
def main():
    log(f"=== mode={MODE} {datetime.datetime.utcnow().isoformat()}Z ===")
    history = load_history()
    story = make_story(history)
    scenes = story["scenes"]
    voice = random.choice(VOICES)
    voices = make_voice(scenes, voice)
    images = make_images(scenes)
    video, total = build_video(images, voices, scenes)

    if MODE == "long" and total < 150:
        raise RuntimeError(f"Long video only {total:.0f}s, too short; aborting")

    thumb = images[0]
    vid = "dry-run"
    if DRY_RUN:
        log("DRY_RUN: skipping upload")
    else:
        vid = upload(video, story, thumb)

    history.append({
        "date": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "mode": MODE,
        "title": story["title"],
        "setting": story.get("setting"),
        "video_id": vid,
    })
    save_history(history)


if __name__ == "__main__":
    main()
