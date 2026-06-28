import os

# Ensure billing constant is set for any test that imports user_credits before
# the root conftest runs (shouldn't happen, but defensive).
os.environ.setdefault("CREDITS_PER_EUR_CENT", "1.82")
