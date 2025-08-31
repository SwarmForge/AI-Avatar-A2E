"""
A2E Avatar → TTS → Video pipeline

Requirements:
  pip install requests python-dotenv

Environment:
  A2E_API_TOKEN = your token string
  (optional) A2E_BASE = https://video.a2e.ai

Usage examples:
  python a2e_avatar_pipeline.py \
    --name "Second Emirate Guy" \
    --gender male \
    --prompt "A professional male avatar..." \
    --script "Hello, I'm your content creator assistant..." \
    --voice-id 6625ebd4613f49985c349f95 \
    --auto-approve \
    --lip-sync

Flow:
  1) Text → Image (saves the picture locally)
  2) Ask for approval (or pass --auto-approve)
  3) Train a custom avatar from the image URL
  4) (optional --lip-sync) trigger a follow-up training step
  5) Generate TTS audio from your script & chosen voice
  6) Generate talking-head video and print the best-guess video URL

Notes:
  • The API schemas sometimes vary; this script errs on the side of robust parsing and logs raw responses
    when fields aren’t found. Adjust parsing if your account returns different shapes.
  • "Lip sync" here is mapped to A2E’s "continueTranining" endpoint provided in your snippet.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import requests
from dotenv import load_dotenv


# ----------------------------
# Utilities
# ----------------------------

def _env(key: str, default: Optional[str] = None) -> str:
    v = os.getenv(key, default)
    if v is None:
        raise RuntimeError(f"Missing required env var: {key}")
    return v


def _print_json(title: str, obj: Any) -> None:
    print(f"\n== {title} ==")
    try:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    except Exception:
        print(obj)


def _first_url_from(obj: Any, exts: Iterable[str] = (".mp4", ".m3u8", ".mov", ".webm", ".mp3", ".aac", ".wav", ".jpg", ".jpeg", ".png")) -> Optional[str]:
    """Walk an arbitrary JSON-like structure and find the first URL, preferring certain extensions."""
    urls: list[str] = []

    def collect(o: Any) -> None:
        if isinstance(o, dict):
            for v in o.values():
                collect(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                collect(v)
        elif isinstance(o, str):
            for m in re.findall(r"https?://[^\s\"']+", o):
                urls.append(m)

    collect(obj)

    # Prefer by extension order
    for ext in exts:
        for u in urls:
            if u.lower().split("?", 1)[0].endswith(ext):
                return u
    return urls[0] if urls else None


# ----------------------------
# Client
# ----------------------------
@dataclass
class A2EClient:
    token: str
    base: str = "https://video.a2e.ai"
    timeout: int = 60

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "a2e-client/1.0",
        })
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "a2e-client/1.0",
            "x-lang": "en-US",                 # <-- add this (backend sometimes requires it)
            "Content-Type": "application/json" # <-- not strictly required with json=, but harmless
        })

    # ---- Text → Image ----
    def text2image(self, name: str, prompt: str, width: int = 1024, height: int = 768,
                   req_key: str = "high_aes_general_v21_L") -> Dict[str, Any]:
        url = f"{self.base}/api/v1/userText2image/start"
        body = {
            "name": name,
            "prompt": prompt,
            "req_key": req_key,
            "width": width,
            "height": height,
        }
        r = self.session.post(url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- Avatar (Video Twin) ----
    def start_avatar_training_from_image(
        self,
        name: str,
        gender: str,
        image_url: str,
        *,
        bg_color: Optional[str] = None,
        bg_image_url: Optional[str] = None,
        model_version: Optional[str] = None,
        prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        skip_preview: Optional[bool] = None,
    ) -> Dict[str, Any]:
# --- at top of start_avatar_training_from_image ---
        if " " in image_url:
            raise ValueError("image_url must not contain spaces; URL-encode them as %20.")        
        url = f"{self.base}/api/v1/userVideoTwin/startTraining"
        body: Dict[str, Any] = {
            "name": name,
            "gender": gender,
            "image_url": image_url,
        }
        if bg_color:
            body["video_backgroud_color"] = bg_color
        if bg_image_url:
            body["video_backgroud_image"] = bg_image_url
        if model_version in {"V2.0", "V2.1"}:
            body["model_version"] = model_version
        if prompt:
            body["prompt"] = prompt
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if skip_preview is not None:
            body["skipPreview"] = bool(skip_preview)

        # r = self.session.post(url, json=body, timeout=self.timeout)
        # r.raise_for_status()
        # return r.json()

        # --- replace the tail of start_avatar_training_from_image ---
        r = self.session.post(url, json=body, timeout=self.timeout)

        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            raise requests.HTTPError(f"{e}\nResponse text: {r.text}") from None

        data = r.json()
        # Surface API-level errors (A2E wraps results as {"code": ..., "data": ..., "msg": ...})
        if isinstance(data, dict) and data.get("code") not in (0, None):
            raise RuntimeError(f"A2E API error (code={data.get('code')}): {data.get('msg') or data.get('message')}")
        return data

    def get_avatar(self, avatar_id: str) -> Dict[str, Any]:
        url = f"{self.base}/api/v1/userVideoTwin/{avatar_id}"
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def continue_training(self, avatar_id: str) -> Dict[str, Any]:
        """Follow-up training step (mapped to 'continueTranining' from your snippet)."""
        url = f"{self.base}/api/v1/userVideoTwin/continueTranining"
        r = self.session.post(url, json={"_id": avatar_id}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def wait_until_avatar_ready(self, avatar_id: str, timeout_s: int = 1800, poll_s: int = 20) -> Dict[str, Any]:
        start = time.time()
        terminal_ok = {"ready", "trained", "succeeded", "completed"}
        terminal_bad = {"failed", "error"}
        while True:
            item = self.get_avatar(avatar_id)
            status = (item.get("data") or {}).get("current_status")
            print(f"[avatar:{avatar_id}] status = {status}")
            if status in terminal_ok:
                return item
            if status in terminal_bad:
                raise RuntimeError(f"Avatar training failed: {json.dumps(item, ensure_ascii=False)}")
            if time.time() - start > timeout_s:
                raise TimeoutError(f"Timed out waiting for avatar {avatar_id} to be ready")
            time.sleep(poll_s)

    # ---- Voices & TTS ----
    def list_voices(self, country: str = "en", region: str = "US", voice_map_type: str = "en-US") -> Dict[str, Any]:
        url = f"{self.base}/api/v1/anchor/voice_list"
        params = {"country": country, "region": region, "voice_map_type": voice_map_type}
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def generate_tts(self, msg: str, voice_id: str, *, country: str = "en", region: str = "US", speech_rate: float = 1.0) -> Dict[str, Any]:
        url = f"{self.base}/api/v1/video/send_tts"
        body = {"msg": msg, "tts_id": voice_id, "speechRate": speech_rate, "country": country, "region": region}
        r = self.session.post(url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- Anchors (characters) ----
    def list_custom_anchors(self, avatar_id: str) -> Dict[str, Any]:
        url = f"{self.base}/api/v1/anchor/character_list"
        params = {"user_video_twin_id": avatar_id, "type": "custom"}
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ---- Video ----
    def create_video(self, *, title: str, anchor_id: str, audio_src: str, resolution: int = 1080,
                     web_bg_width: int = 853, web_bg_height: int = 480,
                     web_people_width: int = 270, web_people_height: int = 480,
                     web_people_x: int = 292, web_people_y: int = 0,
                     is_skip_rs: bool = True, captions: bool = False) -> Dict[str, Any]:
        url = f"{self.base}/api/v1/video/generate"
        body = {
            "title": title,
            "anchor_id": anchor_id,
            "anchor_type": 1,
            "audioSrc": audio_src,
            "resolution": resolution,
            "web_bg_width": web_bg_width,
            "web_bg_height": web_bg_height,
            "web_people_width": web_people_width,
            "web_people_height": web_people_height,
            "web_people_x": web_people_x,
            "web_people_y": web_people_y,
            "isSkipRs": bool(is_skip_rs),
            "isCaptionEnabled": bool(captions),
        }
        r = self.session.post(url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


    def video_result(self, task_id: str) -> Dict[str, Any]:
        url = f"{self.base}/api/v1/video/awsResult"
        r = self.session.post(url, json={"_id": task_id}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()


    def wait_until_video_ready(self, task_id: str, timeout_s: int = 1800, poll_s: int = 20) -> Dict[str, Any]:
        """
        Polls the video_result endpoint until the task reaches a terminal state.

        Args:
            task_id (str): The video task ID.
            timeout_s (int): Maximum time to wait in seconds. Default 1800s (30 minutes).
            poll_s (int): Polling interval in seconds. Default 20s.

        Returns:
            Dict[str, Any]: The final result JSON from the API.

        Raises:
            RuntimeError: If the task ends in a failed/error state.
            TimeoutError: If the timeout is reached before a terminal state.
        """
        start = time.time()
        terminal_ok = {"ready", "success", "completed"}
        terminal_bad = {"failed", "error"}

        while True:
            item = self.video_result(task_id)
            # status = (item.get("data") or {}).get("current_status")
            status = item.get("data")[0]['status']
            print(f"[video:{task_id}] status = {status}")

            if status in terminal_ok:
                return item
            if status in terminal_bad:
                raise RuntimeError(f"Video task failed: {json.dumps(item, ensure_ascii=False)}")
            if time.time() - start > timeout_s:
                raise TimeoutError(f"Timed out waiting for video task {task_id} to be ready")

            time.sleep(poll_s)



# ----------------------------
# File helpers
# ----------------------------

def download_file(url: str, out_path: Path, *, chunk: int = 8192) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "python-requests"}) as r:
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                if part:
                    f.write(part)
    return out_path


# ----------------------------
# Orchestration
# ----------------------------

def run_pipeline(
    *,
    name: str,
    gender: str,
    prompt: str,
    script: str,
    voice_id: str,
    width: int = 1024,
    height: int = 768,
    auto_approve: bool = False,
    lip_sync: bool = False,
) -> None:
    load_dotenv()

    token = _env("A2E_API_TOKEN")
    base = os.getenv("A2E_BASE", "https://video.a2e.ai")

    client = A2EClient(token=token, base=base)

    # 1) TEXT → IMAGE
    print("\n[1/6] Generating image from prompt…")
    t2i = client.text2image(name=name, prompt=prompt, width=width, height=height)
    _print_json("text2image response", t2i)

    image_urls = ((t2i.get("data") or {}).get("image_urls")) or []
    if not image_urls:
        raise RuntimeError("No image_urls returned from text2image.")
    image_url = image_urls[0]
    print(f"Selected image URL: {image_url}")

    # Save locally
    ext = Path(image_url).suffix or ".jpg"
    out_path = Path(f"avatar{ext}")
    print(f"Downloading to: {out_path} …")
    download_file(image_url, out_path)
    print(f"Saved -> {out_path.resolve()}")

    # 2) APPROVAL
    if not auto_approve:
        ans = input("Proceed to train avatar with this image? [y/N] ").strip().lower()
        if ans not in {"y", "yes"}:
            print("Aborted by user before training.")
            return

    # 3) TRAIN AVATAR
    print("\n[2/6] Starting avatar training from image URL…")
    av_start = client.start_avatar_training_from_image(name=name, gender=gender, image_url=image_url)
    _print_json("startTraining response", av_start)

    if av_start.get("code") != 0:
        raise RuntimeError("Avatar startTraining failed.")

    avatar_id = (av_start.get("data") or {}).get("_id")
    if not avatar_id:
        raise RuntimeError("No avatar_id returned from startTraining.")
    print(f"avatar_id = {avatar_id}")

    print("\n[3/6] Waiting for avatar to be ready… (this can take a while)")
    ready_info = client.wait_until_avatar_ready(avatar_id)
    _print_json("avatar ready info", ready_info)

    # 4) OPTIONAL: LIP SYNC / CONTINUE TRAINING
    if lip_sync:
        print("\n[4/6] Triggering follow-up lip-sync training (continueTranining)…")
        cont = client.continue_training(avatar_id)
        _print_json("continueTranining response", cont)
        # Optionally wait again if the API indicates asynchronous work
        try:
            print("Re-checking avatar readiness after continueTranining…")
            ready_info = client.wait_until_avatar_ready(avatar_id)
            _print_json("avatar ready info (post-continue)", ready_info)
        except Exception as e:
            print(f"Warning: post-continue wait error: {e}")

    # 5) TTS
    print("\n[5/6] Generating TTS audio…")
    tts = client.generate_tts(script, voice_id)
    _print_json("send_tts response", tts)
    audio_src = (tts.get("data") if isinstance(tts, dict) else None) or _first_url_from(tts, exts=(".mp3", ".aac", ".wav", ".m4a"))
    if not audio_src:
        raise RuntimeError("Could not extract audioSrc from TTS response; inspect printed JSON and adjust parsing.")
    print(f"audioSrc = {audio_src}")

    # 6) ANCHOR + VIDEO
    print("\n[6/6] Fetching custom anchors for this avatar…")
    anchors = client.list_custom_anchors(avatar_id)
    _print_json("character_list (custom) response", anchors)

    items = (anchors.get("data") or []) if isinstance(anchors, dict) else []
    if not items:
        raise RuntimeError("No custom anchors found for this avatar. Ensure your account supports custom anchors.")
    anchor_id = items[0].get("_id")
    if not anchor_id:
        raise RuntimeError("First custom anchor has no _id field.")
    print(f"anchor_id = {anchor_id}")

    vid = client.create_video(title=f"{name} Greeting Video", anchor_id=anchor_id, audio_src=audio_src)
    _print_json("video generate response", vid)

    if vid.get("code") != 0:
        raise RuntimeError("Video generation failed; see response above.")

    task_id = (vid.get("data") or {}).get("_id")
    if not task_id:
        raise RuntimeError("No task _id returned from video generation.")
    print(f"video task_id = {task_id}")

    # Fetch result once (you can loop/poll if needed)
    result = client.video_result(task_id)
    _print_json("video awsResult response", result)

    video_url = _first_url_from(result, exts=(".mp4", ".m3u8", ".mov", ".webm"))
    if video_url:
        print(f"\n✅ Best-guess video URL: {video_url}")
    else:
        print("\nℹ️ Could not auto-detect a direct video URL. Inspect the printed JSON above for downloadable links.")


# ----------------------------
# CLI
# ----------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A2E: Text→Image → Avatar → TTS → Video pipeline")
    p.add_argument("--name", required=True, help="Avatar/persona display name")
    p.add_argument("--gender", choices=["male", "female"], required=True, help="Gender string expected by API")
    p.add_argument("--prompt", required=True, help="Text prompt to generate the initial image")
    p.add_argument("--script", required=True, help="Narration script for TTS")
    p.add_argument("--voice-id", required=True, help="Voice ID to use for TTS (see voice_list)")
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=768)
    p.add_argument("--auto-approve", action="store_true", help="Skip the approval prompt and continue automatically")
    p.add_argument("--lip-sync", action="store_true", help="Trigger extra training step mapped to continueTranining")
    return p.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    run_pipeline(
        name=args.name,
        gender=args.gender,
        prompt=args.prompt,
        script=args.script,
        voice_id=args.voice_id,
        width=args.width,
        height=args.height,
        auto_approve=args.auto_approve,
        lip_sync=args.lip_sync,
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
