FROM python:3.13-slim

# docker CLI — backend uses it to exec into agent containers
RUN apt-get update && apt-get install -y curl docker.io && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app/leadagent-data

# Copy requirements first for better caching
COPY backend/requirements.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the rest of the code
COPY . .

# Ensure the tag is set for all processes in the container
ENV LEADAGENT_TAG=true
ENV PYTHONPATH=/app/leadagent-data

EXPOSE 8000
CMD ["python", "backend/main.py"]
