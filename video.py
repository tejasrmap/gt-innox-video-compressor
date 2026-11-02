"""
=============================================================================
                    Antigravity Video Compressor
=============================================================================
A modern, high-performance video compression tool with both an intuitive
GUI and a powerful CLI.

Features:
- Dual Modes:
  1. Target File Size (e.g., Discord 25MB, WhatsApp 16MB, Email 25MB, Custom MB)
  2. Quality / CRF (Constant Rate Factor for optimal visual quality)
- Codecs: H.264 (Universal compatibility), H.265/HEVC (Extra space saving)
- Resolution downscaling: 1080p, 720p, 480p, 360p, or percentage scaling (50%, 75%)
- Audio compression & stripping
- Hardware Acceleration support (NVENC, QSV, AMF) with CPU fallback
- Batch processing queue
- Real-time progress tracking (speed, ETA, percentage, savings)
- Modern themed UI powered by ttkbootstrap / Tkinter
=============================================================================
"""

import sys
import os
import time
import math
import shutil
import argparse
import subprocess
import threading
from typing import Optional, Dict, Any, Callable, List

# Ensure safe console output encoding on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Safely import GUI libraries (ttkbootstrap/tkinter); safe fallback on headless/Vercel
HAS_GUI = False
HAS_TTKBOOTSTRAP = False
tb = None
tk = None
filedialog = None
Messagebox = None

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
    from ttkbootstrap.dialogs import Messagebox
    import tkinter as tk
    from tkinter import filedialog
    HAS_TTKBOOTSTRAP = True
    HAS_GUI = True
except Exception:
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkinter import messagebox as Messagebox
        from tkinter import filedialog
        tb = tk
        HAS_GUI = True
    except Exception:
        HAS_GUI = False


# =====================================================================
# FFmpeg Resolution & Metadata Extraction
# =====================================================================

