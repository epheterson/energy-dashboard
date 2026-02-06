FROM python:3.11-slim

WORKDIR /app

# Install system deps (curl needed by egauge toolkit's subprocess calls)
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt

# Copy app
COPY . .

# Remove dev files
RUN rm -rf __pycache__ data charts reports .env.example .gitignore Dockerfile docker-compose.yml

EXPOSE 8400

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8400", "--log-level", "info"]
