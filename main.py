"""
Hindi horror channel - full automation (v3)
Usage:  python main.py short   |   python main.py long
Pipeline: Gemini story -> Gemini TTS voice -> AI images -> ffmpeg video (+thumbnail) -> YouTube upload
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

# normal, clear storytelling voices (no whisper)
GEMINI_VOICES = {"male": ["Charon", "Orus", "Iapetus"], "female": ["Kore", "Erinome", "Sulafat"]}
EDGE_VOICES = {"male": "hi-IN-MadhurNeural", "female": "hi-IN-SwaraNeural"}
FPS = 25

if MODE == "short":
    W, H = 1080, 1920
    IMG_SIZES = [(1080, 1920), (720, 1280)]
    TARGET_WORDS, SCENES = 120, 12
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 66, 420, 4
    HOOK_SIZE, HOOK_MARGIN = 100, 300
else:
    W, H = 1920, 1080
    IMG_SIZES = [(1920, 1080), (1280, 720)]
    ACTS, WORDS_PER_ACT, SCENES_PER_ACT = 6, 270, 10
    TARGET_WORDS, SCENES = ACTS * WORDS_PER_ACT, ACTS * SCENES_PER_ACT
    FONT_SIZE, MARGIN_V, CHUNK_WORDS = 60, 70, 8
    HOOK_SIZE, HOOK_MARGIN = 84, 90

# ------------------------------------------------------------------ variety
SETTINGS = [
    "राजस्थान के एक वीरान किले की बुर्ज", "बिहार के गाँव का पुराना पीपल और कुआँ", "उत्तर प्रदेश की एक पुरानी हवेली",
    "बंगाल की ज़मींदार बाड़ी, जहाँ दुर्गा पूजा बरसों से बंद है", "हिमाचल के पहाड़ों में अकेला होमस्टे",
    "महाराष्ट्र का सदियों पुराना वाड़ा", "गोवा का पुर्तगाली ज़माने का सुनसान घर", "असम के चाय बागान का अंग्रेज़ी बंगला",
    "मध्य प्रदेश का भूला-बिसरा मंदिर, जंगल के बीच", "दक्षिण भारत का प्राचीन मंदिर, जहाँ रात को घंटियाँ बजती हैं",
    "गुजरात की सूखी बावड़ी (stepwell)", "राजस्थान के एक रजवाड़े का महल, जो अब होटल है", "उत्तराखंड के पहाड़ी गाँव का बंद स्कूल",
    "रेलवे का छोटा सुनसान स्टेशन, किसी कस्बे में", "ओडिशा के समुद्र किनारे का पुराना लाइट हाउस", "केरल की पुरानी तरावाड़ तथा नाग-कावु (सर्प-वन)",
    "छत्तीसगढ़ के आदिवासी गाँव का पुराना जंगल", "भूत बंगला जो शहर के बाहर हाईवे पर है", "कश्मीर घाटी की बर्फ़ में फँसा गेस्ट हाउस",
    "दिल्ली की एक पुरानी बावड़ी और खंडहर क़िला", "श्मशान के पास बना गाँव का पुराना स्कूल", "पंजाब के खेतों के बीच बनी वीरान हवेली",
    "झारखंड की बंद पड़ी कोयला खदान और उसका गाँव", "आंध्र प्रदेश के पहाड़ी किले का गुप्त तहख़ाना", "तमिलनाडु का सदियों पुराना मंदिर-तालाब",
    "हरियाणा के गाँव की सुनसान हवेली और नीम का पेड़", "सुनसान घाट, बनारस के पास गंगा किनारे", "पहाड़ी रास्ते का डाक बंगला (dak bungalow)",
    "बंद पड़ा ब्रिटिश-कालीन अस्पताल", "बीकानेर की रेतीली हवेली, जो सालों से बंद है",
]
STORY_TYPES = [
    "दादी-नानी की सुनाई गाँव की सच्ची-सी लगने वाली लोककथा",
    "एक पत्रकार/यूट्यूबर की पड़ताल, जिसमें रहस्य धीरे-धीरे खुलता है",
    "रात की ड्यूटी करने वाले इंसान (चौकीदार/नर्स/ड्राइवर) का अनुभव",
    "शहर से आए परिवार का नए घर में बीता पहला हफ़्ता",
    "यात्रा पर निकले दोस्तों के साथ हुई अनहोनी",
    "एक पुरानी चिट्ठी/डायरी मिलने से खुलता राज़",
    "गाँव का एक अजीब नियम और उसे तोड़ने का नतीजा",
    "रहस्यमयी कहानी जिसमें अंत में पता चलता है कि असली डर कुछ और ही था",
    "पुराने ज़माने के राजा-रानी/ज़मींदार से जुड़ा श्राप और आज का इंसान",
]
LOOKS = [
    "photorealistic cinematic film still, natural colors, 35mm lens",
    "documentary photography, Kodak Portra film tones, soft grain",
    "high-contrast cinematic thriller look, teal and orange grading",
    "vintage 1990s Indian cinema look, warm film grain",
    "desaturated monsoon-overcast cinematic look, wet textures",
    "bright harsh daylight horror look, washed colors, deep shadows",
    "moody painterly cinematic realism, rich textures",
]
LIGHT_ARCS = [
    ["bright harsh midday sun", "golden hour sunlight", "overcast grey sky", "dusk blue hour", "lantern-lit interior", "moonlit night"],
    ["early morning mist", "soft morning sunlight", "bright daylight", "hazy afternoon", "stormy monsoon clouds", "dim candle-lit interior"],
    ["bright daylight", "bright daylight", "golden hour", "dusk", "dim interior with flickering light", "night"],
    ["dawn light", "bright sunlight", "afternoon shade under trees", "sunset glow", "twilight", "lamp-lit night"],
]


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ history
def load_history():
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(h):
    HISTORY_FILE.write_text(json.dumps(h[-300:], ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ story
RULES = """ज़रूरी नियम (YouTube विज्ञापन-अनुकूल रहने के लिए):
- डर और तनाव माहौल, आवाज़ों, परछाइयों और अनिश्चितता से बनाओ। ख़ून-ख़राबा, गोर, शरीर के अंगों का वर्णन, यातना नहीं।
- कोई असली व्यक्ति, असली घटना, किसी असली जगह/समुदाय की बदनामी, धर्म/जाति पर टिप्पणी नहीं। पात्रों के नाम काल्पनिक रखो।
- आत्महत्या, ख़ुद को नुकसान, यौन सामग्री, बच्चों को नुकसान, गाली-गलौज नहीं।
- सरल, बोलचाल की हिंदी (देवनागरी), छोटे वाक्य, जो सुनने में स्वाभाविक लगें। भाव और जज़्बात असली लगें, ताकि सुनने वाला कहानी से जुड़ जाए।
- कहानी एक ही सुनाने वाले की ज़ुबानी हो। पूरी कहानी में उसी के लिंग के हिसाब से क्रियाएँ लिखो (पुरुष: "मैं गया, मैंने देखा"; स्त्री: "मैं गई, मैंने देखा")। लिंग में गड़बड़ी नहीं।
- भारतीय माहौल, भारतीय नाम, बोलचाल और रीति-रिवाज़ दिखें।
- चित्रों के नियम: हर image_prompt अंग्रेज़ी में, एक ही साफ़ दृश्य का वर्णन (कौन, कहाँ, क्या हो रहा है)। हर चित्र पिछले से अलग हो (अलग कोण, अलग जगह, अलग चीज़)। चित्र में कोई लिखावट, लोगो, ख़ून या डरावने शरीर के अंग नहीं। रोशनी का ज़िक्र मत करो, वह अपने आप जुड़ेगी।"""

META_JSON = """ "title": "हिंदी शीर्षक, 70 अक्षर तक, रहस्यमय पर सच्चा (कोई झूठा दावा नहीं)",
 "description": "2-3 पंक्ति का हिंदी विवरण, जो कहानी का अंत न बताए",
 "tags": ["8-12 टैग हिंदी/अंग्रेज़ी"],
 "narrator_gender": "male या female (सुनाने वाले का लिंग)",
 "characters": "English: one line describing the main character's look (age, clothes, hair) so every image matches",
 "hook_text": "स्क्रीन पर पहले 3 सेकंड में दिखने वाली 3-6 शब्दों की चौंकाने वाली हिंदी पंक्ति",
 "thumb_line1": "थंबनेल की छोटी पंक्ति (2-4 हिंदी शब्द)",
 "thumb_line2": "थंबनेल की बड़ी पंक्ति (2-4 हिंदी शब्द, सबसे डरावनी/जिज्ञासा वाली)",
 "thumb_prompt": "English: a dramatic YouTube thumbnail picture, close-up of a frightened Indian person in the foreground and a mysterious figure or the haunted location behind, no text" """


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
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=240)
                if r.status_code == 200:
                    return r.json()["candidates"][0]["content"]["parts"][0]["text"]
                last_err = f"{model}: HTTP {r.status_code} {r.text[:300]}"
                log("Gemini error:", last_err)
                if r.status_code in (404, 400):
                    break
                time.sleep(15 * (attempt + 1))
            except Exception as e:  # noqa
                last_err = f"{model}: {e}"
                log("Gemini exception:", last_err)
                time.sleep(10)
    raise RuntimeError(f"Gemini failed: {last_err}")


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        return json.loads(m.group(0))


def clean_scenes(raw):
    out = []
    for s in raw or []:
        if s.get("narration") and s.get("image_prompt"):
            out.append({
                "narration": str(s["narration"]).strip(),
                "image_prompt": str(s["image_prompt"]).strip(),
                "has_character": bool(s.get("has_character", False)),
            })
    return out


def word_count(scenes):
    return sum(len(s["narration"].split()) for s in scenes)


def finalize_meta(story, setting, stype):
    g = str(story.get("narrator_gender", "male")).lower()
    story["narrator_gender"] = "female" if g.startswith("f") else "male"
    story["characters"] = str(story.get("characters") or "an Indian person in simple clothes")
    story["hook_text"] = str(story.get("hook_text") or "").strip()[:45]
    story["thumb_line1"] = str(story.get("thumb_line1") or "").strip()[:30]
    story["thumb_line2"] = str(story.get("thumb_line2") or story.get("title", "")).strip()[:30]
    story["thumb_prompt"] = str(
        story.get("thumb_prompt") or "frightened Indian person close-up, mysterious figure in the background"
    )
    story["look"] = random.choice(LOOKS)
    story["arc"] = random.choice(LIGHT_ARCS)
    story["setting"] = setting
    story["type"] = stype
    return story


def short_prompt(setting, stype, recent):
    return f"""तुम भारत के एक अनुभवी हिंदी हॉरर कहानीकार हो। एक बिल्कुल नई, मौलिक, काल्पनिक हॉरर कहानी लिखो, YouTube Shorts के लिए।

