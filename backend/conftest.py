import os

# These must be set before any app module is imported — they are evaluated at
# import time and will use whatever is in os.environ at that point.
os.environ.setdefault("WEBUI_URL", "http://localhost:8080")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/open_webui_test.db")
os.environ.setdefault("CREDITS_PER_EUR_CENT", "1.82")
