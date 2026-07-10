SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"

# Load backend/.env so env vars (DATA_DIR, BILLING_ENABLED, etc.) are available
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

export CORS_ALLOW_ORIGIN="http://localhost:5173;http://localhost:8080"
PORT="${PORT:-8080}"

# Seed top-up packs (test mode) after DB migrations
python -m scripts.seed_topup_packs

uvicorn open_webui.main:app --port $PORT --host 0.0.0.0 --forwarded-allow-ips '*' --reload --reload-delay 2 --timeout-graceful-shutdown 5
