<p align="center">
  <img src="static/gt_innox_logo.svg" alt="GT INNOX Logo" width="480"/>
</p>

<h1 align="center">GT INNOX Studio</h1>

<p align="center">
  <b>Next-generation media & document processing studio by GT INNOX.</b><br>
  Compress 4K & 1080p videos with zero visible loss + autonomous Word ⇄ PDF converters.
</p>

<p align="center">
  <a href="https://vercel.com/new"><img src="https://vercel.com/button" alt="Deploy with Vercel"/></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License: MIT"/>
  <img src="https://img.shields.io/badge/Hardware%20Accel-NVENC%20%7C%20QSV%20%7C%20AMF-orange" alt="Hardware Acceleration"/>
  <img src="https://img.shields.io/badge/Platform-Web%20%7C%20Desktop%20%7C%20CLI-purple" alt="Multi-Platform"/>
</p>

---

## ⚡ Features

- **🌐 All-in-One Web Studio**: Responsive glassmorphic studio with drag-and-drop, real-time SSE progress, and segmented tool switcher.
- **📝 Word to PDF Converter**: Autonomous pure-Python converter transforming `.docx` into print-ready, high-fidelity PDFs with preserved tables, typography, and styles.
- **📄 PDF to Word Converter**: Smart extractor parsing PDF text, layout, images, and tables into fully editable Microsoft Word (`.docx`) documents.
- **🎥 High-Fidelity Video Compressor**:
  - **Dual Compression Modes**: Target File Size (e.g., Discord 25MB, WhatsApp 16MB) & Constant Quality (CRF 18–42).
  - **Side-by-Side & Segmented Comparison**: Instant visual diffing on mobile and desktop.
- **📱 100% Mobile Optimized**: Touch-friendly sliders, safe area insets, PWA meta tags, and native Web Share API (`navigator.share`) support to save directly to Photos/Files.
- **🚀 Hardware Acceleration**: Auto-detects NVIDIA NVENC, Intel QSV, and AMD AMF with seamless CPU fallback.
- **📦 Zero External Setup**: Autonomous execution with no Microsoft Office licenses or manual system binary configurations needed.

---

## 🚀 Quick Start

### 1. Web Studio (Browser)
```bash
python web_app.py
```
*or*
```bash
python video.py --web
```
Opens automatically at `http://127.0.0.1:8000`.

### 2. Desktop GUI (Native Window)
```bash
python video.py --gui
```

### 3. CLI Command Line
```bash
# Quick balanced compression
python video.py my_video.mp4

# Fit into target size (e.g. 25 MB Discord limit)
python video.py my_video.mp4 -t 25 -o compressed.mp4

# Custom CRF, 720p scaling, and compact audio
python video.py my_video.mp4 -c 26 -s 720p -a 96k

# Batch compress multiple files
python video.py *.mp4 -d ./compressed_videos/
```

---

## ☁️ Deploy to Vercel

This repository is pre-configured with `vercel.json` and `api/index.py` for instant Vercel Serverless deployment:

1. Push this repository to your **GitHub** account.
2. Go to [Vercel Dashboard](https://vercel.com/new).
3. Import your repository and click **Deploy**.
4. Vercel automatically detects the Python runtime and routes all traffic to the GT INNOX Studio.

---

## 🐙 Publishing to GitHub

```bash
# Initialize git repository
git init

# Add all files
git add .

# Initial commit
git commit -m "feat: initial release of GT INNOX Video Compressor Studio"

# Add your remote repository and push
git remote add origin https://github.com/your-username/gt-innox-video-compressor.git
git branch -M main
git push -u origin main
```

---

## 📁 Repository Structure

```
video_compressor/
├── api/
│   └── index.py            # Vercel Serverless Function entry point
├── static/
│   └── gt_innox_logo.svg   # High-resolution GT INNOX vector brand logo
├── video.py                # Core engine, Desktop GUI & CLI
├── web_app.py              # FastAPI server & embedded Web Studio
├── vercel.json             # Vercel deployment configuration
├── requirements.txt        # Python dependencies
├── .gitignore              # Production git ignore rules
├── LICENSE                 # MIT License (GT INNOX)
└── README.md               # Documentation & badges
```

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.

**Crafted with precision by [GT INNOX](https://github.com).**
