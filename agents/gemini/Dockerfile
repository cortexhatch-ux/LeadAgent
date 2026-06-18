# Pinned base — floating tags can break a working build with no code change
FROM node:22.22.3-slim
# python3-requests: needed by the leadagent_perm/leadagent_main MCP servers
# that the backend launches inside this container via --mcp-config
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-requests curl ca-certificates && rm -rf /var/lib/apt/lists/*
# Install agy to a system-wide path so the non-root node user can access it
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- --dir /usr/local/bin
# Non-root: this container runs a prompt-injectable CLI — don't give it root
USER node
WORKDIR /home/node
# Auth tokens mounted at runtime: -v ~/.gemini:/home/node/.gemini
CMD ["tail", "-f", "/dev/null"]
