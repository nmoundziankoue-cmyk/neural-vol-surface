FROM python:3.14-slim

WORKDIR /app

# torch's default wheel pulls in CUDA deps not needed for this project
# (CPU-only inference on small grids) - the extra index below cuts the
# image down substantially. Drop it if you actually want GPU support.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .
COPY scripts ./scripts
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONPATH=/app
EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
