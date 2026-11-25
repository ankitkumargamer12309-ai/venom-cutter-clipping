"""VENOM CUTTER upload API.

This starter service creates short clips around scene changes using FFmpeg.
It is a lightweight baseline, not semantic AI ranking.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "600"))
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "2700"))
JOBS_DIR = Path(os.getenv("JOBS_DIR", "/tmp/venom-cutter-jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
ALLOWED_ORIGINS = {
    item.strip().rstrip("/")
    for item in os.getenv(
        "ALLOWED_ORIGINS",
        "https://ankitkumargamer12309-ai.github.io,http://127.0.0.1:5500,http://localhost:5500"
    ).split(",")
    if item.strip()
}
PROCESS_LOCK = threading.Lock()
RATE_LIMIT = {}
RATE_LIMIT_WINDOW = 15 * 60
RATE_LIMIT_MAX = 3


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin in ALLOWED_ORIGINS or "*" in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin if origin else "*"
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/<path:path>", methods=["OPTIONS"])
def preflight(path):
    return ("", 204)


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error=f"Video is too large. Maximum upload size is {MAX_UPLOAD_MB} MB."), 413


def clean_expired_jobs():
    now = time.time()
    for folder in JOBS_DIR.iterdir():
        try:
            if folder.is_dir() and now - folder.stat().st_mtime > JOB_TTL_SECONDS:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            continue


def check_rate_limit(ip):
    now = time.time()
    recent = [stamp for stamp in RATE_LIMIT.get(ip, []) if now - stamp < RATE_LIMIT_WINDOW]
    if len(recent) >= RATE_LIMIT_MAX:
        RATE_LIMIT[ip] = recent
        return False
    recent.append(now)
    RATE_LIMIT[ip] = recent
    return True


def run_command(command, timeout):
    return subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, check=False
    )


def read_video_duration(path: Path) -> float:
    result = run_command(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        timeout=30
    )
    if result.returncode != 0:
        raise ValueError("This file could not be read as a supported video.")
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError):
        raise ValueError("The uploaded video has no readable duration.")
    if duration < 8:
        raise ValueError("Please upload a video that is at least 8 seconds long.")
    if duration > MAX_VIDEO_SECONDS:
        raise ValueError(f"Video is too long. Maximum supported length is {MAX_VIDEO_SECONDS // 60} minutes.")
    return duration


def read_video_dimensions(path: Path) -> tuple[int, int]:
    result = run_command(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        timeout=30
    )
    if result.returncode != 0:
        raise ValueError("The video's resolution could not be read.")
    match = re.search(r"(\d+)x(\d+)", result.stdout.strip())
    if not match:
        raise ValueError("The uploaded video has no readable video dimensions.")
    width, height = int(match.group(1)), int(match.group(2))
    if width < 2 or height < 2:
        raise ValueError("The uploaded video's dimensions are invalid.")
    return width, height


def find_scene_times(path: Path) -> list[float]:
    result = run_command(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-vf", "select='gt(scene,0.30)',showinfo", "-an", "-f", "null", "-"],
        timeout=180
    )
    times = []
    for line in result.stderr.splitlines():
        match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if match:
            try:
                times.append(float(match.group(1)))
            except ValueError:
                pass
    return times


def find_motion_times(path: Path, metadata_path: Path) -> list[float]:
    """Estimate visual movement from low-resolution frame differences with FFmpeg."""
    metadata_path.unlink(missing_ok=True)
    result = run_command(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
         "-vf", "fps=2,scale=160:90:flags=fast_bilinear,format=gray,"
                "tblend=all_mode=difference,signalstats,"
                f"metadata=print:file={metadata_path}",
         "-an", "-f", "null", "-"],
        timeout=180
    )
    if result.returncode != 0 or not metadata_path.exists():
        return []
    text = metadata_path.read_text(encoding="utf-8", errors="replace")
    frame_time = None
    movement = None
    peaks = []
    for line in text.splitlines():
        if line.startswith("frame:"):
            if frame_time is not None and movement is not None and movement >= 6.0:
                peaks.append(frame_time)
            match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
            frame_time = float(match.group(1)) if match else None
            movement = None
        elif "lavfi.signalstats.YAVG=" in line:
            try:
                movement = float(line.rsplit("=", 1)[1])
            except ValueError:
                movement = None
    if frame_time is not None and movement is not None and movement >= 6.0:
        peaks.append(frame_time)
    return peaks


def score_clip_windows(duration: float, target_seconds: int, scenes: list[float], motion_times: list[float] | None = None) -> list[dict]:
    """Rank candidate moments by scene-change activity as a lightweight proxy.

    This deterministic heuristic is not a trained gaming/semantic AI model.
    """
    max_clips = 6
    clip_length = min(float(target_seconds), duration)
    if clip_length < 8:
        return []

    step = max(3.0, min(10.0, clip_length / 4))
    candidates = []
    latest_start = max(0.0, duration - clip_length)
    sorted_scenes = sorted(scenes)
    sorted_motion = sorted(motion_times or [])
    start = 0.0
    while start <= latest_start + 0.001:
        end = min(duration, start + clip_length)
        events = [moment for moment in sorted_scenes if start <= moment <= end]
        motion_events = [moment for moment in sorted_motion if start <= moment <= end]
        density = len(events) / max(1.0, clip_length)
        motion_density = len(motion_events) / max(1.0, clip_length)
        burst = sum(1 for left, right in zip(events, events[1:]) if right - left <= 4.0)
        motion_burst = sum(1 for left, right in zip(motion_events, motion_events[1:]) if right - left <= 2.0)
        raw_score = density * 35.0 + motion_density * 18.0 + burst * 2.0 + motion_burst * 1.5
        candidates.append({
            "start": round(start, 1),
            "end": round(end, 1),
            "score": round(min(100.0, raw_score), 1),
            "event_count": len(events),
            "motion_count": len(motion_events),
        })
        start += step

    if not candidates:
        candidates.append({"start": 0.0, "end": round(clip_length, 1), "score": 0.0, "event_count": 0})

    selected = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if all(abs(candidate["start"] - chosen["start"]) >= clip_length * 0.65 for chosen in selected):
            selected.append(candidate)
            if len(selected) >= max_clips:
                break
    return sorted(selected, key=lambda item: item["start"])


def choose_auto_duration(duration: float, scenes: list[float], motion_times: list[float]) -> int:
    """Choose a clip length from observed scene and motion activity."""
    if duration <= 15:
        return 15
    activity_per_minute = (len(scenes) + len(motion_times)) / max(duration, 1.0) * 60.0
    if activity_per_minute >= 8.0:
        return 15
    if activity_per_minute >= 4.0:
        return 30
    if activity_per_minute >= 1.5:
        return 45
    return 60


def choose_clip_windows(duration: float, target_seconds: int, scenes: list[float], motion_times: list[float] | None = None) -> list[tuple[float, float]]:
    ranked = score_clip_windows(duration, target_seconds, scenes, motion_times)
    return [(item["start"], item["end"]) for item in ranked]


def render_clip(source: Path, start: float, end: float, output: Path, ratio: str, quality: str):
    dimensions = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
    width, height = dimensions.get(ratio, dimensions["9:16"])
    if quality == "720p HD":
        width = int(width * 2 / 3)
        height = int(height * 2 / 3)
    elif quality == "Original quality":
        source_width, source_height = read_video_dimensions(source)
        if ratio == "9:16":
            height = source_height
            width = max(2, int(height * 9 / 16))
        elif ratio == "16:9":
            width = source_width
            height = max(2, int(width * 9 / 16))
        else:
            width = height = min(source_width, source_height)
        width -= width % 2
        height -= height % 2
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1"
    )
    result = run_command(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{start:.2f}", "-i", str(source), "-t", f"{end - start:.2f}",
         "-vf", video_filter, "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "22", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(output)],
        timeout=300
    )
    if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg could not export a clip. Try a smaller or different video.")


@app.get("/api/health")
def health():
    return jsonify(ok=True, service="VENOM CUTTER", mode="scene-change-clipping")


@app.post("/api/process")
def process_video():
    clean_expired_jobs()
    ip = request.remote_addr or "unknown"
    if not check_rate_limit(ip):
        return jsonify(error="Too many requests from this connection. Please wait 15 minutes and try again."), 429
    if not PROCESS_LOCK.acquire(blocking=False):
        return jsonify(error="VENOM is processing another video right now. Please try again in a few minutes."), 429

    job_dir = None
    try:
        video = request.files.get("video")
        if not video or not video.filename:
            return jsonify(error="Please choose a video file to upload."), 400
        safe_name = secure_filename(video.filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            return jsonify(error="Supported formats: MP4, MOV, MKV, and WEBM."), 415

        duration_choice = request.form.get("duration", "30")
        target_seconds = 30 if duration_choice == "auto" else int(duration_choice)
        if target_seconds not in {15, 30, 45, 60}:
            target_seconds = 30
        ratio = request.form.get("ratio", "9:16")
        if ratio not in {"9:16", "16:9", "1:1"}:
            ratio = "9:16"
        quality = request.form.get("quality", "1080p HD")
        if quality not in {"1080p HD", "720p HD", "Original quality"}:
            quality = "1080p HD"

        job_id = uuid.uuid4().hex[:12]
        job_dir = JOBS_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        source = job_dir / f"source{extension}"
        video.save(source)

        duration = read_video_duration(source)
        scenes = find_scene_times(source)
        motion_times = find_motion_times(source, job_dir / "motion_metadata.txt")
        if duration_choice == "auto":
            target_seconds = choose_auto_duration(duration, scenes, motion_times)
        windows = choose_clip_windows(duration, min(target_seconds, int(duration)), scenes, motion_times)
        if not windows:
            raise ValueError("No suitable clip sections could be found in this video.")

        clips = []
        for index, (start, end) in enumerate(windows, 1):
            filename = f"venom_clip_{index:02}.mp4"
            output = job_dir / filename
            render_clip(source, start, end, output, ratio, quality)
            clips.append({
                "filename": filename, "start": round(start, 1), "end": round(end, 1),
                "download": f"/api/download/{job_id}/{filename}"
            })

        source.unlink(missing_ok=True)
        return jsonify(
            message=f"{len(clips)} clips created at {target_seconds}-second target duration. Highlights are ranked using scene-change and frame-motion activity (a heuristic, not a trained gaming AI model). Automatic subtitles and semantic AI ranking are not enabled yet.",
            target_duration=target_seconds,
            clips=clips
        )
    except ValueError as error:
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error=str(error)), 400
    except subprocess.TimeoutExpired:
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error="Video processing took too long. Try a shorter video."), 408
    except Exception:
        app.logger.exception("VENOM video processing failed")
        if job_dir:
            shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify(error="Video processing failed. Check the video format and try again."), 500
    finally:
        PROCESS_LOCK.release()


@app.get("/api/download/<job_id>/<filename>")
def download_clip(job_id, filename):
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        return jsonify(error="Invalid download request."), 400
    safe_name = secure_filename(filename)
    if safe_name != filename or not safe_name.endswith(".mp4"):
        return jsonify(error="Invalid download filename."), 400
    clip_path = JOBS_DIR / job_id / safe_name
    if not clip_path.is_file():
        return jsonify(error="This clip has expired. Please generate it again."), 404
    return send_file(clip_path, as_attachment=True, download_name=safe_name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5001")), debug=False)
