# Runs CoolBlocks on any Docker host (see docker-compose.yml). Listens on port 7860.
FROM python:3.11-slim

# rasterio needs libexpat, which the slim image no longer ships
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY --chown=user . .
USER user

ENV COOLBLOCKS_WARMUP=1 MPLCONFIGDIR=/tmp/mpl
WORKDIR /app/backend
EXPOSE 7860
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
