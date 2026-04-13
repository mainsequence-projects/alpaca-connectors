# syntax=docker/dockerfile:1.4
FROM ghcr.io/main-sequence-server-side/poddeploymentorchestrator-jupyterhub-py311:latest

SHELL ["/bin/bash", "-lc"]

ARG USERNAME=dev
ARG UID=1000
ARG GID=1000

RUN groupadd -g ${GID} ${USERNAME} 2>/dev/null || true \
 && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME} 2>/dev/null || true

WORKDIR /app

COPY --chown=${UID}:${GID} . /app

RUN set -eux; \
    python -m pip install -U pip; \
    if [ -f requirements.txt ]; then python -m pip install -r requirements.txt; fi; \
    if [ -f requirements-dev.txt ]; then python -m pip install -r requirements-dev.txt; fi; \
    if [ -f pyproject.toml ] || [ -f setup.py ] || [ -f setup.cfg ]; then \
      if [ -f requirements.txt ] || [ -f requirements-dev.txt ]; then \
        python -m pip install -e . --no-deps; \
      else \
        python -m pip install -e ".[dev]" || python -m pip install -e .; \
      fi; \
    fi

USER ${USERNAME}

CMD ["bash", "-lc", "python -c \"import sys; print(sys.executable); print(sys.version)\" && sleep infinity"]
