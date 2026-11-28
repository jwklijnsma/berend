FROM python:3.15-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY berend ./berend

# OpenShift runs with an arbitrary UID in group 0: make writable dirs group-owned by root.
RUN mkdir -p /data/chroma && chgrp -R 0 /app /data && chmod -R g=u /app /data
ENV CHROMA_DIR=/data/chroma \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    ANONYMIZED_TELEMETRY=False
USER 1001
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1

CMD ["python", "-m", "berend.main"]
