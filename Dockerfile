# Runs CoolBlocks on Hugging Face Spaces (or any Docker host). Open port 7860.
FROM python:3.11-slim

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
