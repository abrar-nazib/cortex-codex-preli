FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the package into /app/normalizer/ so `uvicorn normalizer.main:app` resolves correctly
COPY . ./normalizer/

EXPOSE 9000

CMD ["uvicorn", "normalizer.main:app", "--host", "0.0.0.0", "--port", "9000"]
