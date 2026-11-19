"""
lambda_function.py — AWS Lambda handler (Python 3.12)
Serves student.html and guard.html via Lambda Function URL.
Injects EC2_API_URL from environment into the HTML at runtime.
"""

import os
import base64

# ── Environment ───────────────────────────────────────────────────────────────
EC2_API_URL = os.environ.get("EC2_API_URL", "http://localhost:5000")

# Cache HTML files in memory across warm invocations
_CACHE: dict[str, str] = {}


def _load_html(filename: str) -> str:
    """Read an HTML file from the same directory as this Lambda, with caching."""
    if filename not in _CACHE:
        path = os.path.join(os.path.dirname(__file__), filename)
        with open(path, "r", encoding="utf-8") as fh:
            _CACHE[filename] = fh.read()
    return _CACHE[filename]


def _html_response(html: str, status_code: int = 200) -> dict:
    """Return a Lambda Function URL compatible HTTP response."""
    return {
        "statusCode":      status_code,
        "headers":         {"Content-Type": "text/html; charset=utf-8"},
        "body":            html,
        "isBase64Encoded": False,
    }


def lambda_handler(event: dict, context) -> dict:
    """
    Route:
      /guard            → guard.html
      / or /student     → student.html
    EC2_API_URL placeholder is replaced before serving.
    """
    raw_path = event.get("rawPath", "/").rstrip("/") or "/"

    if raw_path == "/guard":
        template = _load_html("guard.html")
    else:
        template = _load_html("student.html")

    # Inject backend URL
    html = template.replace("{{EC2_API_URL}}", EC2_API_URL)

    return _html_response(html)
