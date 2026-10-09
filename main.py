"""
Horror channel automation.
Usage:  python main.py short   |   python main.py long
Pipeline: Gemini story -> edge-tts Hindi voice -> AI images -> ffmpeg video -> YouTube upload
"""
import asyncio
import base64
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
import wave

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
TTS_MODELS = [
    os.environ.get("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"),
    "gemini-2.5-flash-preview-tts",
]
PRIVACY = os.environ.get("PRIVACY", "public")  # public / unlisted / private
DRY_RUN = os.environ.get("DRY_RUN") == "1"  # build video but do not upload
POLLI_KEY = os.environ.get("POLLINATIONS_KEY", "").strip()  # optional free key

GEMINI_VOICES = {"male": ["Charon", "Fenrir", "Orus"], "female": ["Kore", "Aoede", "Leda"]}
EDGE_VOICES = {"male": "hi-IN-MadhurNeural", "female": "hi-IN-SwaraNeural"}
FPS = 25

if MODE == "short":
    W, H = 1080, 1920          # video size
    IMG_W, IMG_H = 720, 1280   # size requested from image AI
    TARGET_WORDS, SCENES = 95, 8
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 66, 420, 4
else:
    W, H = 1920, 1080
    IMG_W, IMG_H = 1280, 720
    TARGET_WORDS, SCENES = 700, 24
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 60, 70, 8

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
        f"लगभग {TARGET_WORDS} शब्द, कुल {SCENES} दृश्य (scenes)। 50 सेकंड से कम में सुनाई जा सके।"
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
- कहानी एक ही सुनाने वाले की ज़ुबानी हो। पूरी कहानी में उसी के लिंग के हिसाब से क्रियाएँ लिखो (जैसे पुरुष: "मैं गया, मैंने देखा"; स्त्री: "मैं गई, मैंने देखा")। लिंग में गड़बड़ी बिल्कुल नहीं होनी चाहिए।
- ये शीर्षक पहले आ चुके हैं, इनसे अलग रखो: {recent_titles}

चित्रों के नियम (image_prompt):
- हर image_prompt अंग्रेज़ी में, एक ही साफ़ दृश्य का वर्णन करे (कौन, कहाँ, कैसी रोशनी)।
- पूरी कहानी में मुख्य पात्र का रूप, कपड़े और जगह का रंग-रूप एक जैसा बताओ ताकि चित्र आपस में मेल खाएँ।
- चित्र में कोई लिखावट, लोगो, ख़ून या डरावने शरीर के अंग नहीं।

