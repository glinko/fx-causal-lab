FROM python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FXLAB_DATA=/app/data FXLAB_PROJECT=/app
WORKDIR /app
COPY --chown=1000:1000 . .
RUN pip install --no-cache-dir -c requirements.lock '.[test]' && useradd --uid 1000 --create-home fxlab
USER 1000:1000
EXPOSE 8088
CMD ["uvicorn", "fxlab.web:app", "--host", "0.0.0.0", "--port", "8088", "--no-proxy-headers"]