स्थान/माहौल: {setting}
कहानी का ढंग: {stype}
लंबाई: कुल {TARGET_WORDS} शब्द (110 से 135 के बीच), ठीक {SCENES} दृश्य (scenes), जो आराम से बोलने पर 50 सेकंड के आसपास चले।

पहली पंक्ति बहुत छोटी और चौंकाने वाली हो (3 सेकंड में बोली जा सके), ताकि देखने वाला रुक जाए। हर दृश्य के अंत में हल्का सस्पेंस रखो और अंत में दमदार मोड़ या अधूरा सवाल छोड़ो।

{RULES}
- ये शीर्षक पहले आ चुके हैं, इनसे अलग रखो: {recent}

सिर्फ़ यह JSON लौटाओ, और कुछ नहीं:
{{
{META_JSON},
 "scenes": [
  {{"narration": "इस दृश्य में बोला जाने वाला हिंदी पाठ", "image_prompt": "English description of one clear scene", "has_character": true}}
 ]
}}"""


def outline_prompt(setting, stype, recent):
    return f"""तुम भारत के एक अनुभवी हिंदी हॉरर कहानीकार हो। लगभग 10-11 मिनट सुनाई जाने वाली, बिल्कुल नई, मौलिक, काल्पनिक हॉरर कहानी की रूपरेखा बनाओ।

