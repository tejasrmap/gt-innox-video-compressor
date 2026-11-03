"""
Vercel Serverless Function entry point for GT INNOX Studio.
"""

import sys
import os
from urllib.parse import parse_qs, urlencode

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
            # 1. Primary mechanism: Extract original path forwarded via Vercel rewrite parameter
            query_bytes = scope.get("query_string", b"")
            query_str = query_bytes.decode("latin1")
            if "__path__=" in query_str:
                params = parse_qs(query_str, keep_blank_values=True)
                if "__path__" in params:
                    real_path = params["__path__"][0]
                    scope["path"] = real_path
                    scope["raw_path"] = real_path.encode("latin1")
                    
                    # Clean up __path__ so downstream route handlers get clean query parameters
                    del params["__path__"]
                    new_qs = urlencode(params, doseq=True)
                    scope["query_string"] = new_qs.encode("latin1")
            
            # 2. Secondary fallback: Recover from Vercel edge proxy headers
            headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
            cur_path = scope.get("path", "")
            if cur_path in ("/api/index.py", "/api/index", "/api"):
                edge_path = (
                    headers.get("x-invoke-path") or
                    headers.get("x-forwarded-uri") or
                    headers.get("x-forwarded-url") or
                    headers.get("x-original-url") or
                    headers.get("x-rewrite-url")
                )
                if edge_path and not edge_path.endswith("/api/index.py"):
                    clean = edge_path.split("?")[0]
                    scope["path"] = clean
                    scope["raw_path"] = clean.encode("latin1")
            
            # 3. Tertiary fallback: Add /api prefix if stripped by reverse proxy
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
