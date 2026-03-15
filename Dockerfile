FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY requirements.lock ./

RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache -r requirements.lock && \
    python -m compileall -q /opt/venv/lib/

FROM python:3.11-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV="/opt/venv"

WORKDIR /app

COPY server.py proxmox_tools.py ./

RUN python -m compileall -q .

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

CMD ["python", "-u", "server.py"]