स्थान/माहौल: {setting}
कहानी का ढंग: {stype}
कहानी {ACTS} भागों (acts) में बँटी हो: शुरुआत और हुक, माहौल/पात्र, पहला अजीब संकेत, तनाव बढ़ना, चरम (climax), चौंकाने वाला अंत। हर भाग का सारांश 3-4 वाक्यों में लिखो।

{RULES}
- ये शीर्षक पहले आ चुके हैं, इनसे अलग रखो: {recent}

सिर्फ़ यह JSON लौटाओ:
{{
{META_JSON},
 "acts": [ {{"title": "भाग का नाम", "summary": "इस भाग में क्या होगा, 3-4 वाक्य"}} ]
}}"""


def act_prompt(story, i, prior_text):
    acts_txt = "\n".join(f"{k + 1}. {a['title']}: {a['summary']}" for k, a in enumerate(story["acts"]))
    if i == 0:
        extra = "यह पहला भाग है। पहली पंक्ति बहुत छोटी और चौंकाने वाली हो (हुक)।"
    elif i == ACTS - 1:
        extra = "यह आख़िरी भाग है। अंत दमदार, चौंकाने वाला मोड़ वाला हो, और आख़िरी वाक्य दर्शक के ज़ेहन में रह जाए।"
    else:
        extra = "पिछले भाग से बिना दोहराए सीधे आगे बढ़ो, और इस भाग के अंत में सस्पेंस रखो।"
    prev = f"अब तक की कहानी का आख़िरी हिस्सा:\n{prior_text[-900:]}\n" if prior_text else ""
    return f"""कहानी: "{story['title']}" (सुनाने वाला: {'पुरुष' if story['narrator_gender'] == 'male' else 'स्त्री'}).