def get_ffmpeg_path() -> str:
    """Locates a working ffmpeg binary on the system."""
    # 1. System PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg and os.path.isfile(system_ffmpeg):
        return system_ffmpeg

    # 2. imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    # 3. Check alongside script or current working directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "ffmpeg.exe"),
        os.path.join(base_dir, "bin", "ffmpeg.exe"),
        os.path.join(os.getcwd(), "ffmpeg.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    # Fallback to plain "ffmpeg"
    return "ffmpeg"


def get_video_info(file_path: str, ffmpeg_exe: str = None) -> Dict[str, Any]:
    """
    Extracts video duration, width, height, fps, bitrate, and size.
    Uses OpenCV first if available, with FFmpeg probe fallback.
    """
    info = {
        "file_path": file_path,
        "file_name": os.path.basename(file_path),
        "file_size": 0,
        "file_size_formatted": "0 MB",
        "duration": 0.0,
        "duration_formatted": "00:00",
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "resolution_str": "Unknown",
        "bitrate_kbps": 0,
    }

    if not os.path.isfile(file_path):
        return info

    size_bytes = os.path.getsize(file_path)
    info["file_size"] = size_bytes
    info["file_size_formatted"] = format_size(size_bytes)

    # Attempt OpenCV probe
    try:
        import cv2
        cap = cv2.VideoCapture(file_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            cap.release()

            if fps > 0 and frame_count > 0:
                duration = frame_count / fps
                info["duration"] = duration
                info["fps"] = round(fps, 2)
                info["width"] = w
                info["height"] = h
                info["resolution_str"] = f"{w}x{h}"
                info["duration_formatted"] = format_duration(duration)
                if duration > 0:
                    info["bitrate_kbps"] = int((size_bytes * 8) / (duration * 1000))
                return info
    except Exception:
        pass

    # Fallback: probe using FFmpeg stderr output
    try:
        ffmpeg_bin = ffmpeg_exe or get_ffmpeg_path()
        cmd = [ffmpeg_bin, "-i", file_path]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        output = proc.stderr

        for line in output.splitlines():
            line = line.strip()
            # Parse Duration: 00:01:23.45, start: ..., bitrate: 1234 kb/s
            if "Duration:" in line:
                try:
                    dur_str = line.split("Duration:")[1].split(",")[0].strip()
                    parts = dur_str.split(":")
                    if len(parts) == 3:
                        h = float(parts[0])
                        m = float(parts[1])
                        s = float(parts[2])
                        duration = h * 3600 + m * 60 + s
                        info["duration"] = duration
                        info["duration_formatted"] = format_duration(duration)
                        if duration > 0:
                            info["bitrate_kbps"] = int((size_bytes * 8) / (duration * 1000))
                except Exception:
                    pass

            # Parse Video stream resolution and fps
            if "Video:" in line and ("fps" in line or "tbr" in line):
                try:
                    import re
                    match = re.search(r"(\d{3,5})x(\d{3,5})", line)
                    if match:
                        info["width"] = int(match.group(1))
                        info["height"] = int(match.group(2))
                        info["resolution_str"] = f"{info['width']}x{info['height']}"
                    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
                    if fps_match:
                        info["fps"] = float(fps_match.group(1))
                except Exception:
                    pass
    except Exception:
        pass

    return info


# =====================================================================
# Formatters & Helpers
# =====================================================================

def format_size(size_bytes: int) -> str:
    """Formats bytes into human readable string (KB, MB, GB)."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = min(int(math.log(size_bytes, 1024)), len(units) - 1)
    scaled = size_bytes / (1024 ** idx)
    return f"{scaled:.2f} {units[idx]}"


def format_duration(seconds: float) -> str:
    """Formats seconds into HH:MM:SS or MM:SS."""
    seconds = max(0, seconds)
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_time_str(time_str: str) -> float:
    """Converts HH:MM:SS.ms string to seconds."""
    try:
        parts = time_str.strip().split(":")
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0


# =====================================================================
# Video Compressor Engine
# =====================================================================

class VideoCompressor:
    """Handles single and batch video compression execution with real-time progress."""

    def __init__(self, ffmpeg_exe: Optional[str] = None):
        self.ffmpeg_exe = ffmpeg_exe or get_ffmpeg_path()
        self._current_proc: Optional[subprocess.Popen] = None
        self._is_cancelled = False

    def cancel(self):
        """Cancels the running compression subprocess gracefully."""
        self._is_cancelled = True
        if self._current_proc and self._current_proc.poll() is None:
            try:
                self._current_proc.terminate()
            except Exception:
                pass

    def detect_hw_acceleration(self) -> Dict[str, bool]:
        """Detects whether hardware encoders (nvenc, qsv, amf) are supported."""
        support = {"nvenc": False, "qsv": False, "amf": False}
        try:
            res = subprocess.run([self.ffmpeg_exe, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
            txt = res.stdout.lower()
            support["nvenc"] = "h264_nvenc" in txt
            support["qsv"] = "h264_qsv" in txt
            support["amf"] = "h264_amf" in txt
        except Exception:
            pass
        return support

    def build_scale_filter(self, scale_opt: str, original_w: int = 0, original_h: int = 0) -> Optional[str]:
        """Returns ffmpeg video filter string for scaling."""
        if not scale_opt or scale_opt == "Original":
            return None
        
        # Percentage scaling
        if scale_opt == "75%":
            return "scale=trunc(iw*0.75/2)*2:trunc(ih*0.75/2)*2"
        if scale_opt == "50%":
            return "scale=trunc(iw*0.5/2)*2:trunc(ih*0.5/2)*2"
        if scale_opt == "33%":
            return "scale=trunc(iw*0.33/2)*2:trunc(ih*0.33/2)*2"

        # Fixed resolutions while preserving aspect ratio
        targets = {
            "1080p": 1080,
            "720p": 720,
            "480p": 480,
            "360p": 360,
        }
        if scale_opt in targets:
            target_h = targets[scale_opt]
            # scale=-2:target_h maintains aspect ratio and ensures width is even
            return f"scale=-2:{target_h}"

        return None

    def compress(
        self,
        input_path: str,
        output_path: str,
        mode: str = "crf",            # 'crf' or 'target_size'
        crf: int = 28,                # 18 (near lossless) to 40 (heavy compression)
        target_size_mb: float = 25.0, # Target size in megabytes
        codec: str = "h264",          # 'h264', 'h265', 'auto'
        scale: str = "Original",      # 'Original', '1080p', '720p', '480p', '360p', '50%', '75%'
        audio_mode: str = "128k",     # '128k', '96k', '64k', 'copy', 'mute'
        preset: str = "medium",       # ultrafast, veryfast, fast, medium, slow
        hw_accel: bool = False,       # True to try hardware encoding
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        """
        Compresses a video file with progress updates.
        Returns True on success, False otherwise.
        """
        self._is_cancelled = False

        if not os.path.isfile(input_path):
            raise FileNotFoundError(f"Input file not found: {input_path}")

        info = get_video_info(input_path, self.ffmpeg_exe)
        duration = info["duration"]
        if duration <= 0:
            duration = 1.0  # Fallback duration to avoid division by zero

        # Video filter construction
        vf_filters = []
        scale_filter = self.build_scale_filter(scale, info["width"], info["height"])
        if scale_filter:
            vf_filters.append(scale_filter)

        # Always ensure even dimensions for yuv420p compatibility
        if not scale_filter:
            vf_filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

        # Choose encoder
        video_encoder = "libx264"
        if codec == "h265":
            video_encoder = "libx265"

        if hw_accel:
            hw_info = self.detect_hw_acceleration()
            if hw_info["nvenc"]:
                video_encoder = "hevc_nvenc" if codec == "h265" else "h264_nvenc"
            elif hw_info["qsv"]:
                video_encoder = "hevc_qsv" if codec == "h265" else "h264_qsv"
            elif hw_info["amf"]:
                video_encoder = "hevc_amf" if codec == "h265" else "h264_amf"

        cmd = [self.ffmpeg_exe, "-y", "-i", input_path]

        # Mode calculation
        if mode == "target_size":
            # Calculate target video bitrate = (target_bytes * 8) / duration - audio_bitrate
            target_bytes = target_size_mb * 1024 * 1024
            target_total_bits = target_bytes * 8
            total_bitrate = target_total_bits / duration  # bps

            # Reserve audio bitrate
            audio_bitrate_bps = 128 * 1000
            if audio_mode == "mute":
                audio_bitrate_bps = 0
            elif audio_mode == "64k":
                audio_bitrate_bps = 64 * 1000
            elif audio_mode == "96k":
                audio_bitrate_bps = 96 * 1000
            elif audio_mode == "128k":
                audio_bitrate_bps = 128 * 1000

            video_bitrate_kbps = int((total_bitrate - audio_bitrate_bps) / 1000)
            if video_bitrate_kbps < 50:
                video_bitrate_kbps = 50  # Minimum floor

            cmd.extend([
                "-c:v", video_encoder,
                "-b:v", f"{video_bitrate_kbps}k",
                "-maxrate", f"{int(video_bitrate_kbps * 1.4)}k",
                "-bufsize", f"{int(video_bitrate_kbps * 2)}k",
            ])
        else:
            # CRF mode
            cmd.extend([
                "-c:v", video_encoder,
                "-crf", str(crf),
            ])

        # Preset
        if "nvenc" not in video_encoder:
            cmd.extend(["-preset", preset])

        # Pixel format
        cmd.extend(["-pix_fmt", "yuv420p"])

        # Video filters
        if vf_filters:
            cmd.extend(["-vf", ",".join(vf_filters)])

        # Audio configuration
        if audio_mode == "mute":
            cmd.append("-an")
        elif audio_mode == "copy":
            cmd.extend(["-c:a", "copy"])
        else:
            audio_k = audio_mode if "k" in audio_mode else "128k"
            cmd.extend(["-c:a", "aac", "-b:a", audio_k])

        # Faststart for web streaming / mobile compatibility
        cmd.extend(["-movflags", "+faststart"])

        # Progress reporting via pipe:1
        cmd.extend(["-progress", "pipe:1", output_path])

        # Create output directory if needed
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        # Run FFmpeg process
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            self._current_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                startupinfo=startupinfo,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch FFmpeg: {e}")

        # Drain stderr in a background thread to prevent pipe buffer deadlock
        stderr_buffer = []
        def _drain_stderr(pipe):
            try:
                for err_line in iter(pipe.readline, ""):
                    stderr_buffer.append(err_line)
                    if len(stderr_buffer) > 60:
                        stderr_buffer.pop(0)
            except Exception:
                pass
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        stderr_thread = threading.Thread(target=_drain_stderr, args=(self._current_proc.stderr,), daemon=True)
        stderr_thread.start()

        # Monitor progress
        start_time = time.time()
        current_time_sec = 0.0

        if self._current_proc.stdout:
            for line in self._current_proc.stdout:
                if self._is_cancelled:
                    break

                line = line.strip()
                if not line or "=" not in line:
                    continue

                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()

                if key == "out_time":
                    current_time_sec = parse_time_str(val)
                elif key == "out_time_us":
                    try:
                        current_time_sec = float(val) / 1000000.0
                    except Exception:
                        pass
                elif key == "progress":
                    pct = min(100.0, max(0.0, (current_time_sec / duration) * 100.0))
                    elapsed = time.time() - start_time
                    speed = (current_time_sec / elapsed) if elapsed > 0 else 1.0
                    eta_sec = ((duration - current_time_sec) / speed) if speed > 0 else 0.0

                    if progress_callback:
                        progress_callback({
                            "percentage": pct,
                            "current_time": current_time_sec,
                            "total_time": duration,
                            "elapsed": elapsed,
                            "eta": max(0.0, eta_sec),
                            "speed": speed,
                            "status": "compressing" if val != "end" else "done"
                        })

        self._current_proc.wait()
        return_code = self._current_proc.returncode
        self._current_proc = None
        stderr_thread.join(timeout=1.0)

        if self._is_cancelled:
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            return False

        if return_code != 0:
            stderr_out = "".join(stderr_buffer)
            raise RuntimeError(f"Compression failed with exit code {return_code}: {stderr_out}")

        return os.path.isfile(output_path) and os.path.getsize(output_path) > 0


# =====================================================================
# Graphical User Interface (GUI)
# =====================================================================

class VideoCompressorGUI:
    """Modern Tkinter / ttkbootstrap user interface."""

    PRESETS = {
        "Balanced (Recommended)": {"mode": "crf", "crf": 28, "scale": "Original", "codec": "h264", "audio": "128k"},
        "WhatsApp / Small File (16 MB)": {"mode": "target_size", "target_mb": 15.5, "scale": "720p", "codec": "h264", "audio": "96k"},
        "Discord Free (25 MB)": {"mode": "target_size", "target_mb": 24.5, "scale": "Original", "codec": "h264", "audio": "128k"},
        "High Quality (CRF 22)": {"mode": "crf", "crf": 22, "scale": "Original", "codec": "h264", "audio": "128k"},
        "Maximum Space Saver (CRF 34)": {"mode": "crf", "crf": 34, "scale": "480p", "codec": "h265", "audio": "64k"},
    }

    def __init__(self):
        if not HAS_GUI:
            raise RuntimeError("GUI is not supported in this headless/serverless environment.")
        # Create main window
        if HAS_TTKBOOTSTRAP:
            self.root = tb.Window(title="GT INNOX Video Compressor", themename="darkly")
        else:
            self.root = tk.Tk()
            self.root.title("GT INNOX Video Compressor")

        self.root.geometry("860x780")
        self.root.minsize(760, 680)

        # State
        self.compressor = VideoCompressor()
        self.file_queue: List[Dict[str, Any]] = []
        self.current_worker: Optional[threading.Thread] = None
        self.is_processing = False

        # Build UI widgets
        self._build_ui()

    def _build_ui(self):
        # Top Header Bar
        header_frame = tb.Frame(self.root, padding=(20, 15, 20, 10))
        header_frame.pack(fill=X)

        title_label = tb.Label(
            header_frame,
            text="⚡ GT INNOX Video Compressor",
            font=("Segoe UI", 18, "bold"),
            bootstyle="primary" if HAS_TTKBOOTSTRAP else None
        )
        title_label.pack(side=LEFT)

        subtitle_label = tb.Label(
            header_frame,
            text="Next-Gen Media Engine by GT INNOX",
            font=("Segoe UI", 10),
            bootstyle="secondary" if HAS_TTKBOOTSTRAP else None
        )
        subtitle_label.pack(side=LEFT, padx=(12, 0), pady=(6, 0))

        # Main Scrollable / Stacked Container
        main_container = tb.Frame(self.root, padding=(20, 0, 20, 15))
        main_container.pack(fill=BOTH, expand=True)

        # 1. FILE LIST CARD
        files_card = tb.Labelframe(main_container, text=" Selected Video Queue ", padding=12, bootstyle="info" if HAS_TTKBOOTSTRAP else None)
        files_card.pack(fill=BOTH, expand=True, pady=(5, 10))

        # Treeview for selected files
        tree_scroll = tb.Scrollbar(files_card)
        tree_scroll.pack(side=RIGHT, fill=Y)

        columns = ("name", "duration", "res", "orig_size", "status")
        self.tree = tb.Treeview(
            files_card,
            columns=columns,
            show="headings",
            height=5,
            yscrollcommand=tree_scroll.set,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        tree_scroll.config(command=self.tree.yview)

        self.tree.heading("name", text="File Name")
        self.tree.heading("duration", text="Duration")
        self.tree.heading("res", text="Resolution")
        self.tree.heading("orig_size", text="Original Size")
        self.tree.heading("status", text="Status")

        self.tree.column("name", width=280)
        self.tree.column("duration", width=85, anchor=CENTER)
        self.tree.column("res", width=95, anchor=CENTER)
        self.tree.column("orig_size", width=110, anchor=CENTER)
        self.tree.column("status", width=140, anchor=CENTER)
        self.tree.pack(fill=BOTH, expand=True)

        # Buttons under queue
        queue_btn_frame = tb.Frame(files_card, padding=(0, 8, 0, 0))
        queue_btn_frame.pack(fill=X)

        add_btn = tb.Button(
            queue_btn_frame,
            text="➕ Add Video(s)",
            command=self.on_add_files,
            bootstyle="primary-outline" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        add_btn.pack(side=LEFT, padx=(0, 6))

        clear_btn = tb.Button(
            queue_btn_frame,
            text="🗑 Clear Queue",
            command=self.on_clear_queue,
            bootstyle="secondary-outline" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        clear_btn.pack(side=LEFT)

        self.queue_count_lbl = tb.Label(queue_btn_frame, text="0 videos loaded", font=("Segoe UI", 9))
        self.queue_count_lbl.pack(side=RIGHT)

        # 2. COMPRESSION SETTINGS CARD
        settings_card = tb.Labelframe(main_container, text=" Compression Settings ", padding=12, bootstyle="secondary" if HAS_TTKBOOTSTRAP else None)
        settings_card.pack(fill=X, pady=(0, 10))

        # Quick preset row
        preset_row = tb.Frame(settings_card)
        preset_row.pack(fill=X, pady=(0, 10))

        tb.Label(preset_row, text="Quick Preset:", font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(0, 10))
        self.preset_var = tk.StringVar(value="Balanced (Recommended)")
        preset_combo = tb.Combobox(
            preset_row,
            textvariable=self.preset_var,
            values=list(self.PRESETS.keys()),
            state="readonly",
            width=28,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        preset_combo.pack(side=LEFT)
        preset_combo.bind("<<ComboboxSelected>>", self.on_preset_changed)

        # Compression Mode Selection (Radio buttons)
        mode_row = tb.Frame(settings_card)
        mode_row.pack(fill=X, pady=(0, 10))

        self.mode_var = tk.StringVar(value="crf")
        tb.Label(mode_row, text="Mode:", font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(0, 10))
        
        crf_radio = tb.Radiobutton(
            mode_row,
            text="Constant Quality (CRF)",
            variable=self.mode_var,
            value="crf",
            command=self.update_mode_visibility,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        crf_radio.pack(side=LEFT, padx=(0, 20))

        size_radio = tb.Radiobutton(
            mode_row,
            text="Target File Size (MB)",
            variable=self.mode_var,
            value="target_size",
            command=self.update_mode_visibility,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        size_radio.pack(side=LEFT)

        # Mode Dynamic Sliders Frame
        self.slider_frame = tb.Frame(settings_card)
        self.slider_frame.pack(fill=X, pady=(0, 10))

        # CRF Controls
        self.crf_subframe = tb.Frame(self.slider_frame)
        tb.Label(self.crf_subframe, text="CRF Quality (Lower = Better Quality, Higher = Smaller File):", font=("Segoe UI", 9)).pack(anchor=W)
        crf_ctrl_row = tb.Frame(self.crf_subframe)
        crf_ctrl_row.pack(fill=X, pady=(3, 0))
        self.crf_var = tk.IntVar(value=28)
        self.crf_scale = tb.Scale(
            crf_ctrl_row,
            from_=18,
            to=42,
            variable=self.crf_var,
            command=self.on_crf_slider_move,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        self.crf_scale.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.crf_lbl = tb.Label(crf_ctrl_row, text="28 (Balanced)", width=16, font=("Segoe UI", 9, "bold"))
        self.crf_lbl.pack(side=RIGHT)

        # Target Size Controls
        self.target_subframe = tb.Frame(self.slider_frame)
        tb.Label(self.target_subframe, text="Target Output Size (Megabytes):", font=("Segoe UI", 9)).pack(anchor=W)
        target_ctrl_row = tb.Frame(self.target_subframe)
        target_ctrl_row.pack(fill=X, pady=(3, 0))
        self.target_mb_var = tk.DoubleVar(value=24.5)
        self.target_scale = tb.Scale(
            target_ctrl_row,
            from_=2.0,
            to=150.0,
            variable=self.target_mb_var,
            command=self.on_target_slider_move,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        self.target_scale.pack(side=LEFT, fill=X, expand=True, padx=(0, 10))
        self.target_lbl = tb.Label(target_ctrl_row, text="24.5 MB", width=16, font=("Segoe UI", 9, "bold"))
        self.target_lbl.pack(side=RIGHT)

        # Initially show CRF
        self.crf_subframe.pack(fill=X)

        # Advanced Settings Grid
        adv_grid = tb.Frame(settings_card)
        adv_grid.pack(fill=X, pady=(5, 0))

        # Resolution dropdown
        tb.Label(adv_grid, text="Resolution:").grid(row=0, column=0, sticky=W, padx=(0, 6), pady=4)
        self.scale_var = tk.StringVar(value="Original")
        scale_combo = tb.Combobox(
            adv_grid,
            textvariable=self.scale_var,
            values=["Original", "1080p", "720p", "480p", "360p", "75%", "50%"],
            state="readonly",
            width=12,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        scale_combo.grid(row=0, column=1, sticky=W, padx=(0, 20), pady=4)

        # Codec dropdown
        tb.Label(adv_grid, text="Codec:").grid(row=0, column=2, sticky=W, padx=(0, 6), pady=4)
        self.codec_var = tk.StringVar(value="h264")
        codec_combo = tb.Combobox(
            adv_grid,
            textvariable=self.codec_var,
            values=["h264", "h265"],
            state="readonly",
            width=10,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        codec_combo.grid(row=0, column=3, sticky=W, padx=(0, 20), pady=4)

        # Audio dropdown
        tb.Label(adv_grid, text="Audio:").grid(row=0, column=4, sticky=W, padx=(0, 6), pady=4)
        self.audio_var = tk.StringVar(value="128k")
        audio_combo = tb.Combobox(
            adv_grid,
            textvariable=self.audio_var,
            values=["128k", "96k", "64k", "copy", "mute"],
            state="readonly",
            width=8,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        audio_combo.grid(row=0, column=5, sticky=W, pady=4)

        # Hardware Acceleration & Preset Speed Row
        row2 = tb.Frame(settings_card)
        row2.pack(fill=X, pady=(6, 0))

        self.hw_accel_var = tk.BooleanVar(value=False)
        hw_check = tb.Checkbutton(
            row2,
            text="⚡ Use Hardware Acceleration (GPU - NVENC/QSV/AMF)",
            variable=self.hw_accel_var,
            bootstyle="success-round-toggle" if HAS_TTKBOOTSTRAP else None
        )
        hw_check.pack(side=LEFT)

        tb.Label(row2, text="Speed Preset:").pack(side=RIGHT, padx=(0, 6))
        self.preset_speed_var = tk.StringVar(value="medium")
        preset_speed_combo = tb.Combobox(
            row2,
            textvariable=self.preset_speed_var,
            values=["ultrafast", "superfast", "veryfast", "fast", "medium", "slow"],
            state="readonly",
            width=10,
            bootstyle="info" if HAS_TTKBOOTSTRAP else None
        )
        preset_speed_combo.pack(side=RIGHT, padx=(0, 15))

        # 3. OUTPUT DESTINATION CARD
        out_frame = tb.Frame(main_container)
        out_frame.pack(fill=X, pady=(0, 10))

        tb.Label(out_frame, text="Output Directory:", font=("Segoe UI", 9, "bold")).pack(side=LEFT, padx=(0, 8))
        self.out_dir_var = tk.StringVar(value="")
        out_entry = tb.Entry(out_frame, textvariable=self.out_dir_var, bootstyle="info" if HAS_TTKBOOTSTRAP else None)
        out_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 8))

        browse_out_btn = tb.Button(
            out_frame,
            text="📁 Browse",
            command=self.on_browse_output_dir,
            bootstyle="secondary-outline" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        browse_out_btn.pack(side=RIGHT)

        # 4. PROGRESS & ACTIONS CARD
        action_card = tb.Labelframe(main_container, text=" Progress & Actions ", padding=12, bootstyle="primary" if HAS_TTKBOOTSTRAP else None)
        action_card.pack(fill=X)

        self.progress_bar = tb.Progressbar(action_card, mode="determinate", bootstyle="success-striped" if HAS_TTKBOOTSTRAP else None)
        self.progress_bar.pack(fill=X, pady=(0, 6))

        info_status_row = tb.Frame(action_card)
        info_status_row.pack(fill=X, pady=(0, 8))

        self.status_lbl = tb.Label(info_status_row, text="Ready", font=("Segoe UI", 9))
        self.status_lbl.pack(side=LEFT)

        self.eta_lbl = tb.Label(info_status_row, text="", font=("Segoe UI", 9, "bold"))
        self.eta_lbl.pack(side=RIGHT)

        btn_row = tb.Frame(action_card)
        btn_row.pack(fill=X)

        self.start_btn = tb.Button(
            btn_row,
            text="🚀 Start Compression",
            command=self.on_start_compression,
            bootstyle="success" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        self.start_btn.pack(side=LEFT, padx=(0, 8))

        self.cancel_btn = tb.Button(
            btn_row,
            text="⏹ Cancel",
            command=self.on_cancel_compression,
            state=DISABLED,
            bootstyle="danger-outline" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        self.cancel_btn.pack(side=LEFT, padx=(0, 8))

        self.open_dir_btn = tb.Button(
            btn_row,
            text="📂 Open Output Folder",
            command=self.on_open_output_folder,
            bootstyle="info-outline" if HAS_TTKBOOTSTRAP else None,
            cursor="hand2"
        )
        self.open_dir_btn.pack(side=RIGHT)

    # ================= UI Callbacks =================

    def update_mode_visibility(self):
        mode = self.mode_var.get()
        if mode == "crf":
            self.target_subframe.pack_forget()
            self.crf_subframe.pack(fill=X)
        else:
            self.crf_subframe.pack_forget()
            self.target_subframe.pack(fill=X)

    def on_crf_slider_move(self, val):
        crf_val = int(float(val))
        desc = "Balanced"
        if crf_val <= 20:
            desc = "Near Lossless"
        elif crf_val <= 24:
            desc = "High Quality"
        elif crf_val <= 30:
            desc = "Balanced"
        elif crf_val <= 36:
            desc = "Small Size"
        else:
            desc = "Extreme"
        self.crf_lbl.config(text=f"{crf_val} ({desc})")

    def on_target_slider_move(self, val):
        mb_val = round(float(val), 1)
        self.target_lbl.config(text=f"{mb_val} MB")

    def on_preset_changed(self, event=None):
        name = self.preset_var.get()
        preset = self.PRESETS.get(name)
        if not preset:
            return

        self.mode_var.set(preset["mode"])
        self.update_mode_visibility()

        if preset["mode"] == "crf":
            self.crf_var.set(preset.get("crf", 28))
            self.on_crf_slider_move(self.crf_var.get())
        else:
            self.target_mb_var.set(preset.get("target_mb", 24.5))
            self.on_target_slider_move(self.target_mb_var.get())

        self.scale_var.set(preset.get("scale", "Original"))
        self.codec_var.set(preset.get("codec", "h264"))
        self.audio_var.set(preset.get("audio", "128k"))

    def on_add_files(self):
        files = filedialog.askopenfilenames(
            title="Select Video Files to Compress",
            filetypes=[
                ("Video Files", "*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm *.m4v *.ts"),
                ("All Files", "*.*")
            ]
        )
        if not files:
            return

        for f in files:
            norm_path = os.path.normpath(f)
            # Avoid duplicate entries
            if any(item["file_path"] == norm_path for item in self.file_queue):
                continue

            info = get_video_info(norm_path, self.compressor.ffmpeg_exe)
            self.file_queue.append(info)

            # Insert into Treeview
            item_id = self.tree.insert(
                "",
                END,
                values=(
                    info["file_name"],
                    info["duration_formatted"],
                    info["resolution_str"],
                    info["file_size_formatted"],
                    "Pending"
                )
            )
            info["tree_id"] = item_id

            # Set default output directory to same folder as first file if empty
            if not self.out_dir_var.get():
                self.out_dir_var.set(os.path.dirname(norm_path))

        self.queue_count_lbl.config(text=f"{len(self.file_queue)} video(s) loaded")

    def on_clear_queue(self):
        if self.is_processing:
            Messagebox.show_warning("Cannot clear queue while compression is active.", title="Compression Active")
            return
        self.file_queue.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.queue_count_lbl.config(text="0 videos loaded")
        self.status_lbl.config(text="Ready")
        self.eta_lbl.config(text="")
        self.progress_bar["value"] = 0

    def on_browse_output_dir(self):
        chosen = filedialog.askdirectory(title="Select Destination Directory")
        if chosen:
            self.out_dir_var.set(os.path.normpath(chosen))

    def on_open_output_folder(self):
        target_dir = self.out_dir_var.get()
        if not target_dir or not os.path.isdir(target_dir):
            if self.file_queue:
                target_dir = os.path.dirname(self.file_queue[0]["file_path"])
            else:
                target_dir = os.getcwd()

        if sys.platform == "win32":
            os.startfile(target_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", target_dir])
        else:
            subprocess.run(["xdg-open", target_dir])

    def on_start_compression(self):
        if not self.file_queue:
            Messagebox.show_info("Please add at least one video to the queue first.", title="Empty Queue")
            return

        if self.is_processing:
            return

        out_dir = self.out_dir_var.get().strip()
        if not out_dir:
            out_dir = os.path.dirname(self.file_queue[0]["file_path"])
            self.out_dir_var.set(out_dir)

        self.is_processing = True
        self.start_btn.config(state=DISABLED)
        self.cancel_btn.config(state=NORMAL)

        # Launch compression in background thread so GUI remains fluid
        self.current_worker = threading.Thread(target=self._run_compression_thread, daemon=True)
        self.current_worker.start()

    def on_cancel_compression(self):
        if not self.is_processing:
            return
        self.compressor.cancel()
        self.status_lbl.config(text="Cancelling compression...")

    def _run_compression_thread(self):
        mode = self.mode_var.get()
        crf = int(self.crf_var.get())
        target_mb = float(self.target_mb_var.get())
        scale = self.scale_var.get()
        codec = self.codec_var.get()
        audio = self.audio_var.get()
        preset = self.preset_speed_var.get()
        hw_accel = self.hw_accel_var.get()
        out_dir = self.out_dir_var.get().strip()

        total_files = len(self.file_queue)
        completed_count = 0
        total_orig_bytes = 0
        total_compressed_bytes = 0

        for idx, item in enumerate(self.file_queue):
            if not self.is_processing or self.compressor._is_cancelled:
                break

            in_path = item["file_path"]
            tree_id = item.get("tree_id")

            # Update Treeview to 'Compressing...'
            if tree_id:
                self.root.after(0, lambda tid=tree_id: self.tree.item(tid, values=(
                    item["file_name"], item["duration_formatted"], item["resolution_str"], item["file_size_formatted"], "Compressing..."
                )))

            base_name, _ = os.path.splitext(item["file_name"])
            out_name = f"{base_name}_compressed.mp4"
            out_path = os.path.join(out_dir, out_name)

            # Avoid overwriting original
            if os.path.abspath(in_path) == os.path.abspath(out_path):
                out_path = os.path.join(out_dir, f"{base_name}_comp.mp4")

            def on_progress(p_info):
                pct = p_info["percentage"]
                speed = p_info["speed"]
                eta = p_info["eta"]
                self.root.after(0, lambda: self._update_progress_ui(idx + 1, total_files, pct, speed, eta))

            try:
                success = self.compressor.compress(
                    input_path=in_path,
                    output_path=out_path,
                    mode=mode,
                    crf=crf,
                    target_size_mb=target_mb,
                    codec=codec,
                    scale=scale,
                    audio_mode=audio,
                    preset=preset,
                    hw_accel=hw_accel,
                    progress_callback=on_progress
                )
            except Exception as e:
                success = False
                err_msg = str(e)
                print(f"Error compressing {in_path}: {err_msg}")

            if success and os.path.exists(out_path):
                completed_count += 1
                orig_sz = item["file_size"]
                comp_sz = os.path.getsize(out_path)
                total_orig_bytes += orig_sz
                total_compressed_bytes += comp_sz
                pct_saved = ((orig_sz - comp_sz) / orig_sz * 100) if orig_sz > 0 else 0

                status_text = f"Saved {pct_saved:.1f}% ({format_size(comp_sz)})"
                if tree_id:
                    self.root.after(0, lambda tid=tree_id, st=status_text: self.tree.item(tid, values=(
                        item["file_name"], item["duration_formatted"], item["resolution_str"], item["file_size_formatted"], st
                    )))
            else:
                status_text = "Cancelled" if self.compressor._is_cancelled else "Failed"
                if tree_id:
                    self.root.after(0, lambda tid=tree_id, st=status_text: self.tree.item(tid, values=(
                        item["file_name"], item["duration_formatted"], item["resolution_str"], item["file_size_formatted"], st
                    )))

        # Final cleanup on UI thread
        self.root.after(0, lambda: self._on_batch_complete(completed_count, total_files, total_orig_bytes, total_compressed_bytes))

    def _update_progress_ui(self, current_file_idx: int, total_files: int, pct: float, speed: float, eta_sec: float):
        self.progress_bar["value"] = pct
        file_prefix = f"[{current_file_idx}/{total_files}]" if total_files > 1 else ""
        self.status_lbl.config(text=f"{file_prefix} Processing: {pct:.1f}% | Speed: {speed:.2f}x")
        self.eta_lbl.config(text=f"ETA: {format_duration(eta_sec)}")

    def _on_batch_complete(self, completed: int, total: int, orig_bytes: int, comp_bytes: int):
        self.is_processing = False
        self.start_btn.config(state=NORMAL)
        self.cancel_btn.config(state=DISABLED)
        self.progress_bar["value"] = 100 if completed == total else 0

        if self.compressor._is_cancelled:
            self.status_lbl.config(text="Compression cancelled by user.")
            self.eta_lbl.config(text="")
            return

        if completed > 0:
            saved_bytes = max(0, orig_bytes - comp_bytes)
            pct_saved = (saved_bytes / orig_bytes * 100) if orig_bytes > 0 else 0
            self.status_lbl.config(text=f"✅ Completed {completed}/{total} videos! Reduced by {pct_saved:.1f}% ({format_size(saved_bytes)} saved).")
            self.eta_lbl.config(text="Done 🎉")

            Messagebox.show_info(
                f"Compression finished successfully!\n\n"
                f"• Processed: {completed} file(s)\n"
                f"• Original Size: {format_size(orig_bytes)}\n"
                f"• Compressed Size: {format_size(comp_bytes)}\n"
                f"• Total Saved: {format_size(saved_bytes)} ({pct_saved:.1f}% reduction)\n\n"
                f"Output Folder: {self.out_dir_var.get()}",
                title="Compression Complete"
            )
        else:
            self.status_lbl.config(text="⚠️ No videos were compressed.")
            self.eta_lbl.config(text="")

    def run(self):
        """Starts GUI event loop."""
        self.root.mainloop()


# =====================================================================
# Command Line Interface (CLI)
# =====================================================================

def run_cli(args: argparse.Namespace):
    """Executes compression from the command line."""
    compressor = VideoCompressor()
    hw_detected = compressor.detect_hw_acceleration()

    print("=" * 65)
    print("  ⚡ GT INNOX Video Compressor (CLI)")
    print("=" * 65)
    print(f"  * FFmpeg binary : {compressor.ffmpeg_exe}")
    print(f"  * Hardware Accel: NVENC={hw_detected['nvenc']}, QSV={hw_detected['qsv']}, AMF={hw_detected['amf']}")
    print("-" * 65)

    input_files = args.inputs
    if not input_files:
        print("Error: No input files specified.")
        sys.exit(1)

    out_dir = args.out_dir or os.getcwd()
    os.makedirs(out_dir, exist_ok=True)

    mode = "target_size" if args.target else "crf"

    for idx, in_file in enumerate(input_files, 1):
        if not os.path.isfile(in_file):
            print(f"[{idx}/{len(input_files)}] Skipping missing file: {in_file}")
            continue

        info = get_video_info(in_file, compressor.ffmpeg_exe)
        print(f"\n[{idx}/{len(input_files)}] Processing: {info['file_name']}")
        print(f"   Original Size: {info['file_size_formatted']} | Duration: {info['duration_formatted']} | Res: {info['resolution_str']}")

        # Determine output filename
        if args.output and len(input_files) == 1:
            out_file = args.output
        else:
            base, _ = os.path.splitext(os.path.basename(in_file))
            out_file = os.path.join(out_dir, f"{base}_compressed.mp4")

        # Progress printer
        last_pct = [-1]

        def cli_progress(p_info):
            pct = int(p_info["percentage"])
            if pct != last_pct[0] and pct % 5 == 0:
                last_pct[0] = pct
                bar = "#" * (pct // 5) + "-" * (20 - (pct // 5))
                sys.stdout.write(f"\r   [{bar}] {pct:3d}% | Speed: {p_info['speed']:.2f}x | ETA: {format_duration(p_info['eta'])}   ")
                sys.stdout.flush()

        try:
            success = compressor.compress(
                input_path=in_file,
                output_path=out_file,
                mode=mode,
                crf=args.crf,
                target_size_mb=args.target or 25.0,
                codec=args.codec,
                scale=args.scale,
                audio_mode=args.audio,
                preset=args.preset,
                hw_accel=args.hw,
                progress_callback=cli_progress,
            )
            print()  # Newline after progress bar

            if success and os.path.isfile(out_file):
                orig_sz = info["file_size"]
                comp_sz = os.path.getsize(out_file)
                saved_sz = max(0, orig_sz - comp_sz)
                pct_saved = (saved_sz / orig_sz * 100) if orig_sz > 0 else 0
                print(f"   [+] Success! Output: {out_file}")
                print(f"   Final Size: {format_size(comp_sz)} (Saved {pct_saved:.1f}% / {format_size(saved_sz)})")
            else:
                print(f"   [-] Compression failed for {in_file}")
        except KeyboardInterrupt:
            print("\nAborted by user.")
            compressor.cancel()
            break
        except Exception as e:
            print(f"\n   [-] Error: {e}")

    print("\n" + "=" * 65)
    print("Compression complete.")
    print("=" * 65)


# =====================================================================
# Main Entry Point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Antigravity Video Compressor - Compress video files using CRF or Target Size."
    )
    parser.add_argument("inputs", nargs="*", help="Input video file path(s). If omitted and no flags provided, launches GUI.")
    parser.add_argument("-o", "--output", help="Output file path (for single input).")
    parser.add_argument("-d", "--out-dir", help="Output directory for compressed videos.")
    parser.add_argument("-t", "--target", type=float, help="Target file size in MB (e.g. 25 for Discord, 16 for WhatsApp).")
    parser.add_argument("-c", "--crf", type=int, default=28, help="Constant Rate Factor (18-42, default: 28).")
    parser.add_argument("-s", "--scale", default="Original", choices=["Original", "1080p", "720p", "480p", "360p", "75%", "50%"], help="Resolution downscaling.")
    parser.add_argument("--codec", default="h264", choices=["h264", "h265"], help="Video codec (default: h264).")
    parser.add_argument("-a", "--audio", default="128k", choices=["128k", "96k", "64k", "copy", "mute"], help="Audio compression mode.")
    parser.add_argument("-p", "--preset", default="medium", choices=["ultrafast", "veryfast", "fast", "medium", "slow"], help="Encoding speed preset.")
    parser.add_argument("--hw", action="store_true", help="Enable hardware acceleration (NVENC/QSV/AMF).")
    parser.add_argument("--gui", action="store_true", help="Force launch the Graphical User Interface (desktop window).")
    parser.add_argument("--web", action="store_true", help="Launch the modern Web Studio Frontend in your browser.")

    args = parser.parse_args()

    # If --web is specified, start the web frontend
    if args.web:
        try:
            from web_app import start_server
            start_server()
        except ImportError as e:
            print(f"Error launching web frontend: {e}")
        return

    # If --gui is specified, or no arguments are passed, launch the GUI
    if args.gui or len(sys.argv) == 1:
        app = VideoCompressorGUI()
        app.run()
    else:
        run_cli(args)


if __name__ == "__main__":
    main()
