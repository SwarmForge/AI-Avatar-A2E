# A2E Avatar → TTS → Video Pipeline

This guide explains, end‑to‑end, how to generate a talking‑head video with the A2E API using the provided Python script. It covers environment setup, CLI usage, the underlying API calls, how to customize outputs (voices, backgrounds, resolution, layout), and common troubleshooting tips.

---

## Prerequisites

- **Python 3.9+**
- **Packages**: `requests`, `python-dotenv`
- **A2E API token** with permission to access avatar/video endpoints

Install deps:

```bash
pip install requests python-dotenv

```

Create a `.env` file in your project root:

```bash
A2E_API_TOKEN=your_token_here
# Optional (defaults shown)
A2E_BASE=https://video.a2e.ai

```

> The script loads environment variables via python-dotenv when run_pipeline() starts.
> 

---

## Quickstart (CLI)

Run the pipeline with sane defaults:

```bash
python a2e_avatar_pipeline.py \
  --name "Second Emirate Guy" \
  --gender male \
  --prompt "A professional male avatar, studio lighting, neutral background" \
  --script "Hello, I'm your content creator assistant..." \
  --voice-id 6625ebd4613f49985c349f95 \
  --auto-approve \
  --lip-sync

```

What you’ll see:

1. A generated portrait image saved locally (e.g., `avatar.jpg`).
2. Avatar training kicked off with that image.
3. Optional follow‑up `continueTranining` step if `-lip-sync` is used.
4. TTS audio created from your script and voice.
5. Video generation started using your avatar’s **custom anchor** plus the TTS audio.
6. A best‑guess video URL printed when available (MP4/M3U8/etc.).

---

## CLI reference

```
--name (str)           Display name for the avatar/persona. (required)
--gender (male|female) Gender string the API expects. (required)
--prompt (str)         Prompt for the initial Text→Image portrait. (required)
--script (str)         Narration text for TTS. (required)
--voice-id (str)       TTS voice ID (see Voice List). (required)
--width (int)          Portrait width; default 1024.
--height (int)         Portrait height; default 768.
--auto-approve         Skip Y/N prompt before avatar training.
--lip-sync             Trigger a follow‑up training step mapped to `continueTranining`.

```

### Choosing a voice

Use the helper below to list voices, then pass the chosen `_id` as `--voice-id`.

```python
from a2e_avatar_pipeline import A2EClient
client = A2EClient(token="...", base="https://video.a2e.ai")
voices = client.list_voices(country="en", region="US", voice_map_type="en-US")
print(voices)

```

---

## How it works (step‑by‑step)

### 1) Text → Image

- **Endpoint:** `POST /api/v1/userText2image/start`
- **Inputs:** `name`, `prompt`, `req_key` (model key), `width`, `height`
- **Output:** JSON containing `data.image_urls[]`
- **Script behavior:** Picks the first image URL, downloads it locally (e.g., `avatar.jpg`).

**Tips**

- Use a portrait‑style prompt with well‑lit, centered face.
- Adjust `width`/`height` for portrait framing (e.g., 1024×1536) if your account supports it.

### 2) Approval gate

- If `-auto-approve` is **not** set, the script asks to proceed before training.

### 3) Train a custom avatar (Video Twin)

- **Endpoint:** `POST /api/v1/userVideoTwin/startTraining`
- **Inputs:** `name`, `gender`, `image_url`, optional: `video_backgroud_color`, `video_backgroud_image`, `model_version` (`V2.0`/`V2.1`), `prompt`, `negative_prompt`, `skipPreview`.
- **Output:** JSON with `data._id` → this is your **avatar_id**.

The script then **polls**:

- **Endpoint:** `GET /api/v1/userVideoTwin/{avatar_id}`
- Reads `data.current_status` until it reaches a terminal OK state (`ready`, `trained`, `succeeded`, `completed`) or fails/timeouts.
- Defaults: up to **30 minutes** (`timeout_s=1800`) polling every **20s**.

### 4) Optional: Lip‑sync follow‑up

- **Flag:** `-lip-sync`
- **Endpoint:** `POST /api/v1/userVideoTwin/continueTranining`
- **Input:** `{ "_id": avatar_id }`
- The script triggers this step then (optionally) checks readiness again.

### 5) Generate TTS audio

- **Endpoint:** `POST /api/v1/video/send_tts`
- **Inputs:** `msg` (script), `tts_id` (voice id), `speechRate`, `country`, `region`
- **Output:** Audio URL (e.g., `.mp3/.aac/.wav/.m4a`).
- The script extracts `audioSrc` by scanning the JSON for the first audio‑like URL.

### 6) Generate talking‑head video

1. **Find the custom anchor** tied to your avatar:
    - **Endpoint:** `GET /api/v1/anchor/character_list?user_video_twin_id=...&type=custom`
    - Output items include `_id` — this becomes `anchor_id`.
2. **Create the video**:
    - **Endpoint:** `POST /api/v1/video/generate`
    - **Key inputs:**
        - `title`: free‑text title
        - `anchor_id`: from step 1
        - `anchor_type`: `1` (custom)
        - `audioSrc`: URL from TTS step
        - **Resolution & layout** (defaults shown):
            - `resolution`: `1080`
            - `web_bg_width`: `853`, `web_bg_height`: `480`
            - `web_people_width`: `270`, `web_people_height`: `480`
            - `web_people_x`: `292`, `web_people_y`: `0`
            - `isSkipRs`: `true` (render‑speed hint)
            - `isCaptionEnabled`: `false`
3. **Fetch result**:
    - **Endpoint:** `POST /api/v1/video/awsResult`
    - **Input:** `{ "_id": task_id }`
    - Script prints the first plausible **video URL** it finds (e.g., `.mp4/.m3u8/.mov/.webm`).

---

## API reference (as used by the script)

- `POST /api/v1/userText2image/start` → start image generation
- `POST /api/v1/userVideoTwin/startTraining` → start avatar training (returns `data._id`)
- `GET /api/v1/userVideoTwin/{avatar_id}` → avatar status (`data.current_status`)
- `POST /api/v1/userVideoTwin/continueTranining` → optional follow‑up training
- `GET /api/v1/anchor/voice_list` → list voices
- `POST /api/v1/video/send_tts` → synthesize speech (returns/contains audio URL)
- `GET /api/v1/anchor/character_list?user_video_twin_id=<id>&type=custom` → list **custom anchors** for the avatar
- `POST /api/v1/video/generate` → create video task (returns `data._id`)
- `POST /api/v1/video/awsResult` → fetch video result/URL(s)

---

## Example: End‑to‑end log (annotated)

```
[1/6] Generating image from prompt…
== text2image response == { ... "image_urls": ["https://.../avatar.jpg"] }
Downloading to: avatar.jpg … Saved -> /path/avatar.jpg

[2/6] Starting avatar training from image URL…
== startTraining response == { "code": 0, "data": { "_id": "abc123" } }
[3/6] Waiting for avatar to be ready…
[avatar:abc123] status = trained
== avatar ready info == { ... }

[4/6] Triggering follow-up lip-sync training (continueTranining)…
== continueTranining response == { "code": 0, ... }

[5/6] Generating TTS audio…
== send_tts response == { ... "data": "https://.../speech.mp3" }

[6/6] Fetching custom anchors for this avatar…
== character_list (custom) response == { "data": [{"_id": "anchor789", ...}] }
== video generate response == { "code": 0, "data": { "_id": "task456" } }
== video awsResult response == { ... "url": "https://.../video.mp4" }

✅ Best-guess video URL: https://.../video.mp4

```

---