पूरी रूपरेखा:
{acts_txt}

{prev}
अब सिर्फ़ भाग {i + 1} ("{story['acts'][i]['title']}") की पूरी कहानी लिखो: लगभग {WORDS_PER_ACT} शब्द, ठीक {SCENES_PER_ACT} दृश्य (scenes)। {extra}

{RULES}

सिर्फ़ यह JSON लौटाओ:
{{"scenes": [{{"narration": "इस दृश्य में बोला जाने वाला हिंदी पाठ", "image_prompt": "English description of one clear scene", "has_character": true}}]}}"""


def make_story_short(setting, stype, recent):
    for attempt in range(4):
        try:
            data = parse_json(call_gemini(short_prompt(setting, stype, recent)))
            data["scenes"] = clean_scenes(data.get("scenes"))
            words = word_count(data["scenes"])
            log(f"Short story: '{data.get('title')}' scenes={len(data['scenes'])} words={words}")
            if len(data["scenes"]) < 6 or words < 95 or words > 150:
                log("Length out of range, retrying")
                continue
            return finalize_meta(data, setting, stype)
        except Exception as e:  # noqa
            log("Story problem:", e)
    raise RuntimeError("Could not produce a valid short story")


def make_story_long(setting, stype, recent):
    story = None
    for attempt in range(3):
        try:
            story = parse_json(call_gemini(outline_prompt(setting, stype, recent)))
            if len(story.get("acts", [])) >= ACTS:
                story["acts"] = story["acts"][:ACTS]
                break
        except Exception as e:  # noqa
            log("Outline problem:", e)
        story = None
    if not story:
        raise RuntimeError("Could not produce a story outline")
    story = finalize_meta(story, setting, stype)
    scenes, prior = [], ""
    for i in range(ACTS):
        part = None
        for attempt in range(3):
            try:
                data = parse_json(call_gemini(act_prompt(story, i, prior)))
                cand = clean_scenes(data.get("scenes"))
                w = word_count(cand)
                log(f"act {i + 1}/{ACTS}: scenes={len(cand)} words={w}")
                if len(cand) >= 6 and w >= WORDS_PER_ACT * 0.6:
                    part = cand
                    break
            except Exception as e:  # noqa
                log("Act problem:", e)
        if not part:
            raise RuntimeError(f"Could not write act {i + 1}")
        scenes += part
        prior += " ".join(s["narration"] for s in part) + " "
    story["scenes"] = scenes
    log(f"Long story: '{story['title']}' scenes={len(scenes)} words={word_count(scenes)}")
    return story


def make_story(history):
    recent = [h["title"] for h in history[-40:]]
    used_settings = [h.get("setting") for h in history[-14:]]
    used_types = [h.get("type") for h in history[-6:]]
    setting = random.choice([s for s in SETTINGS if s not in used_settings] or SETTINGS)
    stype = random.choice([t for t in STORY_TYPES if t not in used_types] or STORY_TYPES)
    log("setting:", setting, "| type:", stype)
    if MODE == "short":
        return make_story_short(setting, stype, recent)
    return make_story_long(setting, stype, recent)


# ------------------------------------------------------------------ voice
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
    prompt = (
        "Read the following Hindi story aloud like a skilled, natural Indian storyteller. "
        "Use a normal, clear speaking voice at normal conversational volume and pace. "
        "Be warm and emotionally expressive, with real feeling and short natural pauses. "
        "Do NOT whisper and do not sound robotic:\n" + text
    )
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


def group_scenes(scenes, limit=2200):
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
    gap_s = 0.3
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

    com = edge_tts.Communicate(text, voice, rate="+0%")
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
        run(["ffmpeg", "-y", "-i", str(mp3), "-af", "apad=pad_dur=0.3", "-ar", "24000", "-ac", "1",
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
    """Keep shorts between ~46 and ~56.5 seconds (so total stays under 59s)."""
    total = sum(durs)
    if MODE != "short" or 46 <= total <= 56.5:
        return narr, durs
    f = total / 56.0 if total > 56.5 else total / 47.0
    if not (0.85 <= f <= 1.22):
        log(f"WARNING: short narration is {total:.0f}s; cannot be adjusted safely")
        return narr, durs
    out = WORK / "narration_fit.wav"
    run(["ffmpeg", "-y", "-i", str(narr), "-af", f"atempo={f:.3f}", str(out)])
    log(f"Narration tempo x{f:.2f} ({total:.0f}s -> {total / f:.0f}s)")
    return out, [d / f for d in durs]


# ------------------------------------------------------------------ images
_PREF_SIZE = {}


def _download(url, headers, out):
    try:
        r = requests.get(url, headers=headers, timeout=240)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 5000:
            out.write_bytes(r.content)
            return True, 200
        return False, r.status_code
    except Exception as e:  # noqa
        log("image request error:", e)
        return False, 0


def get_image(full_prompt, seed, out, sizes):
    """Try the biggest size first; fall back to smaller sizes if the service refuses."""
    key = tuple(sizes)
    q = urllib.parse.quote(full_prompt)
    headers = {}
    if POLLI_KEY:
        headers["Authorization"] = f"Bearer {POLLI_KEY}"
        base, pause = "https://gen.pollinations.ai/image/", 2
    else:
        base, pause = "https://image.pollinations.ai/prompt/", 16  # anonymous: ~1 request / 15 s
    idx = _PREF_SIZE.get(key, 0)
    while idx < len(sizes):
        w, h = sizes[idx]
        url = f"{base}{q}?width={w}&height={h}&model=flux&nologo=true&seed={seed}"
        size_problem = False
        for attempt in range(3):
            ok, code = _download(url, headers, out)
            if ok:
                _PREF_SIZE[key] = idx
                time.sleep(pause)
                return True
            log(f"image http {code} (size {w}x{h}, try {attempt + 1})")
            if code in (400, 413, 422):
                size_problem = True
                break
            time.sleep(pause + 12 * (attempt + 1))
        if not size_problem:
            return False
        idx += 1
    return False


def light_for(i, n, arc):
    return arc[min(int(i / max(n, 1) * len(arc)), len(arc) - 1)]


def make_images(story):
    scenes = story["scenes"]
    n = len(scenes)
    seed = random.randint(1, 10**6)
    paths, failed = [], 0
    for i, sc in enumerate(scenes):
        light = light_for(i, n, story["arc"])
        who = f" Main character: {story['characters']}." if sc["has_character"] else ""
        full = (f"{sc['image_prompt']}.{who} Lighting: {light}. {story['look']}. "
                "Authentic Indian setting, ultra detailed, sharp focus, high resolution, "
                "no text, no watermark, no gore, no blood")
        out = WORK / f"img_{i:03d}.jpg"
        ok = get_image(full, seed + i, out, IMG_SIZES)
        p = out if ok else (paths[-1] if paths else None)
        if not ok:
            failed += 1
        paths.append(p)
        log(f"image {i + 1}/{n} {'ok' if ok else 'FAILED'} [{light}]")
    if failed > max(1, n * 0.2) or paths[0] is None:
        raise RuntimeError(f"Too many image failures ({failed}/{n}); aborting to protect channel quality")
    first_good = next(p for p in paths if p)
    return [p or first_good for p in paths]


def make_thumbnail(story):
    """AI picture + big Hindi text on top. Returns path or None."""
    try:
        bg = WORK / "thumb_bg.jpg"
        prompt = (f"{story['thumb_prompt']}. {story['characters']}. Dramatic cinematic lighting, high contrast, "
                  "vivid colors, extremely detailed, YouTube thumbnail composition with empty space at the top, "
                  "no text, no watermark, no gore, no blood")
        if not get_image(prompt, random.randint(1, 10**6), bg, [(1280, 720)]):
            return None
        ass = WORK / "thumb.ass"
        l1, l2 = story["thumb_line1"], story["thumb_line2"]
        ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Small,Noto Sans Devanagari,70,&H0000FFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,2,8,40,40,22,1
Style: Big,Noto Sans Devanagari,118,&H000000FF,&H000000FF,&H00FFFFFF,&H80000000,1,0,0,0,100,100,0,0,1,8,3,8,40,40,95,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
Dialogue: 0,0:00:00.00,0:00:05.00,Small,,0,0,0,,{l1}
Dialogue: 1,0:00:00.00,0:00:05.00,Big,,0,0,0,,{l2}
""", encoding="utf-8")
        out = WORK / "thumb.jpg"
        run(["ffmpeg", "-y", "-i", str(bg), "-vf",
             f"scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,eq=contrast=1.08:saturation=1.15,ass={ass.as_posix()}",
             "-frames:v", "1", "-q:v", "3", str(out)])
        return out
    except Exception as e:  # noqa
        log("Thumbnail skipped:", e)
        return None