सिर्फ़ यह JSON लौटाओ, और कुछ नहीं:
{{
 "title": "हिंदी शीर्षक, 70 अक्षर तक, रहस्यमय पर सच्चा (कोई झूठा दावा नहीं)",
 "description": "2-3 पंक्ति का हिंदी विवरण, जो कहानी का अंत न बताए",
 "tags": ["8-12 टैग हिंदी/अंग्रेज़ी"],
 "narrator_gender": "male या female (सुनाने वाले का लिंग)",
 "visual_style": "English, one line, one consistent look for ALL images, e.g. photorealistic cinematic horror film still, 35mm, volumetric fog, moody teal and amber lighting",
 "scenes": [
  {{"narration": "इस दृश्य में बोला जाने वाला हिंदी पाठ", "image_prompt": "English description of one clear scene"}}
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
    g = str(story.get("narrator_gender", "male")).lower()
    story["narrator_gender"] = "female" if g.startswith("f") else "male"
    story["visual_style"] = str(
        story.get("visual_style") or "photorealistic cinematic horror film still, 35mm, volumetric fog, moody lighting"
    )
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


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log("FFMPEG ERROR:", r.stderr[-1500:])
        raise RuntimeError("ffmpeg failed")


def gemini_tts(text, voice):
    """Returns (pcm_bytes, sample_rate). Gemini TTS gives 16-bit mono PCM."""
    prompt = "Say in a slow, low, hushed, eerie storyteller voice with natural dramatic pauses:\n" + text
    last = None
    for model in TTS_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        for attempt in range(3):
            try:
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=300)
                if r.status_code == 200:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    for p in parts:
                        inline = p.get("inlineData") or p.get("inline_data")
                        if inline and inline.get("data"):
                            mime = inline.get("mimeType") or inline.get("mime_type") or ""
                            m = re.search(r"rate=(\d+)", mime)
                            rate = int(m.group(1)) if m else 24000
                            return base64.b64decode(inline["data"]), rate
                    last = f"{model}: no audio in response"
                    log("TTS:", last)
                else:
                    last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
                    log("TTS error:", last)
                    if r.status_code in (400, 404):
                        break
                    time.sleep(20 * (attempt + 1))
            except Exception as e:  # noqa
                last = f"{model}: {e}"
                log("TTS exception:", last)
                time.sleep(10)
    raise RuntimeError(f"Gemini TTS failed: {last}")


def group_scenes(scenes, limit=1400):
    groups, cur, n = [], [], 0
    for i, sc in enumerate(scenes):
        size = len(sc["narration"])
        if cur and n + size > limit:
            groups.append(cur)
            cur, n = [], 0
        cur.append(i)
        n += size
    if cur:
        groups.append(cur)
    return groups


def make_voice_gemini(scenes, gender):
    voice = random.choice(GEMINI_VOICES[gender])
    log("Gemini TTS voice:", voice, f"({gender})")
    gap_s = 0.35
    pcm_all = bytearray()
    durs = [0.0] * len(scenes)
    rate = 24000
    for g in group_scenes(scenes):
        text = " ".join(scenes[i]["narration"] for i in g)
        pcm, rate = gemini_tts(text, voice)
        dur = len(pcm) / (2 * rate)
        total_chars = sum(len(scenes[i]["narration"]) for i in g) or 1
        for i in g:
            durs[i] = dur * len(scenes[i]["narration"]) / total_chars
        gap = b"\x00\x00" * int(rate * gap_s)
        pcm_all += pcm + gap
        durs[g[-1]] += gap_s
        time.sleep(5)
    out = WORK / "narration.wav"
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(pcm_all))
    return out, durs


async def _tts_edge(text, voice, out):
    import edge_tts

    com = edge_tts.Communicate(text, voice, rate="-8%")
    await com.save(str(out))


def make_voice_edge(scenes, gender):
    voice = EDGE_VOICES[gender]
    log("edge-tts fallback voice:", voice)
    wavs, durs = [], []
    for i, sc in enumerate(scenes):
        mp3 = WORK / f"voice_{i:02d}.mp3"
        wav = WORK / f"voice_{i:02d}.wav"
        for attempt in range(4):
            try:
                asyncio.run(_tts_edge(sc["narration"], voice, mp3))
                if mp3.exists() and mp3.stat().st_size > 1000:
                    break
            except Exception as e:  # noqa
                log("edge TTS retry:", e)
                time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"TTS failed for scene {i}")
        run(["ffmpeg", "-y", "-i", str(mp3), "-af", "apad=pad_dur=0.4", "-ar", "24000", "-ac", "1",
             "-c:a", "pcm_s16le", str(wav)])
        wavs.append(wav)
        durs.append(probe_duration(wav))
    lst = WORK / "voices.txt"
    lst.write_text("".join(f"file '{w.name}'\n" for w in wavs))
    out = WORK / "narration.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)])
    return out, durs


def make_voice(scenes, gender):
    try:
        return make_voice_gemini(scenes, gender)
    except Exception as e:  # noqa
        log("Gemini TTS not available, using fallback voice:", e)
        return make_voice_edge(scenes, gender)


def fit_short(narr, durs):
    total = sum(durs)
    if MODE != "short" or total <= 56.5:
        return narr, durs
    f = total / 56.0
    if f > 1.3:
        log(f"WARNING: short narration is {total:.0f}s; too long to speed up safely")
        return narr, durs
    out = WORK / "narration_fit.wav"
    run(["ffmpeg", "-y", "-i", str(narr), "-af", f"atempo={f:.3f}", str(out)])
    log(f"Short sped up x{f:.2f} to fit under 59s")
    return out, [d / f for d in durs]


