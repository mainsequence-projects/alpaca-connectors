# syntax=docker/dockerfile:1.4
FROM ghcr.io/main-sequence-server-side/poddeploymentorchestrator-python-py313@sha256:8b2477adbc42fcd0e754ea853bf3913be5e3e8bb142e9d9dff58cfa940e75382

USER root

SHELL ["/bin/bash", "-lc"]

ARG USERNAME=dev
ARG UID=1000
ARG GID=1000

RUN groupadd -g ${GID} ${USERNAME} 2>/dev/null || true \
 && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME} 2>/dev/null || true

WORKDIR /app

COPY --chown=${UID}:${GID} . /app

RUN set -eux; \
    python -m pip install -U 'pip @ https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70'; \
    python -m pip install --no-cache-dir certifi==2026.10.19; \
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
