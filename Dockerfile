FROM python:3.12-slim

# uv installieren
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Abhängigkeiten zuerst kopieren und installieren (Layer-Caching)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-cache

# Quellcode kopieren
COPY main.py ./
COPY src/ ./src/

# data/-Verzeichnis: Beim ersten Start werden fehlende Dateien automatisch angelegt.
# Als Volume mounten, um Nutzerdaten außerhalb des Images zu halten:
#   docker run -v /host/data:/app/data ...
RUN mkdir -p /app/data
VOLUME /app/data

# Nicht als root ausführen
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

CMD ["uv", "run", "python", "main.py"]
