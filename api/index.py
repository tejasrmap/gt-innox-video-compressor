"""
Vercel Serverless Function entry point for GT INNOX Video Compressor.
"""

import sys
import os

# Add parent directory to sys.path so web_app and video can be imported
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from web_app import app

# Vercel's Python runtime expects 'app' as ASGI handler
handler = app

