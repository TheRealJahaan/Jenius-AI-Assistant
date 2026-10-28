#!/bin/sh
# Container entry point: bring the database schema up to date, load the demo, then serve.
set -e
python -m jenius init-db
# --proxy-headers + FORWARDED_ALLOW_IPS: behind Render/Fly/Railway every request arrives from their proxy, which
# sets X-Forwarded-For. Trusting it ("*") is right there because the app can only be reached through the proxy.
# If the container is reachable directly, set FORWARDED_ALLOW_IPS to your proxy's address instead.
exec uvicorn jenius.api.app:create_app --factory \
  --host 0.0.0.0 --port "${PORT:-8000}" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --workers "${WEB_CONCURRENCY:-2}" --no-server-header
