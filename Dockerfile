FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock
COPY pact ./pact
COPY web ./web
COPY data/manifest.json ./data/manifest.json
ARG INSTALL_NEURAL=false
RUN if [ "$INSTALL_NEURAL" = "true" ]; then pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu 'torch>=2.10,<3' && pip install --no-cache-dir '.[ml]'; fi
RUN pip install --no-deps . && useradd --uid 10001 --create-home pact && mkdir -p runtime && chown pact:pact runtime
USER pact
EXPOSE 8080
CMD ["uvicorn", "pact.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
