#!/usr/bin/env bash
# =============================================
# SILTA - Automated Test Environment Setup & Run
# =============================================

cleanup() {
    echo ""
    echo "=========================================="
    echo "✅ Cleanup complete. Virtual environment 'venv' removed."
    echo "To restart, run the script again."
    echo "=========================================="
    rm -rf venv
}

trap cleanup INT TERM

echo "🚀 Initializing SILTA environment..."

# 1. VENV
if [[ ! -d "venv" ]]; then
    echo "▶️ Creating virtual environment (venv)..."
    python3 -m venv venv
fi

# 2. Dependencies
echo "▶️ Installing dependencies..."
./venv/bin/pip install --quiet -r requirements.txt

# 3. Start server in background
echo ""
echo "=========================================="
echo "🔥 SILTA BRIDGE STARTED! Press Ctrl+C to stop and reset."
echo "=========================================="

# Launch the server (main.py) in background, redirecting output to logs if desired
./venv/bin/python main.py > /dev/null 2>&1 &

SERVER_PID=$!

# Wait for the port to be ready (optional but recommended)
timeout=10   # max seconds
elapsed=0
while ! curl -s http://127.0.0.1:7842 > /dev/null; do
    sleep 0.5
    elapsed=$(echo "$elapsed + 0.5" | bc)
    if (( $(echo "$elapsed >= $timeout" | bc) )); then
        echo "⚠️ Timeout: server not ready within $timeout s."
        break
    fi
done

# Open the browser with the default URL
xdg-open http://127.0.0.1:7842 || true

# Wait for the user to close the script (Ctrl+C)
wait "$SERVER_PID"
