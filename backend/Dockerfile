FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

# Keep the core API/chat runtime small. Node/npm/git/Chromium are not required for
# LLM/image API calls. Browser automation is opt-in and should not bloat every
# backend/worker/beat image on a small production VPS.
ARG INSTALL_AGENT_BROWSER=0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && if [ "$INSTALL_AGENT_BROWSER" = "1" ]; then \
         apt-get install -y --no-install-recommends git nodejs npm; \
       fi \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && curl -kfsSL "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" -w "\n" >> "$(python -m certifi)" \
    && if [ "$INSTALL_AGENT_BROWSER" = "1" ]; then \
         PLAYWRIGHT_BROWSERS_PATH=/opt/agent-browsers python -m playwright install --with-deps chromium \
         && chmod -R a+rX /opt/agent-browsers; \
       fi \
    && find /usr/local/lib/python3.13/site-packages -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && find /usr/local/lib/python3.13/site-packages -type f -name '*.pyc' -delete \
    && rm -rf /root/.cache /tmp/*

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/agent-browsers

COPY . .
RUN python -m compileall -q /app \
    && find /app -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && find /app -type f -name '*.pyc' -delete

RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /app app \
    && mkdir -p /app/media /app/staticfiles /app/logs \
    && find /app -type d -name '__pycache__' -prune -exec rm -rf {} + \
    && find /app -type f -name '*.pyc' -delete \
    && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
