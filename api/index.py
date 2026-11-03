"""
Vercel Serverless Function entry point for GT INNOX Studio.
"""

import sys
import os

# Add parent directory to sys.path so web_app and converter can be imported
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from web_app import app as _fastapi_app


class PathFixerMiddleware:
    """
    ASGI middleware ensuring seamless compatibility across Vercel Serverless
    Function rewrites, reverse proxies, and local uvicorn servers.
    """
    def __init__(self, inner_app):
        self.inner_app = inner_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            
            # 1. Recover original request path from Vercel Edge proxy headers
            matched_path = headers.get("x-matched-path") or headers.get("x-vercel-matched-path")
            if matched_path and not matched_path.endswith("/api/index.py") and not matched_path.endswith("/api/index"):
                clean = matched_path.split("?")[0]
                scope["path"] = clean
                scope["raw_path"] = clean.encode("latin1")
            
            # 2. Normalize /api/index.py/ or /api/index/ prefixed paths
            cur_path = scope.get("path", "")
            if cur_path.startswith("/api/index.py/"):
                normalized = "/api/" + cur_path[len("/api/index.py/"):]
                scope["path"] = normalized
                scope["raw_path"] = normalized.encode("latin1")
            elif cur_path.startswith("/api/index/"):
                normalized = "/api/" + cur_path[len("/api/index/"):]
                scope["path"] = normalized
                scope["raw_path"] = normalized.encode("latin1")
            
            # 3. Restore /api prefix if stripped by reverse proxy
            cur_path = scope.get("path", "")
            if not cur_path.startswith("/api"):
                prefixes = ("/pdf/", "/convert/", "/compress", "/upload", "/download", "/progress", "/cancel", "/stream", "/health")
                for p in prefixes:
                    if cur_path.startswith(p):
                        scope["path"] = "/api" + cur_path
                        scope["raw_path"] = scope["path"].encode("latin1")
                        break

        return await self.inner_app(scope, receive, send)


# Wrap FastAPI app for Vercel Python runtime
app = PathFixerMiddleware(_fastapi_app)
handler = app
