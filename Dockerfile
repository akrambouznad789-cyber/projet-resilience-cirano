# Image de l'API du jumeau (FastAPI). Le paquet de service, confidentiel car dérivé de l'ENR,
# n'est JAMAIS dans l'image : il est monté en lecture seule au lancement.
#
#   docker build -t jumeau-cirano .
#   docker run --rm -p 8000:8000 -v /chemin/vers/service:/artefact:ro jumeau-cirano
#
# Démonstration sans aucune donnée confidentielle (réseau jouet) :
#   mkdir demo && docker run --rm -v "$PWD/demo:/sortie" --entrypoint python jumeau-cirano \
#       -m jumeau_cirano.api.synthetique /sortie
#   docker run --rm -p 8000:8000 -v "$PWD/demo:/artefact:ro" jumeau-cirano

# --- Étape 1 : environnement verrouillé (uv.lock), sans outils de développement ni MLflow ---
FROM python:3.12-slim-bookworm AS construction
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra api --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev --extra api

# --- Étape 2 : exécution, utilisateur sans privilèges ---
FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 10001 jumeau
WORKDIR /app
COPY --from=construction --chown=jumeau:jumeau /app /app
ENV PATH="/app/.venv/bin:$PATH" JUMEAU_ARTEFACT=/artefact PYTHONUNBUFFERED=1
USER jumeau
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/sante', timeout=4)" || exit 1
CMD ["uvicorn", "jumeau_cirano.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