# ------------------------------------------------------------------ video
def make_clip(img, dur, idx, variant):
    frames = int(dur * FPS) + 1
    step = 0.18 / max(frames, 1)
    if variant == 0:    # slow zoom in
        z, x, y = f"min(zoom+{step:.6f},1.22)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif variant == 1:  # slow zoom out
        z, x, y = f"if(eq(on,0),1.22,max(zoom-{step:.6f},1.0))", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif variant == 2:  # pan left to right
        z, x, y = "1.18", f"(iw-iw/zoom)*on/{frames}", "ih/2-(ih/zoom/2)"
    else:               # pan right to left
        z, x, y = "1.18", f"(iw-iw/zoom)*(1-on/{frames})", "ih/2-(ih/zoom/2)"
    sw, sh = int(W * 1.5), int(H * 1.5)
    vf = (
        f"scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={FPS},"
        f"unsharp=5:5:0.6,vignette=PI/6,fade=t=in:st=0:d=0.25,fade=t=out:st={max(dur - 0.25, 0):.2f}:d=0.25,format=yuv420p"
    )
    out = WORK / f"clip_{idx:03d}.mp4"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf", vf,
        "-t", f"{dur:.2f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an", str(out),
    ])
    return out


def ass_time(t):
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def make_subs(story, durs, path):
    scenes = story["scenes"]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Noto Sans Devanagari,{FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,60,60,{MARGIN_V},1
Style: Hook,Noto Sans Devanagari,{HOOK_SIZE},&H0000FFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,7,2,8,60,60,{HOOK_MARGIN},1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    lines, t0 = [], 0.0
    if story.get("hook_text"):
        lines.append(f"Dialogue: 1,{ass_time(0.0)},{ass_time(3.2)},Hook,,0,0,0,,{{\\fad(150,350)}}{story['hook_text']}")
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


def build_video(images, narration, durs, story):
    tail = 0.8
    offset = random.randint(0, 3)
    clips = []
    for i, (img, d) in enumerate(zip(images, durs)):
        d_i = d + (tail if i == len(durs) - 1 else 0)
        clips.append(make_clip(img, d_i, i, (i + offset) % 4))
        if i % 5 == 0 or i == len(images) - 1:
            log(f"clip {i + 1}/{len(images)} done ({d_i:.1f}s)")
    listfile = WORK / "clips.txt"
    listfile.write_text("".join(f"file '{c.name}'\n" for c in clips))
    joined = WORK / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(joined)])

    total = sum(durs) + tail
    subs = WORK / "subs.ass"
    make_subs(story, durs, subs)

    # very soft self-made ambience (wind + low drone); the voice stays clearly on top
    f1 = random.choice([40, 45, 50, 55, 60])
    final = WORK / "final.mp4"
    fc = (
        f"[1:a]highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100,apad=pad_dur=1[a0];"
        f"[2:a]bandpass=f=500:width_type=h:w=600,tremolo=f=0.12:d=0.8,volume=0.05[w];"
        f"[3:a]volume=0.03,tremolo=f=0.25:d=0.7[s];"
        f"[a0][w][s]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.95[a];"
        f"[0:v]ass={subs.as_posix()}[v]"
    )
    run([
        "ffmpeg", "-y", "-i", str(joined), "-i", str(narration),
        "-f", "lavfi", "-t", f"{total + 2:.1f}", "-i", "anoisesrc=color=pink:sample_rate=44100:amplitude=0.5",
        "-f", "lavfi", "-t", f"{total + 2:.1f}", "-i", f"sine=frequency={f1}:sample_rate=44100",
        "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.2f}",
        str(final),
    ])
    log(f"Final video: {total:.0f}s -> {final}")
    return final, total


