"""
=============================================================================
             GT INNOX Video Compressor - Web Studio
=============================================================================
A modern, responsive, glassmorphic web frontend for the GT INNOX video
compressor. Powered by FastAPI, Uvicorn, and HTML5/CSS3/Vanilla JS with
real-time Server-Sent Events (SSE).
=============================================================================
"""

import os
import sys
import uuid
import json
import asyncio
import tempfile
import webbrowser
from typing import Dict, Any, Optional

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse as StarletteStreamingResponse

# Import core video compressor logic from video.py
from video import VideoCompressor, get_video_info, format_size, format_duration, get_ffmpeg_path

app = FastAPI(
    title="GT INNOX Video Compressor",
    description="Next-generation high-fidelity video compression studio by GT INNOX",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for active tasks
TASKS: Dict[str, Dict[str, Any]] = {}

# Temp directory for uploads and compressed files
TEMP_DIR = os.path.join(tempfile.gettempdir(), "gt_innox_video_compressor")
os.makedirs(TEMP_DIR, exist_ok=True)


# =====================================================================
# API Endpoints
# =====================================================================

@app.post("/api/upload")
async def upload_video(file: UploadFile = File(...)):
    """Uploads a video and returns its technical metadata."""
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    task_id = str(uuid.uuid4())
    upload_path = os.path.join(TEMP_DIR, f"{task_id}_original{ext}")

    with open(upload_path, "wb") as f:
        while chunk := await file.read(1024 * 1024 * 4):  # 4MB chunks
            f.write(chunk)

    info = get_video_info(upload_path)

    TASKS[task_id] = {
        "task_id": task_id,
        "input_path": upload_path,
        "output_path": None,
        "info": info,
        "status": "uploaded",
        "progress": {"percentage": 0.0, "speed": 1.0, "eta": 0.0, "status": "ready"},
        "compressor": VideoCompressor(),
        "error": None,
    }

    return {
        "task_id": task_id,
        "filename": file.filename,
        "duration": info["duration"],
        "duration_formatted": info["duration_formatted"],
        "width": info["width"],
        "height": info["height"],
        "resolution": info["resolution_str"],
        "fps": info["fps"],
        "size_bytes": info["file_size"],
        "size_formatted": info["file_size_formatted"],
        "bitrate_kbps": info["bitrate_kbps"],
    }


def run_compression_background(task_id: str, options: Dict[str, Any]):
    """Background worker executing the compression engine."""
    task = TASKS.get(task_id)
    if not task:
        return

    compressor: VideoCompressor = task["compressor"]
    input_path = task["input_path"]
    out_path = os.path.join(TEMP_DIR, f"{task_id}_compressed.mp4")
    task["output_path"] = out_path
    task["status"] = "processing"

    def on_progress(p_info):
        task["progress"] = p_info

    try:
        success = compressor.compress(
            input_path=input_path,
            output_path=out_path,
            mode=options.get("mode", "crf"),
            crf=int(options.get("crf", 28)),
            target_size_mb=float(options.get("target_mb", 25.0)),
            codec=options.get("codec", "h264"),
            scale=options.get("scale", "Original"),
            audio_mode=options.get("audio", "128k"),
            preset=options.get("preset", "medium"),
            hw_accel=bool(options.get("hw_accel", False)),
            progress_callback=on_progress,
        )

        if success and os.path.isfile(out_path):
            comp_size = os.path.getsize(out_path)
            orig_size = task["info"]["file_size"]
            saved_bytes = max(0, orig_size - comp_size)
            pct_saved = (saved_bytes / orig_size * 100) if orig_size > 0 else 0

            task["status"] = "completed"
            task["result"] = {
                "compressed_size": comp_size,
                "compressed_size_formatted": format_size(comp_size),
                "saved_bytes": saved_bytes,
                "saved_bytes_formatted": format_size(saved_bytes),
                "saved_percent": round(pct_saved, 1),
            }
        else:
            task["status"] = "cancelled" if compressor._is_cancelled else "failed"
            task["error"] = "Compression cancelled" if compressor._is_cancelled else "Compression failed."
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)


