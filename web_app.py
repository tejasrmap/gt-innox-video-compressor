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
import converter

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
# Document Conversion Tasks & State
CONVERT_TASKS: Dict[str, Dict[str, Any]] = {}


@app.post("/api/convert/word-to-pdf")
async def convert_docx_endpoint(file: UploadFile = File(...)):
    """Converts uploaded Word (.docx) document to print-ready PDF."""
    if not file.filename.lower().endswith(('.docx', '.doc')):
        raise HTTPException(status_code=400, detail="Please upload a valid Word document (.docx or .doc).")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    task_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0]
    out_pdf_name = f"{base_name}_GT_INNOX.pdf"
    out_pdf_path = os.path.join(TEMP_DIR, f"{task_id}_{out_pdf_name}")

    try:
        res = converter.convert_word_to_pdf(file_bytes, out_pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conversion error: {str(e)}")

    CONVERT_TASKS[task_id] = {
        "file_path": out_pdf_path,
        "filename": out_pdf_name,
        "media_type": "application/pdf"
    }

    return {
        "task_id": task_id,
        "original_filename": file.filename,
        "converted_filename": out_pdf_name,
        "page_count": res["page_count"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "converted_size": res["converted_size"],
        "converted_size_formatted": res["converted_size_formatted"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


@app.post("/api/convert/pdf-to-word")
async def convert_pdf_endpoint(file: UploadFile = File(...)):
    """Converts uploaded PDF document to editable Word (.docx)."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Please upload a valid PDF document (.pdf).")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    task_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0]
    out_docx_name = f"{base_name}_GT_INNOX.docx"
    out_docx_path = os.path.join(TEMP_DIR, f"{task_id}_{out_docx_name}")

    try:
        res = converter.convert_pdf_to_word(file_bytes, out_docx_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Conversion error: {str(e)}")

    CONVERT_TASKS[task_id] = {
        "file_path": out_docx_path,
        "filename": out_docx_name,
        "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    }

    return {
        "task_id": task_id,
        "original_filename": file.filename,
        "converted_filename": out_docx_name,
        "page_count": res["page_count"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "converted_size": res["converted_size"],
        "converted_size_formatted": res["converted_size_formatted"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


@app.get("/api/download-converted/{task_id}")
async def download_converted_file(task_id: str):
    """Downloads a converted Word or PDF document."""
    task = CONVERT_TASKS.get(task_id)
    if not task or not os.path.isfile(task["file_path"]):
        raise HTTPException(status_code=404, detail="Converted file not found")

    return FileResponse(
        task["file_path"],
        filename=task["filename"],
        media_type=task["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{task["filename"]}"'}
    )

@app.post("/api/pdf/compress")
async def compress_pdf_endpoint(file: UploadFile = File(...), quality: str = Form("balanced")):
    """Compresses uploaded PDF document (balanced, extreme, or lossless)."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a valid PDF document.")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    task_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0]
    out_pdf_name = f"{base_name}_GT_INNOX_compressed.pdf"
    out_pdf_path = os.path.join(TEMP_DIR, f"{task_id}_{out_pdf_name}")

    try:
        res = converter.compress_pdf(file_bytes, quality_level=quality, output_pdf_path=out_pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF Compression error: {str(e)}")

    CONVERT_TASKS[task_id] = {
        "file_path": out_pdf_path,
        "filename": out_pdf_name,
        "media_type": "application/pdf"
    }

    return {
        "task_id": task_id,
        "original_filename": file.filename,
        "compressed_filename": out_pdf_name,
        "page_count": res["page_count"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "compressed_size": res["compressed_size"],
        "compressed_size_formatted": res["compressed_size_formatted"],
        "saved_bytes": res["saved_bytes"],
        "saved_bytes_formatted": res["saved_bytes_formatted"],
        "saved_percent": res["saved_percent"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


@app.post("/api/pdf/merge")
async def merge_pdf_endpoint(files: list[UploadFile] = File(...)):
    """Merges multiple uploaded PDF documents into a single PDF."""
    if not files or len(files) < 2:
        raise HTTPException(status_code=400, detail="Please select at least 2 PDF files to merge.")

    pdf_bytes_list = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        b = await f.read()
        if len(b) > 0:
            pdf_bytes_list.append(b)

    if len(pdf_bytes_list) < 2:
        raise HTTPException(status_code=400, detail="At least 2 non-empty PDF files are required.")

    task_id = str(uuid.uuid4())
    out_pdf_name = "Merged_GT_INNOX.pdf"
    out_pdf_path = os.path.join(TEMP_DIR, f"{task_id}_{out_pdf_name}")

    try:
        res = converter.merge_pdfs(pdf_bytes_list, output_pdf_path=out_pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF Merge error: {str(e)}")

    CONVERT_TASKS[task_id] = {
        "file_path": out_pdf_path,
        "filename": out_pdf_name,
        "media_type": "application/pdf"
    }

    return {
        "task_id": task_id,
        "filename": out_pdf_name,
        "file_count": res["file_count"],
        "page_count": res["page_count"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "merged_size": res["merged_size"],
        "merged_size_formatted": res["merged_size_formatted"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


@app.post("/api/pdf/split")
async def split_pdf_endpoint(file: UploadFile = File(...), pages: str = Form("1")):
    """Extracts specified page range (e.g. '1-3, 5') from a PDF."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a valid PDF document.")

    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    task_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0]
    out_pdf_name = f"{base_name}_extracted_GT_INNOX.pdf"
    out_pdf_path = os.path.join(TEMP_DIR, f"{task_id}_{out_pdf_name}")

    try:
        res = converter.split_pdf(file_bytes, page_range_str=pages, output_pdf_path=out_pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF Split error: {str(e)}")

    CONVERT_TASKS[task_id] = {
        "file_path": out_pdf_path,
        "filename": out_pdf_name,
        "media_type": "application/pdf"
    }

    return {
        "task_id": task_id,
        "original_filename": file.filename,
        "extracted_filename": out_pdf_name,
        "extracted_page_count": res["extracted_page_count"],
        "total_pages": res["total_pages"],
        "extracted_pages_list": res["extracted_pages_list"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "split_size": res["split_size"],
        "split_size_formatted": res["split_size_formatted"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


@app.post("/api/pdf/to-jpg")
async def convert_pdf_to_jpg_endpoint(file: UploadFile = File(...), dpi: int = Form(150)):
    """Converts uploaded PDF document into JPG images (or ZIP for multi-page)."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a valid PDF document.")
    file_bytes = await file.read()
    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    task_id = str(uuid.uuid4())
    base_name = os.path.splitext(file.filename)[0]
    try:
        res = converter.pdf_to_jpg(file_bytes, dpi=dpi)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF to JPG conversion error: {str(e)}")
    ext = "zip" if res["is_zip"] else "jpg"
    out_filename = f"{base_name}_GT_INNOX_images.{ext}"
    out_file_path = os.path.join(TEMP_DIR, f"{task_id}_{out_filename}")
    with open(out_file_path, "wb") as f:
        f.write(res["output_bytes"])
    CONVERT_TASKS[task_id] = {
        "file_path": out_file_path,
        "filename": out_filename,
        "media_type": res["media_type"]
    }
    return {
        "task_id": task_id,
        "original_filename": file.filename,
        "converted_filename": out_filename,
        "is_zip": res["is_zip"],
        "media_type": res["media_type"],
        "page_count": res["page_count"],
        "pages_info": res["pages_info"],
        "original_size": res["original_size"],
        "original_size_formatted": res["original_size_formatted"],
        "converted_size": res["converted_size"],
        "converted_size_formatted": res["converted_size_formatted"],
        "time_taken_sec": res["time_taken_sec"],
        "download_url": f"/api/download-converted/{task_id}"
    }


# =====================================================================
# Embedded Modern Frontend with GT INNOX Branding
# =====================================================================

INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <meta name="theme-color" content="#07090e">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="GT INNOX">
  <meta name="format-detection" content="telephone=no">
  <meta name="mobile-web-app-capable" content="yes">
  <title>GT INNOX Studio • Video & PDF Master Suite</title>
  
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
      --gt-cyan: #00f5ff;
      --gt-indigo: #6366f1;
      --gt-pink: #ec4899;
      --gt-emerald: #10b981;
      --gt-purple: #a855f7;
      --gt-blue: #38bdf8;
      --gt-rose: #fb7185;
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
      -webkit-tap-highlight-color: transparent;
    }

    .mobile-only-text {
      display: none;
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
      min-height: -webkit-fill-available;
      display: flex;
      flex-direction: column;
      align-items: center;
      overflow-x: hidden;
    }

    html {
      height: -webkit-fill-available;
    }

    .top-navbar {
      width: 100%;
      border-bottom: 1px solid var(--card-border);
      background: rgba(7, 9, 14, 0.85);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      position: sticky;
      top: 0;
      z-index: 100;
      padding: 12px 24px;
      padding-top: max(12px, env(safe-area-inset-top));
      padding-left: max(16px, env(safe-area-inset-left));
      padding-right: max(16px, env(safe-area-inset-right));
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
      touch-action: manipulation;
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
      touch-action: manipulation;
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
      padding: 28px 20px 48px;
      padding-left: max(16px, env(safe-area-inset-left));
      padding-right: max(16px, env(safe-area-inset-right));
    }

    header {
      text-align: center;
      margin-bottom: 24px;
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
      margin-bottom: 12px;
      box-shadow: 0 0 20px rgba(0, 245, 255, 0.15);
    }

    h1 {
      font-size: 38px;
      font-weight: 900;
      letter-spacing: -1.2px;
      background: var(--gt-gradient);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      margin-bottom: 8px;
      line-height: 1.15;
    }

    .subtitle {
      color: var(--text-muted);
      font-size: 15px;
      max-width: 640px;
      margin: 0 auto;
      line-height: 1.5;
    }

    /* Studio Navigation Tabs */
    .studio-nav-bar {
      display: flex;
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-md);
      padding: 5px;
      margin-bottom: 24px;
      gap: 6px;
      overflow-x: auto;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
    }
    .studio-nav-bar::-webkit-scrollbar {
      display: none;
    }

    .studio-tab-btn {
      flex: 1;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 11px 14px;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-weight: 700;
      font-size: 13px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.25s ease;
      touch-action: manipulation;
      white-space: nowrap;
    }

    .studio-tab-btn:hover {
      color: #fff;
      background: rgba(255, 255, 255, 0.04);
    }

    .studio-tab-btn.active {
      background: var(--gt-gradient-subtle);
      color: #fff;
      border: 1px solid rgba(0, 245, 255, 0.35);
      box-shadow: 0 4px 14px rgba(0, 0, 0, 0.3);
    }

    .glass-card {
      background: var(--card-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-lg);
      padding: 28px;
      box-shadow: 0 24px 48px rgba(0, 0, 0, 0.5);
      margin-bottom: 24px;
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

    .dropzone {
      border: 2px dashed rgba(255, 255, 255, 0.18);
      border-radius: var(--radius-lg);
      padding: 48px 24px;
      text-align: center;
      cursor: pointer;
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
      background: rgba(255, 255, 255, 0.015);
      touch-action: manipulation;
    }

    .dropzone:hover, .dropzone.dragover {
      border-color: var(--gt-cyan);
      background: rgba(0, 245, 255, 0.05);
      transform: translateY(-2px);
      box-shadow: 0 12px 30px rgba(0, 245, 255, 0.15);
    }

    .dropzone:active {
      transform: scale(0.99);
    }

    /* Color Themes */
    .dropzone-word:hover, .dropzone-word.dragover {
      border-color: #38bdf8;
      background: rgba(56, 189, 248, 0.05);
      box-shadow: 0 12px 30px rgba(56, 189, 248, 0.15);
    }
    .icon-word {
      background: rgba(56, 189, 248, 0.12) !important;
      border: 1px solid rgba(56, 189, 248, 0.3) !important;
      color: #38bdf8 !important;
    }

    .dropzone-pdf:hover, .dropzone-pdf.dragover {
      border-color: #fb7185;
      background: rgba(251, 113, 133, 0.05);
      box-shadow: 0 12px 30px rgba(251, 113, 133, 0.15);
    }
    .icon-pdf {
      background: rgba(251, 113, 133, 0.12) !important;
      border: 1px solid rgba(251, 113, 133, 0.3) !important;
      color: #fb7185 !important;
    }

    .dropzone-emerald:hover, .dropzone-emerald.dragover {
      border-color: #10b981;
      background: rgba(16, 185, 129, 0.05);
      box-shadow: 0 12px 30px rgba(16, 185, 129, 0.15);
    }
    .icon-emerald {
      background: rgba(16, 185, 129, 0.12) !important;
      border: 1px solid rgba(16, 185, 129, 0.3) !important;
      color: #10b981 !important;
    }

    .dropzone-purple:hover, .dropzone-purple.dragover {
      border-color: #a855f7;
      background: rgba(168, 85, 247, 0.05);
      box-shadow: 0 12px 30px rgba(168, 85, 247, 0.15);
    }
    .icon-purple {
      background: rgba(168, 85, 247, 0.12) !important;
      border: 1px solid rgba(168, 85, 247, 0.3) !important;
      color: #a855f7 !important;
    }

    .dropzone-icon {
      width: 64px;
      height: 64px;
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
      font-size: 19px;
      font-weight: 700;
      margin-bottom: 6px;
    }

    .dropzone p {
      color: var(--text-muted);
      font-size: 14px;
    }

    .dropzone-mobile-chips {
      display: flex;
      justify-content: center;
      gap: 8px;
      margin-top: 16px;
      flex-wrap: wrap;
    }

    .mobile-chip {
      font-size: 11px;
      font-weight: 600;
      color: var(--gt-cyan);
      background: rgba(0, 245, 255, 0.08);
      border: 1px solid rgba(0, 245, 255, 0.2);
      border-radius: 999px;
      padding: 4px 10px;
    }
    .chip-word { color: #38bdf8; background: rgba(56, 189, 248, 0.08); border-color: rgba(56, 189, 248, 0.25); }
    .chip-pdf { color: #fb7185; background: rgba(251, 113, 133, 0.08); border-color: rgba(251, 113, 133, 0.25); }
    .chip-emerald { color: #10b981; background: rgba(16, 185, 129, 0.08); border-color: rgba(16, 185, 129, 0.25); }
    .chip-purple { color: #a855f7; background: rgba(168, 85, 247, 0.08); border-color: rgba(168, 85, 247, 0.25); }

    input[type="file"] {
      display: none;
    }

    .video-info-grid, .doc-info-grid {
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

    .presets-wrapper {
      position: relative;
      margin: 18px 0;
    }

    .presets-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
    }

    .preset-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-md);
      padding: 14px;
      cursor: pointer;
      transition: all 0.25s ease;
      text-align: left;
      touch-action: manipulation;
      user-select: none;
      -webkit-user-select: none;
    }

    .preset-card:hover {
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(0, 245, 255, 0.4);
    }

    .preset-card:active {
      transform: scale(0.97);
    }

    .preset-card.active {
      border-color: var(--gt-cyan);
      background: rgba(0, 245, 255, 0.08);
      box-shadow: 0 0 16px rgba(0, 245, 255, 0.2);
    }

    .preset-card-emerald.active {
      border-color: #10b981;
      background: rgba(16, 185, 129, 0.08);
      box-shadow: 0 0 16px rgba(16, 185, 129, 0.2);
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
      padding: 11px;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 14px;
      border-radius: var(--radius-sm);
      cursor: pointer;
      transition: all 0.2s;
      touch-action: manipulation;
    }

    .tab-btn.active {
      background: rgba(0, 245, 255, 0.15);
      color: #fff;
      border: 1px solid rgba(0, 245, 255, 0.3);
      box-shadow: 0 2px 8px rgba(0,0,0,0.25);
    }

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
      font-size: 13px;
      color: var(--gt-cyan);
      font-weight: 700;
      background: rgba(0, 245, 255, 0.1);
      border: 1px solid rgba(0, 245, 255, 0.2);
      padding: 3px 10px;
      border-radius: 6px;
    }

    .slider-container {
      position: relative;
      padding: 8px 0;
    }

    input[type=range] {
      width: 100%;
      height: 8px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.12);
      outline: none;
      -webkit-appearance: none;
      appearance: none;
      cursor: pointer;
      touch-action: pan-y;
      margin: 12px 0;
    }

    input[type=range]::-webkit-slider-thumb {
      -webkit-appearance: none;
      appearance: none;
      width: 24px;
      height: 24px;
      border-radius: 50%;
      background: var(--gt-cyan);
      box-shadow: 0 0 14px var(--gt-cyan);
      cursor: pointer;
      transition: transform 0.15s, box-shadow 0.15s;
    }

    input[type=range]::-webkit-slider-thumb:hover {
      transform: scale(1.2);
    }

    input[type=range]::-webkit-slider-thumb:active {
      transform: scale(1.3);
      box-shadow: 0 0 20px var(--gt-cyan), 0 0 0 6px rgba(0, 245, 255, 0.25);
    }

    .grid-4 {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 16px;
      margin-bottom: 16px;
    }

    select, input[type=text] {
      width: 100%;
      padding: 12px 36px 12px 14px;
      background-color: rgba(0, 0, 0, 0.45);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      transition: border-color 0.2s;
    }

    select {
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%2300f5ff' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 14px center;
      cursor: pointer;
      -webkit-appearance: none;
      appearance: none;
    }

    select:focus, input[type=text]:focus {
      border-color: var(--gt-cyan);
      box-shadow: 0 0 0 2px rgba(0, 245, 255, 0.2);
    }

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
      touch-action: manipulation;
      user-select: none;
      -webkit-user-select: none;
    }

    .btn:active {
      transform: scale(0.98);
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

    .btn-share {
      background: rgba(16, 185, 129, 0.15);
      border: 1px solid rgba(16, 185, 129, 0.35);
      color: #34d399;
    }

    .btn-share:hover {
      background: rgba(16, 185, 129, 0.25);
    }

    .sticky-compress-wrapper {
      margin-top: 14px;
      width: 100%;
    }

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
      flex-wrap: wrap;
      gap: 6px;
    }

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
      margin-bottom: 16px;
      box-shadow: 0 0 20px rgba(16, 185, 129, 0.15);
      word-break: break-word;
    }

    .badge-word { background: rgba(56, 189, 248, 0.15); border-color: rgba(56, 189, 248, 0.35); color: #38bdf8; box-shadow: 0 0 20px rgba(56, 189, 248, 0.15); }
    .badge-pdf { background: rgba(251, 113, 133, 0.15); border-color: rgba(251, 113, 133, 0.35); color: #fb7185; box-shadow: 0 0 20px rgba(251, 113, 133, 0.15); }
    .badge-emerald { background: rgba(16, 185, 129, 0.15); border-color: rgba(16, 185, 129, 0.35); color: #34d399; box-shadow: 0 0 20px rgba(16, 185, 129, 0.15); }
    .badge-purple { background: rgba(168, 85, 247, 0.15); border-color: rgba(168, 85, 247, 0.35); color: #c084fc; box-shadow: 0 0 20px rgba(168, 85, 247, 0.15); }
    .badge-amber { background: rgba(245, 158, 11, 0.15); border-color: rgba(245, 158, 11, 0.35); color: #fbbf24; box-shadow: 0 0 20px rgba(245, 158, 11, 0.15); }
    .dropzone-amber:hover, .dropzone-amber.dragover { border-color: #f59e0b; background: rgba(245, 158, 11, 0.05); box-shadow: 0 12px 30px rgba(245, 158, 11, 0.15); }
    .icon-amber { background: rgba(245, 158, 11, 0.12) !important; border: 1px solid rgba(245, 158, 11, 0.3) !important; color: #f59e0b !important; }
    .chip-amber { color: #f59e0b; background: rgba(245, 158, 11, 0.08); border-color: rgba(245, 158, 11, 0.25); }
    .preset-card-amber.active { border-color: #f59e0b; background: rgba(245, 158, 11, 0.08); box-shadow: 0 0 16px rgba(245, 158, 11, 0.2); }
    .jpg-preview-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 12px; margin-bottom: 20px; max-height: 380px; overflow-y: auto; padding: 4px; }
    .jpg-preview-card { background: rgba(0, 0, 0, 0.4); border: 1px solid var(--card-border); border-radius: var(--radius-sm); overflow: hidden; text-align: center; }
    .jpg-preview-card img { width: 100%; height: auto; display: block; border-bottom: 1px solid var(--card-border); cursor: pointer; transition: transform 0.2s; }
    .jpg-preview-card img:hover { transform: scale(1.02); }
    .jpg-preview-card-meta { padding: 8px 6px; font-size: 11px; font-family: 'JetBrains Mono', monospace; color: var(--text-muted); }

    .file-chip-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin: 14px 0;
      max-height: 200px;
      overflow-y: auto;
    }

    .file-chip-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 14px;
      background: rgba(255, 255, 255, 0.04);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      font-size: 13px;
    }

    .mobile-comparison-switch {
      display: none;
      background: rgba(0, 0, 0, 0.45);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-sm);
      padding: 4px;
      margin-bottom: 16px;
    }

    .mobile-comp-tab {
      flex: 1;
      padding: 8px 12px;
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 13px;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.2s;
    }

    .mobile-comp-tab.active {
      background: rgba(0, 245, 255, 0.16);
      color: #fff;
      border: 1px solid rgba(0, 245, 255, 0.3);
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
      align-items: center;
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

    footer {
      width: 100%;
      border-top: 1px solid var(--card-border);
      padding: 30px 20px;
      padding-bottom: max(30px, env(safe-area-inset-bottom));
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
      flex-wrap: wrap;
    }

    .footer-links a {
      color: var(--gt-cyan);
      text-decoration: none;
      font-weight: 600;
    }

    .footer-links a:hover {
      text-decoration: underline;
    }

    /* Mobile Responsive Optimizations */
    @media (max-width: 768px) {
      body { padding: 0; }
      .desktop-only-text { display: none !important; }
      .mobile-only-text { display: block !important; }
      .tab-desktop-text { display: none !important; }
      .top-navbar {
        padding: 10px 14px;
        padding-top: max(10px, env(safe-area-inset-top));
      }
      .nav-logo-svg { height: 28px; }
      .nav-links { gap: 6px; }
      .nav-pill-btn { padding: 6px 11px; font-size: 11px; }
      .container { padding: 14px 12px 32px; }
      header { margin-bottom: 14px; }
      .gt-badge-pill { font-size: 10px; padding: 4px 12px; margin-bottom: 8px; }
      h1 { font-size: 26px; letter-spacing: -0.6px; margin-bottom: 6px; }
      .subtitle { font-size: 13px; line-height: 1.45; padding: 0 4px; }

      .studio-nav-bar { padding: 4px; margin-bottom: 16px; }
      .studio-tab-btn { padding: 9px 11px; font-size: 12px; }

      .glass-card { padding: 16px 14px; border-radius: 16px; margin-bottom: 16px; }
      .dropzone { padding: 36px 14px; border-radius: 14px; }
      .dropzone-icon { width: 52px; height: 52px; margin-bottom: 12px; }
      .dropzone h3 { font-size: 17px; font-weight: 700; }
      .dropzone p { font-size: 12px; }
      .video-info-grid, .doc-info-grid { grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
      .info-pill { padding: 9px 8px; }
      .info-pill-label { font-size: 10px; }
      .info-pill-val { font-size: 13px; }

      .presets-wrapper { position: relative; margin: 14px 0 16px; }
      .presets-wrapper::after {
        content: '';
        position: absolute;
        top: 0; right: 0; bottom: 8px; width: 32px;
        pointer-events: none;
        background: linear-gradient(to right, transparent, rgba(15, 23, 42, 0.95));
        border-radius: 0 12px 12px 0;
      }
      .presets-row {
        display: flex;
        overflow-x: auto;
        scroll-snap-type: x mandatory;
        gap: 8px;
        padding: 2px 30px 8px 2px;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
      }
      .presets-row::-webkit-scrollbar { display: none; }
      .preset-card { flex: 0 0 148px; scroll-snap-align: start; padding: 11px 10px; border-radius: 12px; }
      .preset-title { font-size: 12px; }
      .preset-desc { font-size: 11px; }

      input[type=range] { height: 10px; margin: 12px 0; }
      input[type=range]::-webkit-slider-thumb { width: 28px; height: 28px; box-shadow: 0 0 16px var(--gt-cyan); }

      .grid-4 { grid-template-columns: 1fr 1fr; gap: 10px; }
      select, input[type=text] { padding: 11px 12px; font-size: 16px !important; min-height: 46px; border-radius: 10px; }
      .btn { padding: 14px 18px; font-size: 15px; min-height: 48px; border-radius: 12px; }

      .sticky-compress-wrapper {
        position: sticky;
        bottom: max(10px, env(safe-area-inset-bottom));
        z-index: 30;
        padding: 6px;
        background: rgba(7, 9, 14, 0.88);
        backdrop-filter: blur(16px);
        -webkit-backdrop-filter: blur(16px);
        border: 1px solid var(--card-border);
        border-radius: 14px;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.7);
      }

      .mobile-comparison-switch { display: flex; }
      .comparison-grid { grid-template-columns: 1fr; gap: 12px; }
      video { max-height: 230px; border-radius: 8px; }
      .results-hero .savings-badge { font-size: 14px; padding: 8px 16px; line-height: 1.3; }
      .results-action-row { flex-direction: column !important; gap: 10px !important; }
      .results-action-row .btn { width: 100% !important; }
      footer { padding: 24px 14px; }
    }

    @media (max-width: 440px) {
      .nav-pill-btn span { display: none; }
      .grid-4 { grid-template-columns: 1fr; }
      h1 { font-size: 23px; }
      .preset-card { flex: 0 0 138px; }
    }
  </style>
</head>
<body>

  <!-- Top Navbar -->
  <nav class="top-navbar">
    <a href="/" class="nav-brand" aria-label="GT INNOX Studio Home">
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
      <a href="https://vercel.com/new" target="_blank" rel="noopener" class="nav-pill-btn btn-vercel" title="Deploy on Vercel">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L2 22h20L12 2z"/>
        </svg>
        <span>Deploy</span>
      </a>
      
      <a href="https://github.com/tejasrmap/gt-innox-video-compressor" target="_blank" rel="noopener" class="nav-pill-btn btn-github" title="View Source on GitHub">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
        </svg>
        <span>GitHub</span>
      </a>
    </div>
  </nav>

  <div class="container">
    <header>
      <div class="gt-badge-pill">
        <span>⚡ GT INNOX Studio & Lab</span>
        <span>•</span>
        <span>v2.1.0</span>
      </div>
      <h1>GT INNOX Studio</h1>
      <p class="subtitle desktop-only-text">All-in-one suite: Video Compressor, PDF Compressor, PDF Merge & Split, and autonomous Word ⇄ PDF converters. Zero cloud upload required.</p>
      <p class="subtitle mobile-only-text">Video Compressor, PDF Compressor, Merge & Split, and Word ⇄ PDF.</p>
    </header>

    <!-- Studio Tool Navigation Switcher (5 Tools) -->
    <div class="studio-nav-bar" id="studioNavBar" role="tablist">
      <button class="studio-tab-btn active" data-tool="video" role="tab" aria-selected="true">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <polygon points="23 7 16 12 23 17 23 7"></polygon>
          <rect x="1" y="5" width="15" height="14" rx="2" ry="2"></rect>
        </svg>
        <span>Video Compress</span>
      </button>

      <button class="studio-tab-btn" data-tool="pdf-compress" role="tab" aria-selected="false">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="12" y1="18" x2="12" y2="12"></line>
          <polyline points="9 15 12 12 15 15"></polyline>
        </svg>
        <span>PDF Compress</span>
      </button>

      <button class="studio-tab-btn" data-tool="pdf-merge-split" role="tab" aria-selected="false">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <circle cx="18" cy="18" r="3"></circle>
          <circle cx="6" cy="6" r="3"></circle>
          <path d="M13 6h3a2 2 0 0 1 2 2v7"></path>
          <line x1="6" y1="9" x2="6" y2="21"></line>
        </svg>
        <span>Merge & Split</span>
      </button>

      <button class="studio-tab-btn" data-tool="pdf-to-jpg" role="tab" aria-selected="false">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
          <circle cx="8.5" cy="8.5" r="1.5"></circle>
          <polyline points="21 15 16 10 5 21"></polyline>
        </svg>
        <span>PDF to JPG</span>
      </button>

      <button class="studio-tab-btn" data-tool="word-to-pdf" role="tab" aria-selected="false">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="16" y1="13" x2="8" y2="13"></line>
          <line x1="16" y1="17" x2="8" y2="17"></line>
        </svg>
        <span>Word to PDF</span>
      </button>

      <button class="studio-tab-btn" data-tool="pdf-to-word" role="tab" aria-selected="false">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <path d="M12 18v-6"></path>
          <path d="m9 15 3 3 3-3"></path>
        </svg>
        <span>PDF to Word</span>
      </button>
    </div>

    <!-- ============================================================== -->
    <!-- VIEW 1: VIDEO COMPRESSOR TOOL                                  -->
    <!-- ============================================================== -->
    <div id="videoToolView">
      <div id="uploadSection" class="glass-card">
        <div class="dropzone" id="dropzone">
          <div class="dropzone-icon">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="17 8 12 3 7 8"></polyline>
              <line x1="12" y1="3" x2="12" y2="15"></line>
            </svg>
          </div>
          <h3 class="desktop-only-text">Drop your video here, or browse</h3>
          <h3 class="mobile-only-text">📱 Tap to select video</h3>
          <p class="desktop-only-text">Supports MP4, MOV, MKV, AVI, WebM (Any resolution up to 4K / 60fps)</p>
          <p class="mobile-only-text">Select from Photo Library or Files (MP4, MOV, WebM)</p>
          
          <div class="dropzone-mobile-chips">
            <span class="mobile-chip">⚡ Fast On-Device Engine</span>
            <span class="mobile-chip">🔒 100% Private</span>
            <span class="mobile-chip">🚀 4K & 1080p Ready</span>
          </div>

          <input type="file" id="fileInput" accept="video/*,video/mp4,video/quicktime,video/webm,video/x-m4v">
        </div>
      </div>

      <div id="configSection" class="glass-card hidden">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 8px;">
          <h3 id="videoTitle" style="font-size: 17px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">video_sample.mp4</h3>
          <button class="btn btn-secondary" id="changeFileBtn" style="padding: 6px 12px; font-size: 12px; white-space: nowrap; flex-shrink: 0;">Change Video</button>
        </div>

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

        <div class="presets-wrapper">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <label class="control-label">GT INNOX Optimization Presets</label>
            <span class="mobile-only-text" style="font-size: 11px; color: var(--gt-cyan); font-weight: 600;">Swipe for more 👉</span>
          </div>
          <div class="presets-row" id="presetsRow">
            <div class="preset-card active" data-preset="balanced">
              <div class="preset-title">⚖️ Balanced</div>
              <div class="preset-desc">CRF 28 • High Fidelity</div>
            </div>
            <div class="preset-card" data-preset="whatsapp">
              <div class="preset-title">📱 WhatsApp</div>
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
              <div class="preset-title">🚀 Space Saver</div>
              <div class="preset-desc">CRF 34 • 480p</div>
            </div>
          </div>
        </div>

        <div class="tab-group">
          <button class="tab-btn active" id="tabCrf"><span class="tab-desktop-text">Constant </span>Quality (CRF)</button>
          <button class="tab-btn" id="tabTarget">Target Size (MB)</button>
        </div>

        <div id="crfControlGroup" class="control-group">
          <div class="control-header">
            <span class="control-label">Visual Quality (CRF)</span>
            <span class="control-badge" id="crfValBadge">CRF 28 (Balanced)</span>
          </div>
          <div class="slider-container">
            <input type="range" id="crfSlider" min="18" max="42" value="28" aria-label="Visual Quality CRF Slider">
          </div>
        </div>

        <div id="targetControlGroup" class="control-group hidden">
          <div class="control-header">
            <span class="control-label">Target Output Size</span>
            <span class="control-badge" id="targetValBadge">24.5 MB</span>
          </div>
          <div class="slider-container">
            <input type="range" id="targetSlider" min="2" max="150" value="25" step="0.5" aria-label="Target Output Size in MB">
          </div>
        </div>

        <div class="grid-4">
          <div>
            <label class="control-label" style="display:block; margin-bottom: 6px;">Resolution</label>
            <select id="scaleSelect" aria-label="Output Resolution">
              <option value="Original">Keep Original</option>
              <option value="1080p">1080p (Full HD)</option>
              <option value="720p">720p (HD - Fast)</option>
              <option value="480p">480p (SD - Fastest)</option>
              <option value="360p">360p (Mobile)</option>
              <option value="75%">75% Scale</option>
              <option value="50%">50% Scale</option>
            </select>
          </div>
          <div>
            <label class="control-label" style="display:block; margin-bottom: 6px;">Codec</label>
            <select id="codecSelect" aria-label="Video Codec">
              <option value="h264">H.264 (Universal)</option>
              <option value="h265">H.265 / HEVC</option>
            </select>
          </div>
          <div>
            <label class="control-label" style="display:block; margin-bottom: 6px;">Audio Quality</label>
            <select id="audioSelect" aria-label="Audio Quality">
              <option value="128k">128 kbps (Balanced)</option>
              <option value="96k">96 kbps (Compact)</option>
              <option value="64k">64 kbps (Voice)</option>
              <option value="copy">Keep Original Audio</option>
              <option value="mute">Mute (Remove Audio)</option>
            </select>
          </div>
          <div>
            <label class="control-label" style="display:block; margin-bottom: 6px;">Profile</label>
            <select id="profileSelect" aria-label="Encoding Profile">
              <option value="balanced">⚡ Balanced (Audio & Video Sync)</option>
              <option value="max">🚀 Max Compression</option>
              <option value="hq">💎 High Fidelity Audio/Video</option>
            </select>
          </div>
        </div>

        <div class="sticky-compress-wrapper">
          <button class="btn btn-primary" id="startCompressBtn">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
            </svg>
            Execute GT INNOX Compression
          </button>
        </div>
      </div>

      <div id="progressSection" class="glass-card hidden">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <div class="spinner"></div>
            <span style="font-weight: 700; font-size: 15px;">GT INNOX Engine Compressing...</span>
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

      <div id="resultsSection" class="glass-card hidden">
        <div class="results-hero">
          <div class="savings-badge" id="savingsBadge">
            🎉 Reduced by 74.2% (38.4 MB Saved)
          </div>
          <p style="color: var(--text-muted); font-size: 14px;">Optimized and packaged by GT INNOX.</p>
        </div>

        <div class="mobile-comparison-switch" id="mobileCompSwitch">
          <button class="mobile-comp-tab active" data-view="compressed">⚡ Compressed</button>
          <button class="mobile-comp-tab" data-view="original">Original</button>
          <button class="mobile-comp-tab" data-view="both">Side-by-Side</button>
        </div>

        <div class="comparison-grid">
          <div class="video-card" id="cardCompressed">
            <div class="video-card-header" style="color: var(--gt-cyan);">
              <span>⚡ GT INNOX Compressed</span>
              <span id="compNewSize">--</span>
            </div>
            <video id="playerCompressed" controls playsinline webkit-playsinline></video>
          </div>
          <div class="video-card" id="cardOriginal">
            <div class="video-card-header">
              <span>Original Video</span>
              <span id="compOrigSize">--</span>
            </div>
            <video id="playerOriginal" controls playsinline webkit-playsinline></video>
          </div>
        </div>

        <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
          <button id="shareBtn" class="btn btn-share hidden" style="flex: 1;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
            Save to Photos / Share
          </button>

          <a id="downloadBtn" class="btn btn-primary" style="flex: 2;" href="#" download>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Download Video
          </a>

          <button class="btn btn-secondary" id="restartBtn" style="flex: 1;">
            Compress Another
          </button>
        </div>
      </div>
    </div>

    <!-- ============================================================== -->
    <!-- VIEW 2: PDF COMPRESSOR TOOL                                    -->
    <!-- ============================================================== -->
    <div id="pdfCompressToolView" class="hidden">
      <div id="pdfCompUploadSection" class="glass-card">
        <div class="dropzone dropzone-emerald" id="pdfCompDropzone">
          <div class="dropzone-icon icon-emerald">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="12" y1="18" x2="12" y2="12"></line>
              <polyline points="9 15 12 12 15 15"></polyline>
            </svg>
          </div>
          <h3 class="desktop-only-text">Drop your PDF document here to compress</h3>
          <h3 class="mobile-only-text">📱 Tap to select PDF file</h3>
          <p class="desktop-only-text">Reduce large PDF file sizes by up to 90% while keeping text sharp and images clear</p>
          <p class="mobile-only-text">Shrink PDFs for email, WhatsApp & portal upload limits</p>
          
          <div class="dropzone-mobile-chips">
            <span class="mobile-chip chip-emerald">⚡ Up to 90% Space Savings</span>
            <span class="mobile-chip chip-emerald">📑 Smart Image Downsampling</span>
            <span class="mobile-chip chip-emerald">🔒 100% Private</span>
          </div>

          <input type="file" id="pdfCompFileInput" accept=".pdf,application/pdf">
        </div>
      </div>

      <!-- Config & Preset state -->
      <div id="pdfCompConfigSection" class="glass-card hidden">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 8px;">
          <h3 id="pdfCompTitle" style="font-size: 17px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">document.pdf</h3>
          <button class="btn btn-secondary" id="changePdfCompBtn" style="padding: 6px 12px; font-size: 12px; white-space: nowrap; flex-shrink: 0;">Change PDF</button>
        </div>

        <div class="doc-info-grid">
          <div class="info-pill">
            <div class="info-pill-label">Original PDF Size</div>
            <div class="info-pill-val" id="pdfCompOrigSizeVal">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Page Count</div>
            <div class="info-pill-val" id="pdfCompPageVal">--</div>
          </div>
        </div>

        <div style="margin: 20px 0 16px;">
          <label class="control-label" style="display: block; margin-bottom: 8px;">Compression Level</label>
          <div class="presets-row">
            <div class="preset-card preset-card-emerald active" data-pdf-preset="balanced">
              <div class="preset-title">⚖️ Balanced (Recommended)</div>
              <div class="preset-desc">150 DPI • Crisp Text & High Savings</div>
            </div>
            <div class="preset-card preset-card-emerald" data-pdf-preset="extreme">
              <div class="preset-title">🚀 Extreme Space Saver</div>
              <div class="preset-desc">72–96 DPI • Max reduction under 2 MB</div>
            </div>
            <div class="preset-card preset-card-emerald" data-pdf-preset="lossless">
              <div class="preset-title">💎 Stream Clean (Lossless)</div>
              <div class="preset-desc">Cleans metadata & deflates streams</div>
            </div>
          </div>
        </div>

        <button class="btn btn-primary" id="startPdfCompressBtn" style="background: linear-gradient(135deg, #059669 0%, #10b981 100%);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
          </svg>
          Execute GT INNOX PDF Compression
        </button>
      </div>

      <!-- Processing state -->
      <div id="pdfCompProgressSection" class="glass-card hidden">
        <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
          <div class="spinner" style="border-left-color: #10b981; width: 28px; height: 28px;"></div>
          <span style="font-weight: 700; font-size: 16px; color: #10b981;">Compressing PDF & Optimizing Streams...</span>
        </div>
      </div>

      <!-- Result state -->
      <div id="pdfCompResultSection" class="glass-card hidden">
        <div class="results-hero">
          <div class="savings-badge badge-emerald" id="pdfCompSuccessBadge">
            🎉 PDF Reduced by 78.4% (12.2 MB Saved)
          </div>
          <p style="color: var(--text-muted); font-size: 14px;" id="pdfCompResultSummary">Compressed and ready for download.</p>
        </div>

        <div class="doc-info-grid" style="margin-bottom: 24px;">
          <div class="info-pill">
            <div class="info-pill-label">Original PDF Size</div>
            <div class="info-pill-val" id="pdfCompResOrigSize">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Compressed PDF Size</div>
            <div class="info-pill-val" id="pdfCompResNewSize" style="color: #10b981;">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Space Saved</div>
            <div class="info-pill-val" id="pdfCompResSaved">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Time Taken</div>
            <div class="info-pill-val" id="pdfCompResTime">--</div>
          </div>
        </div>

        <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
          <button id="pdfCompShareBtn" class="btn btn-share hidden" style="flex: 1;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
            Share / Save PDF
          </button>

          <a id="pdfCompDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #059669 0%, #10b981 100%);" href="#" download>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Download Compressed PDF
          </a>

          <button class="btn btn-secondary" id="pdfCompRestartBtn" style="flex: 1;">
            Compress Another PDF
          </button>
        </div>
      </div>
    </div>

    <!-- ============================================================== -->
    <!-- VIEW 3: PDF MERGE & SPLIT TOOL                                 -->
    <!-- ============================================================== -->
    <div id="pdfMergeSplitToolView" class="hidden">
      <!-- Mode toggle -->
      <div class="tab-group" style="margin-bottom: 20px;">
        <button class="tab-btn active" id="tabMergeMode">🔀 Merge Multiple PDFs</button>
        <button class="tab-btn" id="tabSplitMode">✂️ Split / Extract Pages</button>
      </div>

      <!-- SUB-VIEW A: MERGE -->
      <div id="mergeSubSection">
        <div id="mergeUploadBox" class="glass-card">
          <div class="dropzone dropzone-purple" id="mergeDropzone">
            <div class="dropzone-icon icon-purple">
              <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="18" cy="18" r="3"></circle>
                <circle cx="6" cy="6" r="3"></circle>
                <path d="M13 6h3a2 2 0 0 1 2 2v7"></path>
                <line x1="6" y1="9" x2="6" y2="21"></line>
              </svg>
            </div>
            <h3 class="desktop-only-text">Drop multiple PDF files here, or browse</h3>
            <h3 class="mobile-only-text">📱 Tap to select 2 or more PDFs</h3>
            <p class="desktop-only-text">Combine multiple PDFs into a single unified document in your exact order</p>
            <p class="mobile-only-text">Select multiple PDF files to merge into one</p>
            
            <div class="dropzone-mobile-chips">
              <span class="mobile-chip chip-purple">🔀 Multi-File Merge</span>
              <span class="mobile-chip chip-purple">📑 Page Ordering</span>
              <span class="mobile-chip chip-purple">🔒 100% Private</span>
            </div>

            <input type="file" id="mergeFileInput" multiple accept=".pdf,application/pdf">
          </div>

          <div id="mergeSelectedListContainer" class="hidden" style="margin-top: 18px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
              <span class="control-label" id="mergeFileCountLabel">2 PDFs selected</span>
              <button class="btn btn-secondary" id="clearMergeFilesBtn" style="padding: 4px 10px; font-size: 11px;">Clear All</button>
            </div>
            <div class="file-chip-list" id="mergeFileList"></div>

            <button class="btn btn-primary" id="startMergeBtn" style="margin-top: 14px; background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="18" cy="18" r="3"></circle>
                <circle cx="6" cy="6" r="3"></circle>
                <path d="M13 6h3a2 2 0 0 1 2 2v7"></path>
              </svg>
              Merge PDFs into One Document
            </button>
          </div>
        </div>

        <div id="mergeProgressSection" class="glass-card hidden">
          <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
            <div class="spinner" style="border-left-color: #a855f7; width: 28px; height: 28px;"></div>
            <span style="font-weight: 700; font-size: 16px; color: #a855f7;">Merging PDF Documents...</span>
          </div>
        </div>

        <div id="mergeResultSection" class="glass-card hidden">
          <div class="results-hero">
            <div class="savings-badge badge-purple" id="mergeSuccessBadge">
              🎉 PDFs Merged Successfully!
            </div>
            <p style="color: var(--text-muted); font-size: 14px;" id="mergeResultSummary">Combined unified document ready.</p>
          </div>

          <div class="doc-info-grid" style="margin-bottom: 24px;">
            <div class="info-pill">
              <div class="info-pill-label">Files Merged</div>
              <div class="info-pill-val" id="mergeResFileCount">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">Total Pages</div>
              <div class="info-pill-val" id="mergeResPageCount" style="color: #a855f7;">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">Combined Size</div>
              <div class="info-pill-val" id="mergeResSize">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">Time Taken</div>
              <div class="info-pill-val" id="mergeResTime">--</div>
            </div>
          </div>

          <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
            <button id="mergeShareBtn" class="btn btn-share hidden" style="flex: 1;">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="18" cy="5" r="3"></circle>
                <circle cx="6" cy="12" r="3"></circle>
                <circle cx="18" cy="19" r="3"></circle>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
              </svg>
              Share / Save Merged PDF
            </button>

            <a id="mergeDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);" href="#" download>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              Download Merged PDF
            </a>

            <button class="btn btn-secondary" id="mergeRestartBtn" style="flex: 1;">
              Merge More PDFs
            </button>
          </div>
        </div>
      </div>

      <!-- SUB-VIEW B: SPLIT / EXTRACT -->
      <div id="splitSubSection" class="hidden">
        <div id="splitUploadBox" class="glass-card">
          <div class="dropzone dropzone-purple" id="splitDropzone">
            <div class="dropzone-icon icon-purple">
              <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="6" cy="6" r="3"></circle>
                <circle cx="6" cy="18" r="3"></circle>
                <line x1="20" y1="4" x2="8.12" y2="15.88"></line>
                <line x1="14.47" y1="14.48" x2="20" y2="20"></line>
                <line x1="8.12" y1="8.12" x2="12" y2="12"></line>
              </svg>
            </div>
            <h3 class="desktop-only-text">Drop PDF here to extract pages</h3>
            <h3 class="mobile-only-text">📱 Tap to select PDF to split</h3>
            <p class="desktop-only-text">Extract specific pages (e.g. 1-3, 5) or separate document into sections</p>
            <p class="mobile-only-text">Extract specific pages or page ranges</p>
            
            <div class="dropzone-mobile-chips">
              <span class="mobile-chip chip-purple">✂️ Custom Page Ranges</span>
              <span class="mobile-chip chip-purple">📑 Page Extraction</span>
              <span class="mobile-chip chip-purple">🔒 100% Private</span>
            </div>

            <input type="file" id="splitFileInput" accept=".pdf,application/pdf">
          </div>

          <div id="splitConfigBox" class="hidden" style="margin-top: 18px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; gap: 8px;">
              <h3 id="splitFileTitle" style="font-size: 16px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">document.pdf</h3>
              <span class="control-badge" id="splitTotalPagesBadge" style="color: #a855f7; border-color: rgba(168, 85, 247, 0.3); background: rgba(168, 85, 247, 0.1);">10 Pages</span>
            </div>

            <div class="control-group">
              <div class="control-header">
                <span class="control-label">Pages to Extract</span>
                <span style="font-size: 12px; color: var(--text-muted);">e.g. 1-3, 5, 8-10</span>
              </div>
              <input type="text" id="splitPagesInput" placeholder="1-3, 5" value="1">
              <div style="display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap;">
                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;" id="quickRangeFirst">Page 1</button>
                <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;" id="quickRangeAll">All Pages</button>
              </div>
            </div>

            <button class="btn btn-primary" id="startSplitBtn" style="background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="6" cy="6" r="3"></circle>
                <line x1="20" y1="4" x2="8.12" y2="15.88"></line>
              </svg>
              Extract Selected Pages
            </button>
          </div>
        </div>

        <div id="splitProgressSection" class="glass-card hidden">
          <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
            <div class="spinner" style="border-left-color: #a855f7; width: 28px; height: 28px;"></div>
            <span style="font-weight: 700; font-size: 16px; color: #a855f7;">Extracting Pages from PDF...</span>
          </div>
        </div>

        <div id="splitResultSection" class="glass-card hidden">
          <div class="results-hero">
            <div class="savings-badge badge-purple" id="splitSuccessBadge">
              🎉 Pages Extracted Successfully!
            </div>
            <p style="color: var(--text-muted); font-size: 14px;" id="splitResultSummary">Extracted PDF ready for download.</p>
          </div>

          <div class="doc-info-grid" style="margin-bottom: 24px;">
            <div class="info-pill">
              <div class="info-pill-label">Extracted Pages</div>
              <div class="info-pill-val" id="splitResPageCount" style="color: #a855f7;">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">Original Pages</div>
              <div class="info-pill-val" id="splitResOrigPages">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">New PDF Size</div>
              <div class="info-pill-val" id="splitResSize">--</div>
            </div>
            <div class="info-pill">
              <div class="info-pill-label">Time Taken</div>
              <div class="info-pill-val" id="splitResTime">--</div>
            </div>
          </div>

          <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
            <button id="splitShareBtn" class="btn btn-share hidden" style="flex: 1;">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <circle cx="18" cy="5" r="3"></circle>
                <circle cx="6" cy="12" r="3"></circle>
                <circle cx="18" cy="19" r="3"></circle>
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
              </svg>
              Share / Save PDF
            </button>

            <a id="splitDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);" href="#" download>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
              Download Extracted PDF
            </a>

            <button class="btn btn-secondary" id="splitRestartBtn" style="flex: 1;">
              Split Another PDF
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- ============================================================== -->

    <!-- ============================================================== -->
    <!-- VIEW: PDF TO JPG CONVERTER TOOL                                -->
    <!-- ============================================================== -->
    <div id="pdfToJpgToolView" class="hidden">
      <div id="pdfToJpgUploadSection" class="glass-card">
        <div class="dropzone dropzone-amber" id="pdfToJpgDropzone">
          <div class="dropzone-icon icon-amber">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
              <circle cx="8.5" cy="8.5" r="1.5"></circle>
              <polyline points="21 15 16 10 5 21"></polyline>
            </svg>
          </div>
          <h3 class="desktop-only-text">Drop your PDF document here to convert to JPG</h3>
          <h3 class="mobile-only-text">📱 Tap to select PDF file</h3>
          <p class="desktop-only-text">Render PDF pages into high-resolution JPG images with instant visual gallery preview</p>
          <p class="mobile-only-text">Convert PDF pages into high-res JPG images</p>
          
          <div class="dropzone-mobile-chips">
            <span class="mobile-chip chip-amber">🖼️ High-Res JPEG</span>
            <span class="mobile-chip chip-amber">⚡ Multi-Page Gallery</span>
            <span class="mobile-chip chip-amber">🔒 100% Private</span>
          </div>

          <input type="file" id="pdfToJpgFileInput" accept=".pdf,application/pdf">
        </div>
      </div>

      <!-- Config & DPI state -->
      <div id="pdfToJpgConfigSection" class="glass-card hidden">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 8px;">
          <h3 id="pdfToJpgTitle" style="font-size: 17px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">document.pdf</h3>
          <button class="btn btn-secondary" id="changePdfToJpgBtn" style="padding: 6px 12px; font-size: 12px; white-space: nowrap; flex-shrink: 0;">Change PDF</button>
        </div>

        <div class="doc-info-grid">
          <div class="info-pill">
            <div class="info-pill-label">Original PDF Size</div>
            <div class="info-pill-val" id="pdfToJpgOrigSizeVal">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Target Format</div>
            <div class="info-pill-val" style="color: #f59e0b;">JPEG (.jpg)</div>
          </div>
        </div>

        <div style="margin: 20px 0 16px;">
          <label class="control-label" style="display: block; margin-bottom: 8px;">Rendering Resolution (DPI)</label>
          <div class="presets-row">
            <div class="preset-card preset-card-amber active" data-jpg-dpi="150">
              <div class="preset-title">⚡ Standard (150 DPI)</div>
              <div class="preset-desc">Fast, sharp, balanced for web & mobile</div>
            </div>
            <div class="preset-card preset-card-amber" data-jpg-dpi="300">
              <div class="preset-title">💎 Ultra HD (300 DPI)</div>
              <div class="preset-desc">Print-quality, ultra-crisp resolution</div>
            </div>
            <div class="preset-card preset-card-amber" data-jpg-dpi="96">
              <div class="preset-title">📱 Compact (96 DPI)</div>
              <div class="preset-desc">Lightweight file size, fast sharing</div>
            </div>
          </div>
        </div>

        <button class="btn btn-primary" id="startPdfToJpgBtn" style="background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%);">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
            <polyline points="21 15 16 10 5 21"></polyline>
          </svg>
          Convert PDF to JPG Images
        </button>
      </div>

      <!-- Processing state -->
      <div id="pdfToJpgProgressSection" class="glass-card hidden">
        <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
          <div class="spinner" style="border-left-color: #f59e0b; width: 28px; height: 28px;"></div>
          <span style="font-weight: 700; font-size: 16px; color: #f59e0b;">Rendering PDF Pages to JPG Images...</span>
        </div>
      </div>

      <!-- Result state -->
      <div id="pdfToJpgResultSection" class="glass-card hidden">
        <div class="results-hero">
          <div class="savings-badge badge-amber" id="pdfToJpgSuccessBadge">
            🎉 PDF Converted to High-Res JPG Images!
          </div>
          <p style="color: var(--text-muted); font-size: 14px;" id="pdfToJpgResultSummary">All pages rendered and ready for download.</p>
        </div>

        <div class="doc-info-grid" style="margin-bottom: 20px;">
          <div class="info-pill">
            <div class="info-pill-label">Total Pages</div>
            <div class="info-pill-val" id="pdfToJpgResPageCount" style="color: #f59e0b;">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Original PDF Size</div>
            <div class="info-pill-val" id="pdfToJpgResOrigSize">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Images Package Size</div>
            <div class="info-pill-val" id="pdfToJpgResNewSize">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Time Taken</div>
            <div class="info-pill-val" id="pdfToJpgResTime">--</div>
          </div>
        </div>

        <!-- Visual Page Thumbnails Gallery -->
        <label class="control-label" style="display: block; margin-bottom: 10px;">Rendered Page Gallery (Tap any image to open)</label>
        <div class="jpg-preview-grid" id="pdfToJpgGallery"></div>

        <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
          <button id="pdfToJpgShareBtn" class="btn btn-share hidden" style="flex: 1;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
            Share Images
          </button>

          <a id="pdfToJpgDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%);" href="#" download>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Download All (ZIP)
          </a>

          <button class="btn btn-secondary" id="pdfToJpgRestartBtn" style="flex: 1;">
            Convert Another PDF
          </button>
        </div>
      </div>
    </div>

        <!-- VIEW 4: WORD TO PDF CONVERTER TOOL                             -->
    <!-- ============================================================== -->
    <div id="wordToPdfToolView" class="hidden">
      <div id="wordUploadSection" class="glass-card">
        <div class="dropzone dropzone-word" id="wordDropzone">
          <div class="dropzone-icon icon-word">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="16" y1="13" x2="8" y2="13"></line>
              <line x1="16" y1="17" x2="8" y2="17"></line>
              <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
          </div>
          <h3 class="desktop-only-text">Drop your Word document (.docx) here, or browse</h3>
          <h3 class="mobile-only-text">📱 Tap to select Word (.docx) file</h3>
          <p class="desktop-only-text">Converts Microsoft Word documents to clean, print-ready PDF with exact tables & typography</p>
          <p class="mobile-only-text">Select .docx or .doc from Files or Cloud</p>
          
          <div class="dropzone-mobile-chips">
            <span class="mobile-chip chip-word">⚡ High-Fidelity PDF</span>
            <span class="mobile-chip chip-word">📑 Tables & Styles Preserved</span>
            <span class="mobile-chip chip-word">🔒 100% Private</span>
          </div>

          <input type="file" id="wordFileInput" accept=".docx,.doc,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword">
        </div>
      </div>

      <div id="wordProgressSection" class="glass-card hidden">
        <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
          <div class="spinner" style="border-left-color: #38bdf8; width: 28px; height: 28px;"></div>
          <span style="font-weight: 700; font-size: 16px; color: #38bdf8;">Converting Word to PDF...</span>
        </div>
      </div>

      <div id="wordResultSection" class="glass-card hidden">
        <div class="results-hero">
          <div class="savings-badge badge-word" id="wordSuccessBadge">
            🎉 Word Document Converted to PDF!
          </div>
          <p style="color: var(--text-muted); font-size: 14px;" id="wordResultSummary">Formatted and ready for download.</p>
        </div>

        <div class="doc-info-grid" style="margin-bottom: 24px;">
          <div class="info-pill">
            <div class="info-pill-label">Original Word File</div>
            <div class="info-pill-val" id="wordInfoOrigSize">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Converted PDF Size</div>
            <div class="info-pill-val" id="wordInfoPdfSize" style="color: #38bdf8;">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Page Count</div>
            <div class="info-pill-val" id="wordInfoPages">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Conversion Speed</div>
            <div class="info-pill-val" id="wordInfoTime">--</div>
          </div>
        </div>

        <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
          <button id="wordShareBtn" class="btn btn-share hidden" style="flex: 1;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
            Share / Save PDF
          </button>

          <a id="wordDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #0284c7 0%, #38bdf8 100%);" href="#" download>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Download PDF
          </a>

          <button class="btn btn-secondary" id="wordRestartBtn" style="flex: 1;">
            Convert Another
          </button>
        </div>
      </div>
    </div>

    <!-- ============================================================== -->
    <!-- VIEW 5: PDF TO WORD CONVERTER TOOL                             -->
    <!-- ============================================================== -->
    <div id="pdfToWordToolView" class="hidden">
      <div id="pdfUploadSection" class="glass-card">
        <div class="dropzone dropzone-pdf" id="pdfDropzone">
          <div class="dropzone-icon icon-pdf">
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <path d="M12 18v-6"></path>
              <path d="m9 15 3 3 3-3"></path>
            </svg>
          </div>
          <h3 class="desktop-only-text">Drop your PDF document here, or browse</h3>
          <h3 class="mobile-only-text">📱 Tap to select PDF file</h3>
          <p class="desktop-only-text">Extracts PDF text, headings, layout, images, and tables into an editable Microsoft Word (.docx) document</p>
          <p class="mobile-only-text">Select .pdf from Files or Cloud</p>
          
          <div class="dropzone-mobile-chips">
            <span class="mobile-chip chip-pdf">⚡ 100% Editable Word</span>
            <span class="mobile-chip chip-pdf">📊 Smart Table Extraction</span>
            <span class="mobile-chip chip-pdf">🔒 100% Private</span>
          </div>

          <input type="file" id="pdfFileInput" accept=".pdf,application/pdf">
        </div>
      </div>

      <div id="pdfProgressSection" class="glass-card hidden">
        <div style="display: flex; align-items: center; justify-content: center; gap: 14px; padding: 24px 0;">
          <div class="spinner" style="border-left-color: #fb7185; width: 28px; height: 28px;"></div>
          <span style="font-weight: 700; font-size: 16px; color: #fb7185;">Extracting Text, Layout & Tables to Word (.docx)...</span>
        </div>
      </div>

      <div id="pdfResultSection" class="glass-card hidden">
        <div class="results-hero">
          <div class="savings-badge badge-pdf" id="pdfSuccessBadge">
            🎉 PDF Converted to Editable Word (.docx)!
          </div>
          <p style="color: var(--text-muted); font-size: 14px;" id="pdfResultSummary">Formatted and ready for Microsoft Word.</p>
        </div>

        <div class="doc-info-grid" style="margin-bottom: 24px;">
          <div class="info-pill">
            <div class="info-pill-label">Original PDF Size</div>
            <div class="info-pill-val" id="pdfInfoOrigSize">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Converted Word Size</div>
            <div class="info-pill-val" id="pdfInfoDocxSize" style="color: #fb7185;">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Page Count</div>
            <div class="info-pill-val" id="pdfInfoPages">--</div>
          </div>
          <div class="info-pill">
            <div class="info-pill-label">Conversion Speed</div>
            <div class="info-pill-val" id="pdfInfoTime">--</div>
          </div>
        </div>

        <div class="results-action-row" style="display: flex; gap: 12px; flex-wrap: wrap;">
          <button id="pdfShareBtn" class="btn btn-share hidden" style="flex: 1;">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <circle cx="18" cy="5" r="3"></circle>
              <circle cx="6" cy="12" r="3"></circle>
              <circle cx="18" cy="19" r="3"></circle>
              <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"></line>
              <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"></line>
            </svg>
            Share / Save Docx
          </button>

          <a id="pdfDownloadBtn" class="btn btn-primary" style="flex: 2; background: linear-gradient(135deg, #e11d48 0%, #fb7185 100%);" href="#" download>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            Download Word (.docx)
          </a>

          <button class="btn btn-secondary" id="pdfRestartBtn" style="flex: 1;">
            Convert Another
          </button>
        </div>
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
      <a href="https://github.com/tejasrmap/gt-innox-video-compressor" target="_blank" rel="noopener">GitHub Repository</a>
      <span>•</span>
      <a href="https://vercel.com" target="_blank" rel="noopener">Deploy on Vercel</a>
    </div>
  </footer>

  <script>
    function triggerHaptic(duration = 10) {
      if (typeof window !== 'undefined' && window.navigator && window.navigator.vibrate) {
        try { window.navigator.vibrate(duration); } catch (e) {}
      }
    }

    function formatBytes(bytes) {
      if (!bytes || bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // =========================================================================
    // STUDIO TOOL SWITCHER LOGIC (5 Tools)
    // =========================================================================
    const studioTabs = document.querySelectorAll('.studio-tab-btn');
    const toolViews = {
      'video': document.getElementById('videoToolView'),
      'pdf-compress': document.getElementById('pdfCompressToolView'),
      'pdf-merge-split': document.getElementById('pdfMergeSplitToolView'),
      'pdf-to-jpg': document.getElementById('pdfToJpgToolView'),
      'word-to-pdf': document.getElementById('wordToPdfToolView'),
      'pdf-to-word': document.getElementById('pdfToWordToolView')
    };

    function switchTool(toolName) {
      triggerHaptic(8);
      studioTabs.forEach(tab => {
        const isMatch = tab.dataset.tool === toolName;
        tab.classList.toggle('active', isMatch);
        tab.setAttribute('aria-selected', isMatch);
      });

      Object.keys(toolViews).forEach(key => {
        if (toolViews[key]) {
          toolViews[key].classList.toggle('hidden', key !== toolName);
        }
      });

      window.location.hash = toolName;
    }

    studioTabs.forEach(tab => {
      tab.addEventListener('click', () => switchTool(tab.dataset.tool));
    });

    if (window.location.hash) {
      const hashTool = window.location.hash.replace('#', '');
      if (toolViews[hashTool]) {
        switchTool(hashTool);
      }
    }

    // =========================================================================
    // TOOL 1: VIDEO COMPRESSOR LOGIC
    // =========================================================================
    let currentTaskId = null;
    let originalFileInfo = null;
    let rawSelectedFile = null;
    let eventSource = null;
    let currentMode = 'crf';
    let wakeLock = null;
    let compressedFileObj = null;

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
    const cardOriginal = document.getElementById('cardOriginal');
    const cardCompressed = document.getElementById('cardCompressed');
    const mobileCompSwitch = document.getElementById('mobileCompSwitch');
    const downloadBtn = document.getElementById('downloadBtn');
    const shareBtn = document.getElementById('shareBtn');
    const restartBtn = document.getElementById('restartBtn');
    const changeFileBtn = document.getElementById('changeFileBtn');

    dropzone.addEventListener('click', () => { triggerHaptic(12); fileInput.click(); });
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handleUpload(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
      if (fileInput.files.length > 0) handleUpload(fileInput.files[0]);
    });

    function handleUpload(file) {
      if (!file) return;
      rawSelectedFile = file;
      dropzone.innerHTML = `<div class="spinner" style="margin-bottom: 12px;"></div><h3>Analyzing ${file.name}...</h3><p style="color: var(--text-muted); font-size: 13px; margin-top: 6px;">Preparing on-device optimizer...</p>`;

      const videoUrl = URL.createObjectURL(file);
      const tempVideo = document.createElement('video');
      tempVideo.preload = 'metadata';
      tempVideo.muted = true;
      tempVideo.playsInline = true;
      tempVideo.setAttribute('playsinline', '');
      tempVideo.setAttribute('webkit-playsinline', '');
      tempVideo.src = videoUrl;

      const timeoutFallback = setTimeout(() => { applyMetadata({ duration: 30, w: 1920, h: 1080 }); }, 1500);
      tempVideo.onloadedmetadata = () => {
        clearTimeout(timeoutFallback);
        applyMetadata({
          duration: tempVideo.duration || 1,
          w: tempVideo.videoWidth || 1920,
          h: tempVideo.videoHeight || 1080
        });
      };
      tempVideo.onerror = () => {
        clearTimeout(timeoutFallback);
        applyMetadata({ duration: 10, w: 1280, h: 720 });
      };

      function applyMetadata(meta) {
        const mins = Math.floor(meta.duration / 60);
        const secs = Math.floor(meta.duration % 60);
        originalFileInfo = {
          filename: file.name,
          size_bytes: file.size,
          size_formatted: formatBytes(file.size),
          duration: meta.duration,
          duration_formatted: `${mins}:${secs < 10 ? '0' : ''}${secs}`,
          resolution: `${meta.w}x${meta.h}`,
          fps: '30.0',
          client_url: videoUrl
        };
        populateInfo(originalFileInfo);
      }
    }

    function populateInfo(data) {
      document.getElementById('videoTitle').textContent = data.filename;
      document.getElementById('infoSize').textContent = data.size_formatted;
      document.getElementById('infoDuration').textContent = data.duration_formatted;
      document.getElementById('infoRes').textContent = data.resolution;
      document.getElementById('infoFps').textContent = data.fps ? data.fps + ' fps' : '30 fps';

      const mbSize = data.size_bytes / (1024 * 1024);
      targetSlider.max = Math.max(50, Math.ceil(mbSize * 1.2));
      targetSlider.value = Math.min(25, Math.max(5, (mbSize * 0.4).toFixed(1)));
      targetValBadge.textContent = targetSlider.value + ' MB';

      uploadSection.classList.add('hidden');
      configSection.classList.remove('hidden');
      configSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    tabCrf.addEventListener('click', () => {
      triggerHaptic(8);
      currentMode = 'crf';
      tabCrf.classList.add('active');
      tabTarget.classList.remove('active');
      crfControlGroup.classList.remove('hidden');
      targetControlGroup.classList.add('hidden');
    });

    tabTarget.addEventListener('click', () => {
      triggerHaptic(8);
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

    document.querySelectorAll('#configSection .preset-card').forEach(card => {
      card.addEventListener('click', () => {
        triggerHaptic(10);
        document.querySelectorAll('#configSection .preset-card').forEach(c => c.classList.remove('active'));
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

    function applyMobileView(view) {
      if (!cardOriginal || !cardCompressed) return;
      document.querySelectorAll('.mobile-comp-tab').forEach(t => t.classList.remove('active'));
      const activeTab = document.querySelector(`.mobile-comp-tab[data-view="${view}"]`);
      if (activeTab) activeTab.classList.add('active');

      if (window.innerWidth <= 768) {
        if (view === 'compressed') {
          cardCompressed.style.display = 'block';
          cardOriginal.style.display = 'none';
        } else if (view === 'original') {
          cardCompressed.style.display = 'none';
          cardOriginal.style.display = 'block';
        } else {
          cardCompressed.style.display = 'block';
          cardOriginal.style.display = 'block';
        }
      } else {
        cardCompressed.style.display = 'block';
        cardOriginal.style.display = 'block';
      }
    }

    document.querySelectorAll('.mobile-comp-tab').forEach(tab => {
      tab.addEventListener('click', () => { triggerHaptic(8); applyMobileView(tab.dataset.view); });
    });

    window.addEventListener('resize', () => {
      if (window.innerWidth > 768) {
        if (cardOriginal) cardOriginal.style.display = 'block';
        if (cardCompressed) cardCompressed.style.display = 'block';
      } else {
        const activeTab = document.querySelector('.mobile-comp-tab.active');
        applyMobileView(activeTab ? activeTab.dataset.view : 'compressed');
      }
    });

    startCompressBtn.addEventListener('click', async () => {
      triggerHaptic(15);
      configSection.classList.add('hidden');
      progressSection.classList.remove('hidden');
      progressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      if ('wakeLock' in navigator) {
        try { wakeLock = await navigator.wakeLock.request('screen'); } catch (e) {}
      }

      const isLocalServer = (location.hostname === 'localhost' || location.hostname === '127.0.0.1') && !location.hostname.includes('vercel');

      if (isLocalServer) {
        try {
          const uploadForm = new FormData();
          uploadForm.append('file', rawSelectedFile);
          const uploadResp = await fetch('/api/upload', { method: 'POST', body: uploadForm });
          if (!uploadResp.ok) throw new Error('Local upload failed');
          const data = await uploadResp.json();
          currentTaskId = data.task_id;

          const formData = new FormData();
          formData.append('task_id', currentTaskId);
          formData.append('mode', currentMode);
          formData.append('crf', crfSlider.value);
          formData.append('target_mb', targetSlider.value);
          formData.append('scale', scaleSelect.value);
          formData.append('codec', codecSelect.value);
          formData.append('audio', audioSelect.value);

          await fetch('/api/compress', { method: 'POST', body: formData });
          listenToProgress();
          return;
        } catch (e) {
          console.warn('Local engine fallback:', e);
        }
      }

      runBrowserCompression();
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
          if (data.progress.speed) progressSpeed.textContent = `Speed: ${data.progress.speed.toFixed(2)}x`;
          if (data.progress.eta) {
            const m = Math.floor(data.progress.eta / 60);
            const s = Math.floor(data.progress.eta % 60);
            progressEta.textContent = `ETA: ${m}:${s < 10 ? '0' : ''}${s}`;
          }
        }

        if (data.status === 'completed') {
          eventSource.close();
          releaseWakeLock();
          showBackendResults(data.result);
        } else if (data.status === 'failed' || data.status === 'cancelled') {
          eventSource.close();
          releaseWakeLock();
          alert(data.error || 'Compression halted.');
          location.reload();
        }
      };

      eventSource.onerror = () => { eventSource.close(); releaseWakeLock(); };
    }

    async function releaseWakeLock() {
      if (wakeLock) {
        try { await wakeLock.release(); } catch (e) {}
        wakeLock = null;
      }
    }

    async function runBrowserCompression() {
      const video = document.createElement('video');
      video.src = URL.createObjectURL(rawSelectedFile);
      video.playsInline = true;
      video.setAttribute('playsinline', '');
      video.setAttribute('webkit-playsinline', '');
      video.style.cssText = 'position:fixed;top:-9999px;left:-9999px;width:2px;height:2px;opacity:0.01;pointer-events:none;';
      document.body.appendChild(video);

      await new Promise((res) => { video.onloadeddata = res; video.load(); });

      let targetW = video.videoWidth || 1280;
      let targetH = video.videoHeight || 720;
      const scale = scaleSelect.value;
      if (scale === '1080p' && targetH > 1080) {
        targetW = Math.round((video.videoWidth * 1080) / video.videoHeight);
        targetH = 1080;
      } else if (scale === '720p' && targetH > 720) {
        targetW = Math.round((video.videoWidth * 720) / video.videoHeight);
        targetH = 720;
      } else if (scale === '480p' && targetH > 480) {
        targetW = Math.round((video.videoWidth * 480) / video.videoHeight);
        targetH = 480;
      } else if (scale === '360p' && targetH > 360) {
        targetW = Math.round((video.videoWidth * 360) / video.videoHeight);
        targetH = 360;
      } else if (scale === '50%') {
        targetW = Math.round(targetW * 0.5);
        targetH = Math.round(targetH * 0.5);
      }

      targetW = Math.floor(targetW / 2) * 2;
      targetH = Math.floor(targetH / 2) * 2;

      const canvas = document.createElement('canvas');
      canvas.width = targetW;
      canvas.height = targetH;
      const ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });

      let audioTrack = null;
      let audioCtx = null;
      try {
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (AudioContextClass) {
          audioCtx = new AudioContextClass();
          if (audioCtx.state === 'suspended') await audioCtx.resume();
          const source = audioCtx.createMediaElementSource(video);
          const dest = audioCtx.createMediaStreamDestination();
          source.connect(dest);
          const tracks = dest.stream.getAudioTracks();
          if (tracks.length > 0 && audioSelect.value !== 'mute') audioTrack = tracks[0];
        }
      } catch (e) { console.warn('Audio setup:', e); }

      const stream = canvas.captureStream ? canvas.captureStream(30) : canvas.webkitCaptureStream(30);
      if (audioTrack) stream.addTrack(audioTrack);

      const dur = video.duration || 10;
      let bitrate = 1500000;
      if (currentMode === 'target_size') {
        bitrate = Math.max(120000, Math.floor((parseFloat(targetSlider.value) * 1024 * 1024 * 8) / dur));
      } else {
        const crf = parseInt(crfSlider.value);
        bitrate = Math.max(250000, Math.floor(5000000 * Math.pow(0.86, crf - 18)));
      }

      let mime = 'video/webm;codecs=vp9,opus';
      let ext = 'webm';
      if (MediaRecorder.isTypeSupported('video/mp4;codecs=avc1,mp4a.40.2')) {
        mime = 'video/mp4;codecs=avc1,mp4a.40.2'; ext = 'mp4';
      } else if (MediaRecorder.isTypeSupported('video/mp4')) {
        mime = 'video/mp4'; ext = 'mp4';
      } else if (!MediaRecorder.isTypeSupported(mime)) {
        mime = 'video/webm'; ext = 'webm';
      }

      let recorder;
      try {
        recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: bitrate });
      } catch (e) {
        recorder = new MediaRecorder(stream, { videoBitsPerSecond: bitrate });
      }

      const chunks = [];
      recorder.ondataavailable = (e) => { if (e.data && e.data.size > 0) chunks.push(e.data); };
      recorder.onstop = () => {
        releaseWakeLock();
        if (video.parentNode) video.parentNode.removeChild(video);
        if (audioCtx) { try { audioCtx.close(); } catch (err) {} }

        const blob = new Blob(chunks, { type: mime });
        const compUrl = URL.createObjectURL(blob);
        const compBytes = blob.size;
        const origBytes = rawSelectedFile.size;
        const savedBytes = Math.max(0, origBytes - compBytes);
        const savedPct = ((savedBytes / origBytes) * 100).toFixed(1);
        const outputFilename = `${rawSelectedFile.name.replace(/\.[^/.]+$/, '')}_GT_INNOX_compressed.${ext}`;

        progressSection.classList.add('hidden');
        resultsSection.classList.remove('hidden');
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        savingsBadge.textContent = `🎉 Reduced by ${savedPct}% (${formatBytes(savedBytes)} Saved)`;
        compOrigSize.textContent = formatBytes(origBytes);
        compNewSize.textContent = formatBytes(compBytes);

        playerOriginal.src = URL.createObjectURL(rawSelectedFile);
        playerCompressed.src = compUrl;

        downloadBtn.href = compUrl;
        downloadBtn.download = outputFilename;

        try {
          compressedFileObj = new File([blob], outputFilename, { type: mime });
          if (navigator.canShare && navigator.canShare({ files: [compressedFileObj] })) {
            shareBtn.classList.remove('hidden');
            shareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [compressedFileObj],
                  title: 'GT INNOX Compressed Video',
                  text: `Compressed with GT INNOX Studio (${savingsBadge.textContent.trim()})`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { shareBtn.classList.add('hidden'); }
        } catch (fErr) { shareBtn.classList.add('hidden'); }

        applyMobileView('compressed');
      };

      recorder.start(500);

      video.playbackRate = 1.0;
      try {
        video.muted = false;
        video.volume = 1.0;
        await video.play();
      } catch (playErr) {
        video.muted = true;
        try { await video.play(); } catch (e2) {
          alert('Could not start video playback.');
          progressSection.classList.add('hidden');
          configSection.classList.remove('hidden');
          if (video.parentNode) video.parentNode.removeChild(video);
          releaseWakeLock();
          return;
        }
      }

      const startTime = Date.now();
      video.onended = () => { if (recorder.state === 'recording') recorder.stop(); };

      function renderFrame() {
        if (video.ended || video.paused) {
          if (recorder.state === 'recording') recorder.stop();
          return;
        }
        ctx.drawImage(video, 0, 0, targetW, targetH);
        const cur = video.currentTime;
        const pct = Math.min(99, (cur / dur) * 100).toFixed(1);
        progressFill.style.width = pct + '%';
        progressPct.textContent = pct + '%';

        const elapsed = (Date.now() - startTime) / 1000;
        const speed = cur / (elapsed || 0.1);
        progressSpeed.textContent = `Speed: ${speed.toFixed(2)}x`;
        const eta = Math.max(0, (dur - cur) / (speed || 1));
        const m = Math.floor(eta / 60);
        const s = Math.floor(eta % 60);
        progressEta.textContent = `ETA: ${m}:${s < 10 ? '0' : ''}${s}`;

        if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(renderFrame);
        else requestAnimationFrame(renderFrame);
      }

      if ('requestVideoFrameCallback' in video) video.requestVideoFrameCallback(renderFrame);
      else requestAnimationFrame(renderFrame);
    }

    cancelBtn.addEventListener('click', async () => {
      triggerHaptic(10);
      releaseWakeLock();
      if (currentTaskId) {
        try { await fetch(`/api/cancel/${currentTaskId}`, { method: 'POST' }); } catch (e) {}
      }
      location.reload();
    });

    function showBackendResults(result) {
      progressSection.classList.add('hidden');
      resultsSection.classList.remove('hidden');
      resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

      savingsBadge.textContent = `🎉 Reduced by ${result.saved_percent}% (${result.saved_bytes_formatted} Saved)`;
      compOrigSize.textContent = originalFileInfo.size_formatted;
      compNewSize.textContent = result.compressed_size_formatted;

      playerOriginal.src = `/api/stream/${currentTaskId}/original`;
      playerCompressed.src = `/api/stream/${currentTaskId}/compressed`;

      downloadBtn.href = `/api/download/${currentTaskId}`;
      downloadBtn.download = `${originalFileInfo.filename.replace(/\.[^/.]+$/, '')}_GT_INNOX_compressed.mp4`;

      applyMobileView('compressed');
    }

    restartBtn.addEventListener('click', () => { triggerHaptic(8); location.reload(); });
    changeFileBtn.addEventListener('click', () => { triggerHaptic(8); location.reload(); });

    // =========================================================================
    // TOOL 2: PDF COMPRESSOR LOGIC
    // =========================================================================
    let rawPdfCompFile = null;
    let currentPdfPreset = 'balanced';

    const pdfCompDropzone = document.getElementById('pdfCompDropzone');
    const pdfCompFileInput = document.getElementById('pdfCompFileInput');
    const pdfCompUploadSection = document.getElementById('pdfCompUploadSection');
    const pdfCompConfigSection = document.getElementById('pdfCompConfigSection');
    const pdfCompProgressSection = document.getElementById('pdfCompProgressSection');
    const pdfCompResultSection = document.getElementById('pdfCompResultSection');
    const startPdfCompressBtn = document.getElementById('startPdfCompressBtn');
    const pdfCompDownloadBtn = document.getElementById('pdfCompDownloadBtn');
    const pdfCompShareBtn = document.getElementById('pdfCompShareBtn');
    const pdfCompRestartBtn = document.getElementById('pdfCompRestartBtn');
    const changePdfCompBtn = document.getElementById('changePdfCompBtn');

    pdfCompDropzone.addEventListener('click', () => { triggerHaptic(12); pdfCompFileInput.click(); });
    pdfCompDropzone.addEventListener('dragover', (e) => { e.preventDefault(); pdfCompDropzone.classList.add('dragover'); });
    pdfCompDropzone.addEventListener('dragleave', () => pdfCompDropzone.classList.remove('dragover'));
    pdfCompDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      pdfCompDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handlePdfCompSelect(e.dataTransfer.files[0]);
    });
    pdfCompFileInput.addEventListener('change', () => {
      if (pdfCompFileInput.files.length > 0) handlePdfCompSelect(pdfCompFileInput.files[0]);
    });

    function handlePdfCompSelect(file) {
      if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
        alert('Please select a valid PDF file (.pdf).');
        return;
      }
      rawPdfCompFile = file;
      document.getElementById('pdfCompTitle').textContent = file.name;
      document.getElementById('pdfCompOrigSizeVal').textContent = formatBytes(file.size);
      document.getElementById('pdfCompPageVal').textContent = 'Estimating...';

      pdfCompUploadSection.classList.add('hidden');
      pdfCompConfigSection.classList.remove('hidden');
      pdfCompConfigSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    document.querySelectorAll('[data-pdf-preset]').forEach(card => {
      card.addEventListener('click', () => {
        triggerHaptic(10);
        document.querySelectorAll('[data-pdf-preset]').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        currentPdfPreset = card.dataset.pdfPreset;
      });
    });

    changePdfCompBtn.addEventListener('click', () => {
      pdfCompFileInput.value = '';
      pdfCompConfigSection.classList.add('hidden');
      pdfCompUploadSection.classList.remove('hidden');
    });

    startPdfCompressBtn.addEventListener('click', async () => {
      if (!rawPdfCompFile) return;
      triggerHaptic(15);
      pdfCompConfigSection.classList.add('hidden');
      pdfCompProgressSection.classList.remove('hidden');
      pdfCompProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        formData.append('file', rawPdfCompFile);
        formData.append('quality', currentPdfPreset);

        const resp = await fetch('/api/pdf/compress', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Compression failed' }));
          throw new Error(err.detail || 'PDF compression failed');
        }

        const data = await resp.json();
        pdfCompProgressSection.classList.add('hidden');
        pdfCompResultSection.classList.remove('hidden');
        pdfCompResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('pdfCompSuccessBadge').textContent = `🎉 Reduced by ${data.saved_percent}% (${data.saved_bytes_formatted} Saved)`;
        document.getElementById('pdfCompResOrigSize').textContent = data.original_size_formatted;
        document.getElementById('pdfCompResNewSize').textContent = data.compressed_size_formatted;
        document.getElementById('pdfCompResSaved').textContent = data.saved_bytes_formatted;
        document.getElementById('pdfCompResTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('pdfCompResultSummary').textContent = `${data.compressed_filename} (${data.page_count} pages) ready.`;

        pdfCompDownloadBtn.href = data.download_url;
        pdfCompDownloadBtn.download = data.compressed_filename;

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const pdfShareFile = new File([blob], data.compressed_filename, { type: 'application/pdf' });
          if (navigator.canShare && navigator.canShare({ files: [pdfShareFile] })) {
            pdfCompShareBtn.classList.remove('hidden');
            pdfCompShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [pdfShareFile],
                  title: 'GT INNOX Compressed PDF',
                  text: `Compressed with GT INNOX Studio (${data.saved_bytes_formatted} saved)`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { pdfCompShareBtn.classList.add('hidden'); }
        } catch (e) { pdfCompShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'PDF compression failed. Please try again.');
        pdfCompProgressSection.classList.add('hidden');
        pdfCompConfigSection.classList.remove('hidden');
      }
    });

    pdfCompRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      pdfCompFileInput.value = '';
      rawPdfCompFile = null;
      pdfCompResultSection.classList.add('hidden');
      pdfCompUploadSection.classList.remove('hidden');
    });

    // =========================================================================
    // TOOL 3: PDF MERGE & SPLIT LOGIC
    // =========================================================================
    const tabMergeMode = document.getElementById('tabMergeMode');
    const tabSplitMode = document.getElementById('tabSplitMode');
    const mergeSubSection = document.getElementById('mergeSubSection');
    const splitSubSection = document.getElementById('splitSubSection');

    tabMergeMode.addEventListener('click', () => {
      triggerHaptic(8);
      tabMergeMode.classList.add('active');
      tabSplitMode.classList.remove('active');
      mergeSubSection.classList.remove('hidden');
      splitSubSection.classList.add('hidden');
    });

    tabSplitMode.addEventListener('click', () => {
      triggerHaptic(8);
      tabSplitMode.classList.add('active');
      tabMergeMode.classList.remove('active');
      splitSubSection.classList.remove('hidden');
      mergeSubSection.classList.add('hidden');
    });

    // Sub-tool: Merge Logic
    let mergeFilesList = [];
    const mergeDropzone = document.getElementById('mergeDropzone');
    const mergeFileInput = document.getElementById('mergeFileInput');
    const mergeSelectedListContainer = document.getElementById('mergeSelectedListContainer');
    const mergeFileList = document.getElementById('mergeFileList');
    const mergeFileCountLabel = document.getElementById('mergeFileCountLabel');
    const clearMergeFilesBtn = document.getElementById('clearMergeFilesBtn');
    const startMergeBtn = document.getElementById('startMergeBtn');
    const mergeProgressSection = document.getElementById('mergeProgressSection');
    const mergeResultSection = document.getElementById('mergeResultSection');
    const mergeDownloadBtn = document.getElementById('mergeDownloadBtn');
    const mergeShareBtn = document.getElementById('mergeShareBtn');
    const mergeRestartBtn = document.getElementById('mergeRestartBtn');

    mergeDropzone.addEventListener('click', () => { triggerHaptic(12); mergeFileInput.click(); });
    mergeDropzone.addEventListener('dragover', (e) => { e.preventDefault(); mergeDropzone.classList.add('dragover'); });
    mergeDropzone.addEventListener('dragleave', () => mergeDropzone.classList.remove('dragover'));
    mergeDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      mergeDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) addMergeFiles(Array.from(e.dataTransfer.files));
    });
    mergeFileInput.addEventListener('change', () => {
      if (mergeFileInput.files.length > 0) addMergeFiles(Array.from(mergeFileInput.files));
    });

    function addMergeFiles(files) {
      const valid = files.filter(f => f.name.toLowerCase().endsWith('.pdf'));
      if (valid.length === 0) {
        alert('Please select valid PDF files (.pdf).');
        return;
      }
      mergeFilesList = mergeFilesList.concat(valid);
      renderMergeList();
    }

    function renderMergeList() {
      if (mergeFilesList.length === 0) {
        mergeSelectedListContainer.classList.add('hidden');
        return;
      }
      mergeSelectedListContainer.classList.remove('hidden');
      mergeFileCountLabel.textContent = `${mergeFilesList.length} PDFs selected (in merge order)`;
      mergeFileList.innerHTML = mergeFilesList.map((f, i) => `
        <div class="file-chip-item">
          <span>${i + 1}. 📄 <b>${f.name}</b> (${formatBytes(f.size)})</span>
          <button class="btn btn-secondary" style="padding: 2px 8px; font-size: 11px;" onclick="removeMergeFile(${i})">Remove</button>
        </div>
      `).join('');
    }

    window.removeMergeFile = (index) => {
      mergeFilesList.splice(index, 1);
      renderMergeList();
    };

    clearMergeFilesBtn.addEventListener('click', () => {
      mergeFilesList = [];
      renderMergeList();
    });

    startMergeBtn.addEventListener('click', async () => {
      if (mergeFilesList.length < 2) {
        alert('Please add at least 2 PDF files to merge.');
        return;
      }
      triggerHaptic(15);
      document.getElementById('mergeUploadBox').classList.add('hidden');
      mergeProgressSection.classList.remove('hidden');
      mergeProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        mergeFilesList.forEach(f => formData.append('files', f));

        const resp = await fetch('/api/pdf/merge', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Merge failed' }));
          throw new Error(err.detail || 'PDF merge failed');
        }

        const data = await resp.json();
        mergeProgressSection.classList.add('hidden');
        mergeResultSection.classList.remove('hidden');
        mergeResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('mergeResFileCount').textContent = `${data.file_count} Files`;
        document.getElementById('mergeResPageCount').textContent = `${data.page_count} Pages`;
        document.getElementById('mergeResSize').textContent = data.merged_size_formatted;
        document.getElementById('mergeResTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('mergeResultSummary').textContent = `${data.filename} (${data.page_count} pages) merged successfully.`;

        mergeDownloadBtn.href = data.download_url;
        mergeDownloadBtn.download = data.filename;

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const pdfShareFile = new File([blob], data.filename, { type: 'application/pdf' });
          if (navigator.canShare && navigator.canShare({ files: [pdfShareFile] })) {
            mergeShareBtn.classList.remove('hidden');
            mergeShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [pdfShareFile],
                  title: 'GT INNOX Merged PDF',
                  text: `Merged with GT INNOX Studio`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { mergeShareBtn.classList.add('hidden'); }
        } catch (e) { mergeShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'PDF merge failed.');
        mergeProgressSection.classList.add('hidden');
        document.getElementById('mergeUploadBox').classList.remove('hidden');
      }
    });

    mergeRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      mergeFilesList = [];
      renderMergeList();
      mergeResultSection.classList.add('hidden');
      document.getElementById('mergeUploadBox').classList.remove('hidden');
    });

    // Sub-tool: Split Logic
    let splitFileObj = null;
    const splitDropzone = document.getElementById('splitDropzone');
    const splitFileInput = document.getElementById('splitFileInput');
    const splitConfigBox = document.getElementById('splitConfigBox');
    const splitPagesInput = document.getElementById('splitPagesInput');
    const startSplitBtn = document.getElementById('startSplitBtn');
    const splitProgressSection = document.getElementById('splitProgressSection');
    const splitResultSection = document.getElementById('splitResultSection');
    const splitDownloadBtn = document.getElementById('splitDownloadBtn');
    const splitShareBtn = document.getElementById('splitShareBtn');
    const splitRestartBtn = document.getElementById('splitRestartBtn');

    splitDropzone.addEventListener('click', () => { triggerHaptic(12); splitFileInput.click(); });
    splitDropzone.addEventListener('dragover', (e) => { e.preventDefault(); splitDropzone.classList.add('dragover'); });
    splitDropzone.addEventListener('dragleave', () => splitDropzone.classList.remove('dragover'));
    splitDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      splitDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handleSplitSelect(e.dataTransfer.files[0]);
    });
    splitFileInput.addEventListener('change', () => {
      if (splitFileInput.files.length > 0) handleSplitSelect(splitFileInput.files[0]);
    });

    function handleSplitSelect(file) {
      if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
        alert('Please select a valid PDF file.');
        return;
      }
      splitFileObj = file;
      document.getElementById('splitFileTitle').textContent = file.name;
      splitConfigBox.classList.remove('hidden');
      splitPagesInput.value = '1';
      splitConfigBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    document.getElementById('quickRangeFirst').addEventListener('click', () => { splitPagesInput.value = '1'; });
    document.getElementById('quickRangeAll').addEventListener('click', () => { splitPagesInput.value = 'all'; });

    startSplitBtn.addEventListener('click', async () => {
      if (!splitFileObj) return;
      triggerHaptic(15);
      document.getElementById('splitUploadBox').classList.add('hidden');
      splitProgressSection.classList.remove('hidden');
      splitProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        formData.append('file', splitFileObj);
        formData.append('pages', splitPagesInput.value.trim() || '1');

        const resp = await fetch('/api/pdf/split', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Split failed' }));
          throw new Error(err.detail || 'PDF split failed');
        }

        const data = await resp.json();
        splitProgressSection.classList.add('hidden');
        splitResultSection.classList.remove('hidden');
        splitResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('splitResPageCount').textContent = `${data.extracted_page_count} Pages`;
        document.getElementById('splitResOrigPages').textContent = `${data.total_pages} Pages`;
        document.getElementById('splitResSize').textContent = data.split_size_formatted;
        document.getElementById('splitResTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('splitResultSummary').textContent = `${data.extracted_filename} (pages ${data.extracted_pages_list.join(', ')}) extracted.`;

        splitDownloadBtn.href = data.download_url;
        splitDownloadBtn.download = data.extracted_filename;

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const pdfShareFile = new File([blob], data.extracted_filename, { type: 'application/pdf' });
          if (navigator.canShare && navigator.canShare({ files: [pdfShareFile] })) {
            splitShareBtn.classList.remove('hidden');
            splitShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [pdfShareFile],
                  title: 'GT INNOX Extracted PDF',
                  text: `Extracted with GT INNOX Studio`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { splitShareBtn.classList.add('hidden'); }
        } catch (e) { splitShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'PDF extraction failed.');
        splitProgressSection.classList.add('hidden');
        document.getElementById('splitUploadBox').classList.remove('hidden');
      }
    });

    splitRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      splitFileInput.value = '';
      splitFileObj = null;
      splitConfigBox.classList.add('hidden');
      splitResultSection.classList.add('hidden');
      document.getElementById('splitUploadBox').classList.remove('hidden');
    });

    // =========================================================================
// =========================================================================
    // TOOL: PDF TO JPG CONVERTER LOGIC
    // =========================================================================
    let rawPdfToJpgFile = null;
    let currentJpgDpi = 150;

    const pdfToJpgDropzone = document.getElementById('pdfToJpgDropzone');
    const pdfToJpgFileInput = document.getElementById('pdfToJpgFileInput');
    const pdfToJpgUploadSection = document.getElementById('pdfToJpgUploadSection');
    const pdfToJpgConfigSection = document.getElementById('pdfToJpgConfigSection');
    const pdfToJpgProgressSection = document.getElementById('pdfToJpgProgressSection');
    const pdfToJpgResultSection = document.getElementById('pdfToJpgResultSection');
    const pdfToJpgGallery = document.getElementById('pdfToJpgGallery');
    const startPdfToJpgBtn = document.getElementById('startPdfToJpgBtn');
    const pdfToJpgDownloadBtn = document.getElementById('pdfToJpgDownloadBtn');
    const pdfToJpgShareBtn = document.getElementById('pdfToJpgShareBtn');
    const pdfToJpgRestartBtn = document.getElementById('pdfToJpgRestartBtn');
    const changePdfToJpgBtn = document.getElementById('changePdfToJpgBtn');

    pdfToJpgDropzone.addEventListener('click', () => { triggerHaptic(12); pdfToJpgFileInput.click(); });
    pdfToJpgDropzone.addEventListener('dragover', (e) => { e.preventDefault(); pdfToJpgDropzone.classList.add('dragover'); });
    pdfToJpgDropzone.addEventListener('dragleave', () => pdfToJpgDropzone.classList.remove('dragover'));
    pdfToJpgDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      pdfToJpgDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handlePdfToJpgSelect(e.dataTransfer.files[0]);
    });
    pdfToJpgFileInput.addEventListener('change', () => {
      if (pdfToJpgFileInput.files.length > 0) handlePdfToJpgSelect(pdfToJpgFileInput.files[0]);
    });

    function handlePdfToJpgSelect(file) {
      if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
        alert('Please select a valid PDF file (.pdf).');
        return;
      }
      rawPdfToJpgFile = file;
      document.getElementById('pdfToJpgTitle').textContent = file.name;
      document.getElementById('pdfToJpgOrigSizeVal').textContent = formatBytes(file.size);

      pdfToJpgUploadSection.classList.add('hidden');
      pdfToJpgConfigSection.classList.remove('hidden');
      pdfToJpgConfigSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    document.querySelectorAll('[data-jpg-dpi]').forEach(card => {
      card.addEventListener('click', () => {
        triggerHaptic(10);
        document.querySelectorAll('[data-jpg-dpi]').forEach(c => c.classList.remove('active'));
        card.classList.add('active');
        currentJpgDpi = parseInt(card.dataset.jpgDpi) || 150;
      });
    });

    changePdfToJpgBtn.addEventListener('click', () => {
      pdfToJpgFileInput.value = '';
      pdfToJpgConfigSection.classList.add('hidden');
      pdfToJpgUploadSection.classList.remove('hidden');
    });

    startPdfToJpgBtn.addEventListener('click', async () => {
      if (!rawPdfToJpgFile) return;
      triggerHaptic(15);
      pdfToJpgConfigSection.classList.add('hidden');
      pdfToJpgProgressSection.classList.remove('hidden');
      pdfToJpgProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        formData.append('file', rawPdfToJpgFile);
        formData.append('dpi', currentJpgDpi);

        const resp = await fetch('/api/pdf/to-jpg', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Conversion failed' }));
          throw new Error(err.detail || 'PDF to JPG conversion failed');
        }

        const data = await resp.json();
        pdfToJpgProgressSection.classList.add('hidden');
        pdfToJpgResultSection.classList.remove('hidden');
        pdfToJpgResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('pdfToJpgSuccessBadge').textContent = `🎉 Converted ${data.page_count} ${data.page_count === 1 ? 'Page' : 'Pages'} to JPG!`;
        document.getElementById('pdfToJpgResPageCount').textContent = `${data.page_count} Pages`;
        document.getElementById('pdfToJpgResOrigSize').textContent = data.original_size_formatted;
        document.getElementById('pdfToJpgResNewSize').textContent = data.converted_size_formatted;
        document.getElementById('pdfToJpgResTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('pdfToJpgResultSummary').textContent = `${data.converted_filename} ready for download.`;

        pdfToJpgDownloadBtn.href = data.download_url;
        pdfToJpgDownloadBtn.download = data.converted_filename;
        pdfToJpgDownloadBtn.innerHTML = `
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          ${data.is_zip ? 'Download All Pages (ZIP)' : 'Download JPG Image'}
        `;

        // Render preview thumbnails
        pdfToJpgGallery.innerHTML = (data.pages_info || []).map(p => `
          <div class="jpg-preview-card">
            <a href="${p.thumbnail_url}" target="_blank" title="View Full Page ${p.page}">
              <img src="${p.thumbnail_url}" alt="Page ${p.page}" loading="lazy" />
            </a>
            <div class="jpg-preview-card-meta">
              Page ${p.page} • ${p.size_formatted}
            </div>
          </div>
        `).join('');

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const shareFile = new File([blob], data.converted_filename, { type: data.media_type });
          if (navigator.canShare && navigator.canShare({ files: [shareFile] })) {
            pdfToJpgShareBtn.classList.remove('hidden');
            pdfToJpgShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [shareFile],
                  title: 'GT INNOX Converted JPGs',
                  text: `Converted with GT INNOX Studio`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { pdfToJpgShareBtn.classList.add('hidden'); }
        } catch (e) { pdfToJpgShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'PDF to JPG conversion failed.');
        pdfToJpgProgressSection.classList.add('hidden');
        pdfToJpgConfigSection.classList.remove('hidden');
      }
    });

    pdfToJpgRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      pdfToJpgFileInput.value = '';
      rawPdfToJpgFile = null;
      pdfToJpgResultSection.classList.add('hidden');
      pdfToJpgUploadSection.classList.remove('hidden');
    });

    // TOOL 5: WORD TO PDF CONVERTER LOGIC
    // =========================================================================
    const wordDropzone = document.getElementById('wordDropzone');
    const wordFileInput = document.getElementById('wordFileInput');
    const wordUploadSection = document.getElementById('wordUploadSection');
    const wordProgressSection = document.getElementById('wordProgressSection');
    const wordResultSection = document.getElementById('wordResultSection');
    const wordDownloadBtn = document.getElementById('wordDownloadBtn');
    const wordShareBtn = document.getElementById('wordShareBtn');
    const wordRestartBtn = document.getElementById('wordRestartBtn');

    wordDropzone.addEventListener('click', () => { triggerHaptic(12); wordFileInput.click(); });
    wordDropzone.addEventListener('dragover', (e) => { e.preventDefault(); wordDropzone.classList.add('dragover'); });
    wordDropzone.addEventListener('dragleave', () => wordDropzone.classList.remove('dragover'));
    wordDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      wordDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handleWordUpload(e.dataTransfer.files[0]);
    });
    wordFileInput.addEventListener('change', () => {
      if (wordFileInput.files.length > 0) handleWordUpload(wordFileInput.files[0]);
    });

    async function handleWordUpload(file) {
      if (!file || (!file.name.toLowerCase().endsWith('.docx') && !file.name.toLowerCase().endsWith('.doc'))) {
        alert('Please select a valid Word document (.docx or .doc).');
        return;
      }

      wordUploadSection.classList.add('hidden');
      wordProgressSection.classList.remove('hidden');
      wordProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        formData.append('file', file);

        const resp = await fetch('/api/convert/word-to-pdf', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Conversion failed' }));
          throw new Error(err.detail || 'Word to PDF conversion failed');
        }

        const data = await resp.json();
        wordProgressSection.classList.add('hidden');
        wordResultSection.classList.remove('hidden');
        wordResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('wordInfoOrigSize').textContent = data.original_size_formatted;
        document.getElementById('wordInfoPdfSize').textContent = data.converted_size_formatted;
        document.getElementById('wordInfoPages').textContent = data.page_count + (data.page_count === 1 ? ' Page' : ' Pages');
        document.getElementById('wordInfoTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('wordResultSummary').textContent = `${data.converted_filename} ready for download.`;

        wordDownloadBtn.href = data.download_url;
        wordDownloadBtn.download = data.converted_filename;

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const pdfShareFile = new File([blob], data.converted_filename, { type: 'application/pdf' });
          if (navigator.canShare && navigator.canShare({ files: [pdfShareFile] })) {
            wordShareBtn.classList.remove('hidden');
            wordShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [pdfShareFile],
                  title: 'GT INNOX Converted PDF',
                  text: `Converted with GT INNOX Studio`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { wordShareBtn.classList.add('hidden'); }
        } catch (e) { wordShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'Word to PDF conversion failed.');
        wordProgressSection.classList.add('hidden');
        wordUploadSection.classList.remove('hidden');
      }
    }

    wordRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      wordFileInput.value = '';
      wordResultSection.classList.add('hidden');
      wordUploadSection.classList.remove('hidden');
    });

    // =========================================================================
    // TOOL 5: PDF TO WORD CONVERTER LOGIC
    // =========================================================================
    const pdfDropzone = document.getElementById('pdfDropzone');
    const pdfFileInput = document.getElementById('pdfFileInput');
    const pdfUploadSection = document.getElementById('pdfUploadSection');
    const pdfProgressSection = document.getElementById('pdfProgressSection');
    const pdfResultSection = document.getElementById('pdfResultSection');
    const pdfDownloadBtn = document.getElementById('pdfDownloadBtn');
    const pdfShareBtn = document.getElementById('pdfShareBtn');
    const pdfRestartBtn = document.getElementById('pdfRestartBtn');

    pdfDropzone.addEventListener('click', () => { triggerHaptic(12); pdfFileInput.click(); });
    pdfDropzone.addEventListener('dragover', (e) => { e.preventDefault(); pdfDropzone.classList.add('dragover'); });
    pdfDropzone.addEventListener('dragleave', () => pdfDropzone.classList.remove('dragover'));
    pdfDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      pdfDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files.length > 0) handlePdfUpload(e.dataTransfer.files[0]);
    });
    pdfFileInput.addEventListener('change', () => {
      if (pdfFileInput.files.length > 0) handlePdfUpload(pdfFileInput.files[0]);
    });

    async function handlePdfUpload(file) {
      if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
        alert('Please select a valid PDF document (.pdf).');
        return;
      }

      pdfUploadSection.classList.add('hidden');
      pdfProgressSection.classList.remove('hidden');
      pdfProgressSection.scrollIntoView({ behavior: 'smooth', block: 'center' });

      try {
        const formData = new FormData();
        formData.append('file', file);

        const resp = await fetch('/api/convert/pdf-to-word', { method: 'POST', body: formData });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ detail: 'Conversion failed' }));
          throw new Error(err.detail || 'PDF to Word conversion failed');
        }

        const data = await resp.json();
        pdfProgressSection.classList.add('hidden');
        pdfResultSection.classList.remove('hidden');
        pdfResultSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        document.getElementById('pdfInfoOrigSize').textContent = data.original_size_formatted;
        document.getElementById('pdfInfoDocxSize').textContent = data.converted_size_formatted;
        document.getElementById('pdfInfoPages').textContent = data.page_count + (data.page_count === 1 ? ' Page' : ' Pages');
        document.getElementById('pdfInfoTime').textContent = `${data.time_taken_sec}s`;
        document.getElementById('pdfResultSummary').textContent = `${data.converted_filename} ready for download.`;

        pdfDownloadBtn.href = data.download_url;
        pdfDownloadBtn.download = data.converted_filename;

        try {
          const fileResp = await fetch(data.download_url);
          const blob = await fileResp.blob();
          const docxShareFile = new File([blob], data.converted_filename, { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
          if (navigator.canShare && navigator.canShare({ files: [docxShareFile] })) {
            pdfShareBtn.classList.remove('hidden');
            pdfShareBtn.onclick = async () => {
              triggerHaptic(12);
              try {
                await navigator.share({
                  files: [docxShareFile],
                  title: 'GT INNOX Converted Word Document',
                  text: `Converted with GT INNOX Studio`
                });
              } catch (shErr) { if (shErr.name !== 'AbortError') console.warn(shErr); }
            };
          } else { pdfShareBtn.classList.add('hidden'); }
        } catch (e) { pdfShareBtn.classList.add('hidden'); }
      } catch (err) {
        alert(err.message || 'PDF to Word conversion failed.');
        pdfProgressSection.classList.add('hidden');
        pdfUploadSection.classList.remove('hidden');
      }
    }

    pdfRestartBtn.addEventListener('click', () => {
      triggerHaptic(8);
      pdfFileInput.value = '';
      pdfResultSection.classList.add('hidden');
      pdfUploadSection.classList.remove('hidden');
    });
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
