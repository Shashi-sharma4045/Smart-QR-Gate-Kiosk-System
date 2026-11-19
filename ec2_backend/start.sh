#!/usr/bin/env bash
# start.sh — Launch the Flask app with Gunicorn on EC2
# Usage: chmod +x start.sh && ./start.sh

set -euo pipefail

# Activate virtualenv if present
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# Load .env if it exists
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "Starting Smart Gate Kiosk EC2 backend..."
echo "DB path  : $(pwd)/outpass.db"
echo "SNS Topic: ${SNS_TOPIC_ARN:-NOT SET}"
echo "Region   : ${AWS_REGION:-ap-south-1}"

# Gunicorn: 2 sync workers, bind on all interfaces port 5000
exec gunicorn \
    --workers 2 \
    --bind 0.0.0.0:5000 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    app:app
