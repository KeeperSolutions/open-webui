import os

# WEBUI_URL is required at config.py import time.
# Set a test default so the test suite can run without the env var.
os.environ.setdefault("WEBUI_URL", "http://localhost:8080")
