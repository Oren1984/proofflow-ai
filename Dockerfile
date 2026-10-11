FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PROOFFLOW_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt requirements-llm.txt ./
# The optional LLM client is only installed when explicitly requested at build time.
ARG INSTALL_LLM=false
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_LLM" = "true" ]; then pip install -r requirements-llm.txt; fi

RUN useradd --create-home --uid 10001 proofflow \
    && mkdir /data \
    && chown proofflow:proofflow /data

COPY --chown=proofflow:proofflow .streamlit/ .streamlit/
COPY --chown=proofflow:proofflow demo/synthetic_data/ demo/synthetic_data/
COPY --chown=proofflow:proofflow app/ app/

USER proofflow
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

CMD ["streamlit", "run", "app/main.py", "--server.port=8501", "--server.address=0.0.0.0"]