# ------------------------------------------------------------------ upload
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
    desc += "\n👉 ऐसी और डरावनी कहानियों के लिए चैनल को सब्सक्राइब करें और घंटी का बटन दबाएँ।"
    tags = [str(t)[:30] for t in story.get("tags", [])][:15]
    if MODE == "short":
        title = (title[:88] + " #Shorts") if "#shorts" not in title.lower() else title[:100]
        desc += "\n\n#Shorts #HindiHorror #HorrorStory #भूतियाकहानी"
        tags += ["shorts"]
    else:
        title = title[:100]
        desc += "\n\n#HindiHorror #HorrorStory #हॉररकहानी #भूतियाकहानी"

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
            log("Thumbnail set")
        except HttpError as e:
            log("Thumbnail skipped (channel may need phone verification):", str(e)[:150])
    return vid


# ------------------------------------------------------------------ main
def main():
    log(f"=== mode={MODE} {datetime.datetime.utcnow().isoformat()}Z ===")
    history = load_history()
    story = narration = durs = None
    for attempt in range(3 if MODE == "short" else 1):
        story = make_story(history)
        scenes = story["scenes"]
        log(f"narrator: {story['narrator_gender']} | look: {story['look'][:50]} | hook: {story['hook_text']}")
        narration, durs = make_voice(scenes, story["narrator_gender"])
        narration, durs = fit_short(narration, durs)
        total = sum(durs) + 0.8
        if MODE == "long":
            break
        if 45 <= total <= 58.5:
            break
        log(f"Short is {total:.0f}s (needs 45-58s), trying a new story")
    total = sum(durs) + 0.8
    if MODE == "short" and not (44 <= total <= 59):
        raise RuntimeError(f"Short length {total:.0f}s is outside 45-58s; aborting")
    if MODE == "long" and total < 540:
        raise RuntimeError(f"Long video only {total:.0f}s (needs 9+ min); aborting")

    images = make_images(story)
    thumb = make_thumbnail(story) if MODE == "long" else None
    video, total = build_video(images, narration, durs, story)

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
        "type": story.get("type"),
        "look": story.get("look"),
        "seconds": round(total),
        "video_id": vid,
    })
    save_history(history)


if __name__ == "__main__":
    main()