@app.post("/api/compress")
async def start_compression(
    background_tasks: BackgroundTasks,
    task_id: str = Form(...),
    mode: str = Form("crf"),
    crf: int = Form(28),
    target_mb: float = Form(25.0),
    scale: str = Form("Original"),
    codec: str = Form("h264"),
    audio: str = Form("128k"),
    preset: str = Form("medium"),
    hw_accel: bool = Form(False),
):
    """Starts the compression task in the background."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    task = TASKS[task_id]
    if task["status"] == "processing":
        return {"status": "already_running"}

    options = {
        "mode": mode,
        "crf": crf,
        "target_mb": target_mb,
        "scale": scale,
        "codec": codec,
        "audio": audio,
        "preset": preset,
        "hw_accel": hw_accel,
    }

    background_tasks.add_task(run_compression_background, task_id, options)
    return {"status": "started", "task_id": task_id}


@app.get("/api/progress/{task_id}")
async def get_progress_sse(task_id: str):
    """Server-Sent Events endpoint streaming live progress to the web browser."""
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Task not found")

    async def event_generator():
        while True:
            task = TASKS.get(task_id)
            if not task:
                break

            data = {
                "status": task["status"],
                "progress": task.get("progress", {}),
                "result": task.get("result", None),
                "error": task.get("error", None),
            }
            yield f"data: {json.dumps(data)}\n\n"

            if task["status"] in ["completed", "failed", "cancelled"]:
                break

            await asyncio.sleep(0.3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/cancel/{task_id}")
async def cancel_compression(task_id: str):
    """Cancels a running compression job."""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    compressor: VideoCompressor = task.get("compressor")
    if compressor:
        compressor.cancel()
    task["status"] = "cancelled"
    return {"status": "cancelled"}


@app.get("/api/download/{task_id}")
async def download_compressed(task_id: str):
    """Downloads the compressed video file."""
    task = TASKS.get(task_id)
    if not task or not task.get("output_path") or not os.path.isfile(task["output_path"]):
        raise HTTPException(status_code=404, detail="Compressed video not found")

    orig_name = task["info"]["file_name"]
    base, _ = os.path.splitext(orig_name)
    download_name = f"{base}_GT_INNOX_compressed.mp4"

    return FileResponse(
        task["output_path"],
        filename=download_name,
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'}
    )


@app.get("/api/stream/{task_id}/{video_type}")
async def stream_endpoint(task_id: str, video_type: str):
    """Streams either 'original' or 'compressed' video for HTML5 <video> preview."""
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    file_path = task["input_path"] if video_type == "original" else task.get("output_path")
    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not ready")

    return FileResponse(file_path, media_type="video/mp4")


# =====================================================================
# Embedded Modern Frontend with GT INNOX Branding
# =====================================================================

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GT INNOX Video Compressor Studio</title>
  
  <!-- Favicon: GT INNOX Monogram -->
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><polygon points='50,4 94,27 94,73 50,96 6,73 6,27' fill='%231E1B4B' stroke='%2300F5FF' stroke-width='4'/><path d='M52 26 C36 26 26 36 26 50 C26 64 36 74 52 74 C62 74 68 70 71 63 L71 52 L48 52 L48 44 L79 44 L79 66 C72 77 62 81 50 81 C30 81 18 68 18 50 C18 32 30 19 51 19 C62 19 70 23 76 29 Z' fill='%2300F5FF'/><path d='M40 33 L78 33 L78 40 L63 40 L63 67 L55 67 L55 40 L40 40 Z' fill='%23FFFFFF'/></svg>">
  
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  
  <style>
    :root {
      --bg-dark: #07090e;
      --card-bg: rgba(15, 23, 42, 0.72);
      --card-border: rgba(255, 255, 255, 0.08);
      --card-border-glow: rgba(0, 245, 255, 0.35);
      --gt-cyan: #00f5ff;
      --gt-indigo: #6366f1;
      --gt-pink: #ec4899;
      --gt-gradient: linear-gradient(135deg, #00f5ff 0%, #6366f1 50%, #ec4899 100%);
      --gt-gradient-subtle: linear-gradient(135deg, rgba(0, 245, 255, 0.15) 0%, rgba(99, 102, 241, 0.15) 50%, rgba(236, 72, 153, 0.15) 100%);
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --radius-lg: 20px;
      --radius-md: 12px;
      --radius-sm: 8px;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    body {
      background-color: var(--bg-dark);
      background-image: 
        radial-gradient(at 10% 10%, rgba(0, 245, 255, 0.12) 0px, transparent 50%),
        radial-gradient(at 90% 90%, rgba(236, 72, 153, 0.10) 0px, transparent 50%),
        radial-gradient(at 50% 50%, rgba(99, 102, 241, 0.08) 0px, transparent 65%);
      background-attachment: fixed;
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    /* Top Navigation Bar */
    .top-navbar {
      width: 100%;
      border-bottom: 1px solid var(--card-border);
      background: rgba(7, 9, 14, 0.85);
      backdrop-filter: blur(20px);
      position: sticky;
      top: 0;
      z-index: 100;
      padding: 12px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .nav-brand {
      display: flex;
      align-items: center;
      gap: 12px;
      text-decoration: none;
      color: inherit;
    }

    .nav-logo-svg {
      height: 38px;
      width: auto;
    }

    .nav-links {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .nav-pill-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 8px 14px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      transition: all 0.25s ease;
      cursor: pointer;
    }

    .btn-github {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--card-border);
      color: #fff;
    }
    .btn-github:hover {
      background: rgba(255, 255, 255, 0.12);
      border-color: rgba(255, 255, 255, 0.2);
      transform: translateY(-1px);
    }

    .btn-vercel {
      background: #000;
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #fff;
    }
    .btn-vercel:hover {
      border-color: var(--gt-cyan);
      box-shadow: 0 0 14px rgba(0, 245, 255, 0.3);
      transform: translateY(-1px);
    }

    .container {
      width: 100%;
      max-width: 980px;
      padding: 36px 20px 48px;
    }

    /* Hero Header */
    header {
      text-align: center;
      margin-bottom: 32px;
    }

    .gt-badge-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 16px;
      border-radius: 999px;
      background: var(--gt-gradient-subtle);
      border: 1px solid rgba(0, 245, 255, 0.35);
      color: var(--gt-cyan);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 1px;
      text-transform: uppercase;
      margin-bottom: 16px;
      box-shadow: 0 0 20px rgba(0, 245, 255, 0.15);
    }

    h1 {
      font-size: 44px;
      font-weight: 900;
      letter-spacing: -1.2px;
      background: var(--gt-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 10px;
    }

    .subtitle {
      color: var(--text-muted);
      font-size: 16px;
      max-width: 620px;
      margin: 0 auto;
      line-height: 1.5;
    }

    /* Glass Cards */
    .glass-card {
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 30px;
      box-shadow: 0 24px 48px rgba(0, 0, 0, 0.5);
      margin-bottom: 24px;
      transition: all 0.3s ease;
      position: relative;
      overflow: hidden;
    }

    .glass-card::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      background: var(--gt-gradient);
      opacity: 0.7;
    }

    /* Dropzone */
    .dropzone {
      border: 2px dashed rgba(255, 255, 255, 0.15);
      border-radius: var(--radius-lg);
      padding: 56px 24px;
      text-align: center;
      cursor: pointer;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      background: rgba(255, 255, 255, 0.015);
    }

    .dropzone:hover, .dropzone.dragover {
      border-color: var(--gt-cyan);
      background: rgba(0, 245, 255, 0.05);
      transform: translateY(-2px);
      box-shadow: 0 12px 30px rgba(0, 245, 255, 0.15);
    }

    .dropzone-icon {
      width: 68px;
      height: 68px;
      margin: 0 auto 16px;
      border-radius: 50%;
      background: rgba(0, 245, 255, 0.1);
      border: 1px solid rgba(0, 245, 255, 0.25);
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--gt-cyan);
    }

    .dropzone h3 {
      font-size: 20px;
      font-weight: 700;
      margin-bottom: 6px;
    }

    .dropzone p {
      color: var(--text-muted);
      font-size: 14px;
    }

    #fileInput {
      display: none;
    }

    /* Video Details Card */
    .video-info-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }

    .info-pill {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: var(--radius-md);
      padding: 12px;
      text-align: center;
    }

    .info-pill-label {
      font-size: 11px;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
      letter-spacing: 0.5px;
      margin-bottom: 4px;
    }

    .info-pill-val {
      font-size: 15px;
      font-weight: 700;
      color: var(--text-main);
      font-family: 'JetBrains Mono', monospace;
    }

    /* Presets Row */
    .presets-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 20px 0;
    }

    .preset-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-md);
      padding: 14px;
      cursor: pointer;
      transition: all 0.25s ease;
      text-align: left;
    }

    .preset-card:hover {
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(0, 245, 255, 0.4);
    }

    .preset-card.active {
      border-color: var(--gt-cyan);
      background: rgba(0, 245, 255, 0.08);
      box-shadow: 0 0 16px rgba(0, 245, 255, 0.2);
    }

    .preset-title {
      font-size: 14px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 4px;
    }

    .preset-desc {
      font-size: 12px;
      color: var(--text-muted);
    }

    /* Tabs */
    .tab-group {
      display: flex;
      background: rgba(0, 0, 0, 0.4);
      border-radius: var(--radius-md);
      padding: 4px;
      margin-bottom: 20px;
      border: 1px solid var(--card-border);
    }

    .tab-btn {
      flex: 1;
      padding: 10px;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 14px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.2s;
    }

    .tab-btn.active {
      background: rgba(0, 245, 255, 0.15);
      color: #fff;
      border: 1px solid rgba(0, 245, 255, 0.3);
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }

    /* Sliders and Inputs */
    .control-group {
      margin-bottom: 20px;
    }

    .control-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }

    .control-label {
      font-size: 14px;
      font-weight: 600;
    }

    .control-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      color: var(--gt-cyan);
      font-weight: 700;
      background: rgba(0, 245, 255, 0.1);
      border: 1px solid rgba(0, 245, 255, 0.2);
      padding: 3px 10px;
      border-radius: 6px;
    }

    input[type=range] {
      width: 100%;
      height: 6px;
      border-radius: 3px;
      background: rgba(255, 255, 255, 0.12);
      outline: none;
      -webkit-appearance: none;
      cursor: pointer;
    }

    input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      appearance: none;
      width: 18px;
      height: 18px;
      border-radius: 50%;
      background: var(--gt-cyan);
      box-shadow: 0 0 12px var(--gt-cyan);
      cursor: pointer;
      transition: transform 0.15s;
    }

    input[type=range]::-webkit-slider-thumb:hover {
      transform: scale(1.2);
    }

    .grid-3 {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }

    select {
      width: 100%;
      padding: 11px 14px;
      background: rgba(0, 0, 0, 0.35);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      cursor: pointer;
      transition: border-color 0.2s;
    }

    select:focus {
      border-color: var(--gt-cyan);
    }

    /* Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 14px 24px;
      font-size: 15px;
      font-weight: 700;
      border-radius: var(--radius-md);
      border: none;
      cursor: pointer;
      transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
      text-decoration: none;
    }

    .btn-primary {
      background: var(--gt-gradient);
      color: white;
      box-shadow: 0 8px 24px rgba(0, 245, 255, 0.25);
      width: 100%;
    }

    .btn-primary:hover {
      transform: translateY(-2px);
      box-shadow: 0 12px 30px rgba(0, 245, 255, 0.4);
    }

    .btn-cancel {
      background: rgba(239, 68, 68, 0.15);
      border: 1px solid rgba(239, 68, 68, 0.3);
      color: #fca5a5;
    }

    .btn-cancel:hover {
      background: rgba(239, 68, 68, 0.25);
    }

    .btn-secondary {
      background: rgba(255, 255, 255, 0.06);
      border: 1px solid var(--card-border);
      color: var(--text-main);
    }

    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.1);
    }

    /* Progress Section */
    .progress-track {
      width: 100%;
      height: 12px;
      background: rgba(0, 0, 0, 0.4);
      border-radius: 999px;
      overflow: hidden;
      margin-bottom: 12px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }

    .progress-fill {
      height: 100%;
      width: 0%;
      background: var(--gt-gradient);
      border-radius: 999px;
      transition: width 0.3s ease;
      box-shadow: 0 0 12px rgba(0, 245, 255, 0.5);
    }

    .progress-meta {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
      color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }

    /* Results */
    .results-hero {
      text-align: center;
      padding: 10px 0 20px;
    }

    .savings-badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 22px;
      border-radius: 999px;
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #34d399;
      font-size: 18px;
      font-weight: 800;
      margin-bottom: 20px;
      box-shadow: 0 0 20px rgba(16, 185, 129, 0.15);
    }

    .comparison-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-bottom: 24px;
    }

    .video-card {
      background: rgba(0, 0, 0, 0.3);
      border-radius: var(--radius-md);
      overflow: hidden;
      border: 1px solid var(--card-border);
    }

    .video-card-header {
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 700;
      background: rgba(255, 255, 255, 0.04);
      display: flex;
      justify-content: space-between;
    }

    video {
      width: 100%;
      display: block;
      background: #000;
      max-height: 280px;
    }

    .hidden {
      display: none !important;
    }

    .spinner {
      border: 3px solid rgba(255,255,255,0.1);
      width: 22px;
      height: 22px;
      border-radius: 50%;
      border-left-color: var(--gt-cyan);
      animation: spin 1s linear infinite;
      display: inline-block;
    }

    @keyframes spin {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }

    /* Footer */
    footer {
      width: 100%;
      border-top: 1px solid var(--card-border);
      padding: 30px 20px;
      text-align: center;
      color: var(--text-muted);
      font-size: 13px;
      margin-top: auto;
      background: rgba(7, 9, 14, 0.6);
    }

    .footer-brand {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      margin-bottom: 8px;
      font-weight: 700;
      color: #fff;
    }

    .footer-links {
      display: flex;
      justify-content: center;
      gap: 16px;
      margin-top: 12px;
    }

    .footer-links a {
      color: var(--gt-cyan);
      text-decoration: none;
      font-weight: 600;
    }

    .footer-links a:hover {
      text-decoration: underline;
    }
  </style>
</head>
<body>

  <!-- Top Navbar -->
  <nav class="top-navbar">
    <a href="/" class="nav-brand">
      <!-- GT INNOX Vector Logo -->
      <svg class="nav-logo-svg" viewBox="0 0 520 120" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="navGtGrad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#00F5FF" />
            <stop offset="50%" stop-color="#6366F1" />
            <stop offset="100%" stop-color="#EC4899" />
          </linearGradient>
          <linearGradient id="navInnoxGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stop-color="#FFFFFF" />
            <stop offset="100%" stop-color="#CBD5E1" />
          </linearGradient>
        </defs>
        <g transform="translate(15, 10)">
          <polygon points="50,2 94,26 94,74 50,98 6,74 6,26" fill="#1E1B4B" stroke="url(#navGtGrad)" stroke-width="4"/>
          <path d="M 52 26 C 36 26 26 36 26 50 C 26 64 36 74 52 74 C 62 74 68 70 71 63 L 71 52 L 48 52 L 48 44 L 79 44 L 79 66 C 72 77 62 81 50 81 C 30 81 18 68 18 50 C 18 32 30 19 51 19 C 62 19 70 23 76 29 Z" fill="url(#navGtGrad)"/>
          <path d="M 40 33 L 78 33 L 78 40 L 63 40 L 63 67 L 55 67 L 55 40 L 40 40 Z" fill="#FFFFFF"/>
        </g>
        <g transform="translate(135, 72)">
          <text x="0" y="0" font-family="'Plus Jakarta Sans', sans-serif" font-size="52" font-weight="900" fill="url(#navGtGrad)">GT</text>
          <text x="85" y="0" font-family="'Plus Jakarta Sans', sans-serif" font-size="52" font-weight="800" letter-spacing="4" fill="url(#navInnoxGrad)">INNOX</text>
          <text x="3" y="24" font-family="'Plus Jakarta Sans', sans-serif" font-size="12" font-weight="700" letter-spacing="6" fill="#00F5FF">STUDIO</text>
        </g>
      </svg>
    </a>

    <div class="nav-links">
      <!-- Vercel Deploy Badge -->
      <a href="https://vercel.com/new" target="_blank" class="nav-pill-btn btn-vercel">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L2 22h20L12 2z"/>
        </svg>
        Deploy on Vercel
      </a>
      
      <!-- GitHub Button -->
      <a href="https://github.com" target="_blank" class="nav-pill-btn btn-github">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
        </svg>
        GitHub
      </a>
    </div>
  </nav>

  <div class="container">
    <!-- Header -->
    <header>
      <div class="gt-badge-pill">
        <span>⚡ GT INNOX Next-Gen Media Lab</span>
        <span>•</span>
        <span>v1.0.0</span>
      </div>
      <h1>GT INNOX Video Compressor</h1>
      <p class="subtitle">Next-generation high-fidelity video compression engine by GT INNOX. Compress 4K, 1080p, and high-bitrate videos with zero visible loss for Discord, WhatsApp, and Web.</p>
    </header>

    <!-- STEP 1: UPLOAD DROPZONE -->
    <div id="uploadSection" class="glass-card">
      <div class="dropzone" id="dropzone">
        <div class="dropzone-icon">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
          </svg>
        </div>
        <h3>Drop your video here, or browse</h3>
        <p>Supports MP4, MOV, MKV, AVI, WebM (Any resolution up to 4K / 60fps)</p>
        <input type="file" id="fileInput" accept="video/*">
      </div>
    </div>

    <!-- STEP 2: CONFIGURATION & PREVIEW CARD -->
    <div id="configSection" class="glass-card hidden">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <h3 id="videoTitle" style="font-size: 18px; font-weight: 700;">video_sample.mp4</h3>
        <button class="btn btn-secondary" id="changeFileBtn" style="padding: 6px 14px; font-size: 12px;">Change Video</button>
      </div>

      <!-- Info Pills -->
      <div class="video-info-grid">
        <div class="info-pill">
          <div class="info-pill-label">Original Size</div>
          <div class="info-pill-val" id="infoSize">--</div>
        </div>
        <div class="info-pill">
          <div class="info-pill-label">Duration</div>
          <div class="info-pill-val" id="infoDuration">--</div>
        </div>
        <div class="info-pill">
          <div class="info-pill-label">Resolution</div>
          <div class="info-pill-val" id="infoRes">--</div>
        </div>
        <div class="info-pill">
          <div class="info-pill-label">FPS</div>
          <div class="info-pill-val" id="infoFps">--</div>
        </div>
      </div>

      <!-- Presets -->
      <div style="margin-top: 24px;">
        <label class="control-label">GT INNOX Optimization Presets</label>
        <div class="presets-row">
          <div class="preset-card active" data-preset="balanced">
            <div class="preset-title">⚖️ Balanced (Recommended)</div>
            <div class="preset-desc">CRF 28 • High Fidelity</div>
          </div>
          <div class="preset-card" data-preset="whatsapp">
            <div class="preset-title">📱 WhatsApp Limit</div>
            <div class="preset-desc">Max 16 MB • 720p</div>
          </div>
          <div class="preset-card" data-preset="discord">
            <div class="preset-title">💬 Discord Free</div>
            <div class="preset-desc">Max 25 MB • Fast</div>
          </div>
          <div class="preset-card" data-preset="hq">
            <div class="preset-title">💎 Cinema Master</div>
            <div class="preset-desc">CRF 22 • Near Lossless</div>
          </div>
          <div class="preset-card" data-preset="extreme">
            <div class="preset-title">🚀 Ultra Space Saver</div>
            <div class="preset-desc">CRF 34 • 480p</div>
          </div>
        </div>
      </div>

      <!-- Mode Selector -->
      <div class="tab-group">
        <button class="tab-btn active" id="tabCrf">Constant Quality (CRF Mode)</button>
        <button class="tab-btn" id="tabTarget">Target File Size (MB Ceiling)</button>
      </div>

      <!-- CRF Slider -->
      <div id="crfControlGroup" class="control-group">
        <div class="control-header">
          <span class="control-label">Visual Quality (CRF)</span>
          <span class="control-badge" id="crfValBadge">CRF 28 (Balanced)</span>
        </div>
        <input type="range" id="crfSlider" min="18" max="42" value="28">
      </div>

      <!-- Target Size Slider -->
      <div id="targetControlGroup" class="control-group hidden">
        <div class="control-header">
          <span class="control-label">Target Output Size</span>
          <span class="control-badge" id="targetValBadge">24.5 MB</span>
        </div>
        <input type="range" id="targetSlider" min="2" max="150" value="25" step="0.5">
      </div>

      <!-- Advanced Settings Grid -->
      <div class="grid-3">
        <div>
          <label class="control-label" style="display:block; margin-bottom: 6px;">Resolution</label>
          <select id="scaleSelect">
            <option value="Original">Keep Original</option>
            <option value="1080p">1080p (Full HD)</option>
            <option value="720p">720p (HD)</option>
            <option value="480p">480p (SD)</option>
            <option value="360p">360p (Mobile)</option>
            <option value="75%">75% Scale</option>
            <option value="50%">50% Scale</option>
          </select>
        </div>
        <div>
          <label class="control-label" style="display:block; margin-bottom: 6px;">Codec</label>
          <select id="codecSelect">
            <option value="h264">H.264 (Universal)</option>
            <option value="h265">H.265 / HEVC (High Efficiency)</option>
          </select>
        </div>
        <div>
          <label class="control-label" style="display:block; margin-bottom: 6px;">Audio Quality</label>
          <select id="audioSelect">
            <option value="128k">128 kbps (Balanced)</option>
            <option value="96k">96 kbps (Compact)</option>
            <option value="64k">64 kbps (Voice)</option>
            <option value="copy">Keep Original Audio</option>
            <option value="mute">Mute (Remove Audio)</option>
          </select>
        </div>
      </div>

      <!-- Compress Button -->
      <button class="btn btn-primary" id="startCompressBtn" style="margin-top: 12px;">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        Execute GT INNOX Compression
      </button>
    </div>

    <!-- STEP 3: PROGRESS BOX -->
    <div id="progressSection" class="glass-card hidden">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
        <div style="display: flex; align-items: center; gap: 10px;">
          <div class="spinner"></div>
          <span style="font-weight: 700; font-size: 16px;">GT INNOX Engine Compressing...</span>
        </div>
        <button class="btn btn-cancel" id="cancelBtn" style="padding: 6px 14px; font-size: 13px;">Cancel</button>
      </div>

      <div class="progress-track">
        <div class="progress-fill" id="progressFill"></div>
      </div>

      <div class="progress-meta">
        <span id="progressPct">0.0%</span>
        <span id="progressSpeed">Speed: --</span>
        <span id="progressEta">ETA: --</span>
      </div>
    </div>

    <!-- STEP 4: RESULTS & COMPARISON -->
    <div id="resultsSection" class="glass-card hidden">
      <div class="results-hero">
        <div class="savings-badge" id="savingsBadge">
          🎉 Reduced by 74.2% (38.4 MB Saved)
        </div>
        <p style="color: var(--text-muted); font-size: 14px;">Optimized and packaged by GT INNOX.</p>
      </div>

      <!-- Video Comparison Players -->
      <div class="comparison-grid">
        <div class="video-card">
          <div class="video-card-header">
            <span>Original Video</span>
            <span id="compOrigSize">--</span>
          </div>
          <video id="playerOriginal" controls></video>
        </div>
        <div class="video-card">
          <div class="video-card-header" style="color: var(--gt-cyan);">
            <span>GT INNOX Compressed</span>
            <span id="compNewSize">--</span>
          </div>
          <video id="playerCompressed" controls></video>
        </div>
      </div>

      <div style="display: flex; gap: 12px;">
        <a id="downloadBtn" class="btn btn-primary" style="flex: 2;" href="#" download>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          Download Compressed Video
        </a>
        <button class="btn btn-secondary" id="restartBtn" style="flex: 1;">
          Compress Another
        </button>
      </div>
    </div>
  </div>

  <!-- Footer -->
  <footer>
    <div class="footer-brand">
      <svg width="20" height="20" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <polygon points="50,4 94,27 94,73 50,96 6,73 6,27" fill="#1E1B4B" stroke="#00F5FF" stroke-width="5"/>
        <path d="M52 26 C36 26 26 36 26 50 C26 64 36 74 52 74 C62 74 68 70 71 63 L71 52 L48 52 L48 44 L79 44 L79 66 C72 77 62 81 50 81 C30 81 18 68 18 50 C18 32 30 19 51 19 C62 19 70 23 76 29 Z" fill="#00F5FF"/>
      </svg>
      <span>GT INNOX • Media & Innovation Lab</span>
    </div>
    <p>Open Source Software under MIT License. Production-ready for GitHub & Vercel.</p>
    <div class="footer-links">
      <a href="https://github.com" target="_blank">GitHub Repository</a>
      <span>•</span>
      <a href="https://vercel.com" target="_blank">Deploy on Vercel</a>
      <span>•</span>
      <a href="#">GT INNOX Community</a>
    </div>
  </footer>

  <script>
    let currentTaskId = null;
    let originalFileInfo = null;
    let eventSource = null;
    let currentMode = 'crf';

    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const uploadSection = document.getElementById('uploadSection');
    const configSection = document.getElementById('configSection');
    const progressSection = document.getElementById('progressSection');
    const resultsSection = document.getElementById('resultsSection');

    const tabCrf = document.getElementById('tabCrf');
    const tabTarget = document.getElementById('tabTarget');
    const crfControlGroup = document.getElementById('crfControlGroup');
    const targetControlGroup = document.getElementById('targetControlGroup');
    const crfSlider = document.getElementById('crfSlider');
    const crfValBadge = document.getElementById('crfValBadge');
    const targetSlider = document.getElementById('targetSlider');
    const targetValBadge = document.getElementById('targetValBadge');
    const scaleSelect = document.getElementById('scaleSelect');
    const codecSelect = document.getElementById('codecSelect');
    const audioSelect = document.getElementById('audioSelect');

    const startCompressBtn = document.getElementById('startCompressBtn');
    const cancelBtn = document.getElementById('cancelBtn');
    const progressFill = document.getElementById('progressFill');
    const progressPct = document.getElementById('progressPct');
    const progressSpeed = document.getElementById('progressSpeed');
    const progressEta = document.getElementById('progressEta');

    const savingsBadge = document.getElementById('savingsBadge');
    const compOrigSize = document.getElementById('compOrigSize');
    const compNewSize = document.getElementById('compNewSize');
    const playerOriginal = document.getElementById('playerOriginal');
    const playerCompressed = document.getElementById('playerCompressed');
    const downloadBtn = document.getElementById('downloadBtn');
    const restartBtn = document.getElementById('restartBtn');
    const changeFileBtn = document.getElementById('changeFileBtn');

    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleUpload(e.dataTransfer.files[0]);
      }
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) {
        handleUpload(fileInput.files[0]);
      }
    });

    async function handleUpload(file) {
      const formData = new FormData();
      formData.append('file', file);

      dropzone.innerHTML = `<div class="spinner" style="margin-bottom: 12px;"></div><h3>Uploading & Analyzing ${file.name}...</h3>`;

      try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error('Upload failed');
        const data = await resp.json();

        currentTaskId = data.task_id;
        originalFileInfo = data;

        document.getElementById('videoTitle').textContent = data.filename;
        document.getElementById('infoSize').textContent = data.size_formatted;
        document.getElementById('infoDuration').textContent = data.duration_formatted;
        document.getElementById('infoRes').textContent = data.resolution;
        document.getElementById('infoFps').textContent = data.fps + ' fps';

        const mbSize = data.size_bytes / (1024 * 1024);
        targetSlider.max = Math.max(50, Math.ceil(mbSize * 1.2));
        targetSlider.value = Math.min(25, Math.max(5, (mbSize * 0.4).toFixed(1)));
        targetValBadge.textContent = targetSlider.value + ' MB';

        uploadSection.classList.add('hidden');
        configSection.classList.remove('hidden');
      } catch (err) {
        alert('Upload failed: ' + err.message);
        location.reload();
      }
    }

    tabCrf.addEventListener('click', () => {
      currentMode = 'crf';
      tabCrf.classList.add('active');
      tabTarget.classList.remove('active');
      crfControlGroup.classList.remove('hidden');
      targetControlGroup.classList.add('hidden');
    });

    tabTarget.addEventListener('click', () => {
      currentMode = 'target_size';
      tabTarget.classList.add('active');
      tabCrf.classList.remove('active');
      targetControlGroup.classList.remove('hidden');
      crfControlGroup.classList.add('hidden');
    });

    crfSlider.addEventListener('input', (e) => {
      const val = parseInt(e.target.value);
      let desc = 'Balanced';
      if (val <= 20) desc = 'Near Lossless';
      else if (val <= 24) desc = 'High Quality';
      else if (val <= 30) desc = 'Balanced';
      else if (val <= 36) desc = 'Small Size';
      else desc = 'Extreme';
      crfValBadge.textContent = `CRF ${val} (${desc})`;
    });

    targetSlider.addEventListener('input', (e) => {
      targetValBadge.textContent = `${parseFloat(e.target.value).toFixed(1)} MB`;
    });

    document.querySelectorAll('.preset-card').forEach(card => {
      card.addEventListener('click', () => {
        document.querySelectorAll('.preset-card').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        const p = card.dataset.preset;

        if (p === 'balanced') {
          tabCrf.click();
          crfSlider.value = 28;
          crfSlider.dispatchEvent(new Event('input'));
          scaleSelect.value = 'Original';
          codecSelect.value = 'h264';
          audioSelect.value = '128k';
        } else if (p === 'whatsapp') {
          tabTarget.click();
          targetSlider.value = 15.5;
          targetSlider.dispatchEvent(new Event('input'));
          scaleSelect.value = '720p';
          codecSelect.value = 'h264';
          audioSelect.value = '96k';
        } else if (p === 'discord') {
          tabTarget.click();
          targetSlider.value = 24.5;
          targetSlider.dispatchEvent(new Event('input'));
          scaleSelect.value = 'Original';
          codecSelect.value = 'h264';
          audioSelect.value = '128k';
        } else if (p === 'hq') {
          tabCrf.click();
          crfSlider.value = 22;
          crfSlider.dispatchEvent(new Event('input'));
          scaleSelect.value = 'Original';
          codecSelect.value = 'h264';
          audioSelect.value = '128k';
        } else if (p === 'extreme') {
          tabCrf.click();
          crfSlider.value = 34;
          crfSlider.dispatchEvent(new Event('input'));
          scaleSelect.value = '480p';
          codecSelect.value = 'h265';
          audioSelect.value = '64k';
        }
      });
    });

    startCompressBtn.addEventListener('click', async () => {
      if (!currentTaskId) return;

      const formData = new FormData();
      formData.append('task_id', currentTaskId);
      formData.append('mode', currentMode);
      formData.append('crf', crfSlider.value);
      formData.append('target_mb', targetSlider.value);
      formData.append('scale', scaleSelect.value);
      formData.append('codec', codecSelect.value);
      formData.append('audio', audioSelect.value);

      configSection.classList.add('hidden');
      progressSection.classList.remove('hidden');

      await fetch('/api/compress', { method: 'POST', body: formData });
      listenToProgress();
    });

    function listenToProgress() {
      if (eventSource) eventSource.close();
      eventSource = new EventSource(`/api/progress/${currentTaskId}`);

      eventSource.onmessage = (e) => {
        const data = JSON.parse(e.data);

        if (data.progress && data.progress.percentage !== undefined) {
          const pct = Math.min(100, Math.max(0, data.progress.percentage)).toFixed(1);
          progressFill.style.width = pct + '%';
          progressPct.textContent = pct + '%';
          if (data.progress.speed) {
            progressSpeed.textContent = `Speed: ${data.progress.speed.toFixed(2)}x`;
          }
          if (data.progress.eta) {
            const m = Math.floor(data.progress.eta / 60);
            const s = Math.floor(data.progress.eta % 60);
            progressEta.textContent = `ETA: ${m}:${s < 10 ? '0' : ''}${s}`;
          }
        }

        if (data.status === 'completed') {
          eventSource.close();
          showResults(data.result);
        } else if (data.status === 'failed' || data.status === 'cancelled') {
          eventSource.close();
          alert(data.error || 'Compression halted.');
          location.reload();
        }
      };

      eventSource.onerror = () => {
        eventSource.close();
      };
    }

    cancelBtn.addEventListener('click', async () => {
      if (currentTaskId) {
        await fetch(`/api/cancel/${currentTaskId}`, { method: 'POST' });
        location.reload();
      }
    });

    function showResults(result) {
      progressSection.classList.add('hidden');
      resultsSection.classList.remove('hidden');

      savingsBadge.textContent = `🎉 Reduced by ${result.saved_percent}% (${result.saved_bytes_formatted} Saved)`;
      compOrigSize.textContent = originalFileInfo.size_formatted;
      compNewSize.textContent = result.compressed_size_formatted;

      playerOriginal.src = `/api/stream/${currentTaskId}/original`;
      playerCompressed.src = `/api/stream/${currentTaskId}/compressed`;

      downloadBtn.href = `/api/download/${currentTaskId}`;
    }

    restartBtn.addEventListener('click', () => location.reload());
    changeFileBtn.addEventListener('click', () => location.reload());
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serves the GT INNOX single-page frontend application."""
    return HTMLResponse(content=INDEX_HTML)


# =====================================================================
# Server Launcher
# =====================================================================

def start_server(port: int = 8000, host: str = "127.0.0.1", open_browser: bool = True):
    """Starts Uvicorn server and optionally opens default browser."""
    url = f"http://{host}:{port}"
    print("=" * 65)
    print("  GT INNOX Video Compressor Studio")
    print("=" * 65)
    print(f"  * Web UI URL  : {url}")
    print(f"  * FFmpeg path : {get_ffmpeg_path()}")
    print("-" * 65)

    if open_browser:
        def _open():
            import time
            time.sleep(1.0)
            webbrowser.open(url)
        import threading
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run GT INNOX Video Compressor Web Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to run web app (default: 8000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Do not automatically launch browser")
    args = parser.parse_args()

    start_server(port=args.port, host=args.host, open_browser=not args.no_browser)
