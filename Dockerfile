FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    APP_MODE=production AGENT_DOMAIN=supply_chain
WORKDIR /app
RUN useradd --system --uid 10001 --home /app appuser && mkdir -p /app/var && chown appuser:appuser /app/var
COPY pyproject.toml /app/pyproject.toml
COPY support_agent/ /app/support_agent/
RUN --mount=type=secret,id=pip_ca,required=false \
    if [ -f /run/secrets/pip_ca ]; then export PIP_CERT=/run/secrets/pip_ca; fi; \
    pip install --no-cache-dir '.[knowledge,mcp,oidc]'
USER appuser
EXPOSE 8000
CMD ["python", "-m", "support_agent.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
