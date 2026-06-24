import os

# Set before any test module is imported — CREDITS_PER_EUR_CENT is evaluated at
# import time in user_credits.py and crashes collection if missing.
os.environ.setdefault("CREDITS_PER_EUR_CENT", "1.82")
