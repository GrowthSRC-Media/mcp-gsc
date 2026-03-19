FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gsc_server.py .

# /data is mounted as a volume and holds:
#   client_secrets.json  — OAuth credentials from Google Cloud Console
#   tokens/              — per-user token files (created automatically)
#   oauth_states.json    — transient OAuth state (created automatically)
VOLUME /data

# All configuration is read from .env (mounted at runtime) or environment variables.
# No defaults are baked into the image. See .env.example for all options.

CMD ["python", "gsc_server.py"]
