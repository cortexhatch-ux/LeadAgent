# Pinned base — floating tags can break a working build with no code change
FROM python:3.13.13-slim

# curl is needed at runtime by the compose healthcheck.
# Install only the static docker CLI binary — the docker.io package ships the
# entire engine/daemon, which this image never runs.
# This container keeps root: it drives the host Docker socket to exec into
# agent containers, which requires it.
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    curl -fsSL "https://download.docker.com/linux/static/stable/$(uname -m)/docker-29.5.3.tgz" \
    | tar -xz --strip-components=1 -C /usr/local/bin docker/docker

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
