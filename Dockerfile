FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SM_DATA=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py store.py live.py manage.py ./
COPY public ./public

# Runs as joseph's uid so files in the bind-mounted ./data stay his.
USER 1000:1000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

# One worker: live gig state is in process memory. Threads carry the WebSockets.
# No access log: request paths contain join tokens.
CMD ["gunicorn", "-w", "1", "--threads", "100", "-b", "0.0.0.0:8000", \
     "--graceful-timeout", "5", "--no-control-socket", "--error-logfile", "-", "server:app"]
