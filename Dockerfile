FROM python:3.11-slim

WORKDIR /app

COPY . .

# Install system dependencies for common Python packages
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot/main.py"]