# ---------------------------------------------------------------- images
def fetch_image(prompt, idx, seed, style):
    full = f"{prompt}, {style}, no text, no watermark, no blood, no gore"
    q = urllib.parse.quote(full)
    headers = {}
    if POLLI_KEY:
        url = (f"https://gen.pollinations.ai/image/{q}?width={IMG_W}&height={IMG_H}"
               f"&model=flux&nologo=true&seed={seed + idx}")
        headers["Authorization"] = f"Bearer {POLLI_KEY}"
        pause = 2
    else:
        # anonymous tier allows about one request per 15 seconds
        url = (f"https://image.pollinations.ai/prompt/{q}?width={IMG_W}&height={IMG_H}"
               f"&model=flux&nologo=true&seed={seed + idx}")
        pause = 16
    out = WORK / f"img_{idx:02d}.jpg"
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, timeout=180)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
                out.write_bytes(r.content)
                time.sleep(pause)
                return out
            log(f"image {idx} http {r.status_code}")
        except Exception as e:  # noqa
            log(f"image {idx} error: {e}")
        time.sleep(pause + 12 * (attempt + 1))
    return None


def make_images(scenes, style):
    seed = random.randint(1, 10**6)
    paths, failed = [], 0
    for i, sc in enumerate(scenes):
        p = fetch_image(sc["image_prompt"], i, seed, style)
        if p is None:
            failed += 1
            p = paths[-1] if paths else None
        paths.append(p)
        log(f"image {i + 1}/{len(scenes)} {'ok' if p else 'FAILED'}")
    if failed > max(1, len(scenes) * 0.25) or paths[0] is None:
        raise RuntimeError(f"Too many image failures ({failed}/{len(scenes)}); aborting to protect channel quality")
    first_good = next(p for p in paths if p)
    return [p or first_good for p in paths]


# ---------------------------------------------------------------- video
def make_clip(img, dur, idx, zoom_in):
    frames = int(dur * FPS) + 1
    step = 0.16 / max(frames, 1)
    if zoom_in:
        z = f"min(zoom+{step:.6f},1.2)"
    else:
        z = f"if(eq(on,0),1.2,max(zoom-{step:.6f},1.0))"
    sw, sh = int(W * 1.5), int(H * 1.5)
    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        f"vignette=PI/5,noise=alls=5:allf=t,fade=t=in:st=0:d=0.3,fade=t=out:st={max(dur - 0.3, 0):.2f}:d=0.3,format=yuv420p"
    )
    out = WORK / f"clip_{idx:02d}.mp4"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf", vf,
        "-t", f"{dur:.2f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", "-an", str(out),
    ])
    return out


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
        speech = max(dur - 0.3, 0.5)
        total_chars = sum(len(c) for c in chunks) or 1
        t = t0
        for c in chunks:
            d = speech * len(c) / total_chars
            lines.append(f"Dialogue: 0,{ass_time(t)},{ass_time(t + d)},Default,,0,0,0,,{c}")
            t += d
        t0 += dur
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def build_video(images, narration, durs, scenes):
    tail = 0.8
    clips = []
    for i, (img, d) in enumerate(zip(images, durs)):
        d_i = d + (tail if i == len(durs) - 1 else 0)
        clips.append(make_clip(img, d_i, i, zoom_in=(i % 2 == 0)))
        log(f"clip {i + 1}/{len(images)} done ({d_i:.1f}s)")
    listfile = WORK / "clips.txt"
    listfile.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(joined)])

    total = sum(durs) + tail
    subs = WORK / "subs.ass"
    make_subs(scenes, durs, subs)

    # quiet self-made ambience (no copyright issues); voice stays clearly on top
    f1 = random.choice([40, 45, 50, 55, 60])
    final = WORK / "final.mp4"
    fc = (
        f"[1:a]highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100,apad=pad_dur=1[a0];"
        f"[2:a]lowpass=f=250,volume=0.10[n];"
        f"[3:a]volume=0.05,tremolo=f=0.25:d=0.7[s];"
        f"[a0][n][s]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a];"
        f"[0:v]ass={subs.as_posix()}[v]"
    )
    run([
        "ffmpeg", "-y", "-i", str(joined), "-i", str(narration),
        "-f", "lavfi", "-t", f"{total + 2:.1f}", "-i", "anoisesrc=color=brown:sample_rate=44100:amplitude=0.4",
        "-f", "lavfi", "-t", f"{total + 2:.1f}", "-i", f"sine=frequency={f1}:sample_rate=44100",
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}",
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
    log(f"narrator: {story['narrator_gender']} | style: {story['visual_style'][:80]}")
    narration, durs = make_voice(scenes, story["narrator_gender"])
    narration, durs = fit_short(narration, durs)
    images = make_images(scenes, story["visual_style"])
    video, total = build_video(images, narration, durs, scenes)

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
