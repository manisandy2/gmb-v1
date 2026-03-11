#!/bin/bash

set -euo pipefail

echo "=============================================="
echo "🚀 Entrypoint starting (RUN_JOB=${RUN_JOB:-})"
echo "=============================================="

export PYTHONPATH=/app${PYTHONPATH:+":$PYTHONPATH"}

validate_config() {
    echo "📋 Validating configuration files..."
    
    if [ ! -f /app/config.env ]; then
        echo "❌ config.env NOT FOUND"
        ls -la /app/config* || true
        exit 1
    fi
    echo "✅ config.env exists"
    
    if [ ! -f /app/config.credentials.json ]; then
        echo "❌ config.credentials.json NOT FOUND"
        exit 1
    fi
    
    if ! python3 -m json.tool /app/config.credentials.json > /dev/null 2>&1; then
        echo "❌ JSON is INVALID"
        exit 1
    fi
    echo "✅ config.credentials.json is valid"
}

validate_config

case "${RUN_JOB:-0}" in
    1|"30min"|"30-minute")
        echo "🚀 Running Job: 30-Minute Sync (Token + Reply + Summary)"
        exec python jobs/30minutesSync.py
        ;;
    2|"eod"|"end-of-day")
        echo "🚀 Running Job: End-of-Day Sync (Locations + Metrics)"
        exec python jobs/eodSync.py
        ;;
    3|"fetch"|"fetch-reviews")
        echo "🚀 Running Job: Fetch Reviews"
        exec python jobs/fetchReviewJob.py
        ;;
    4|"metrics"|"daily-metrics")
        echo "🚀 Running Job: Daily Metrics"
        exec python jobs/metricsJob.py
        ;;
    5|"sync"|"sync-locations")
        echo "🚀 Running Job: Sync Locations"
        exec python jobs/syncLocationsJob.py
        ;;
    0|""|"server"|"web")
        echo "🚀 Starting FastAPI web server"
        exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}" --log-level info
        ;;
    *)
        echo "❌ Unknown RUN_JOB value: $RUN_JOB"
        echo ""
        echo "Valid values:"
        echo "  0, '', 'server', 'web'     -> Start web server"
        echo "  1, '30min', '30-minute'    -> 30-minute sync"
        echo "  2, 'eod', 'end-of-day'     -> End-of-day sync"
        echo "  3, 'fetch', 'fetch-reviews'-> Fetch reviews"
        echo "  4, 'metrics', 'daily-metrics' -> Daily metrics"
        echo "  5, 'sync', 'sync-locations'-> Sync locations"
        exit 1
        ;;
esac