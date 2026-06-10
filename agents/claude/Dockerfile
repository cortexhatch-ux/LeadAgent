# Pinned base — floating tags can break a working build with no code change
FROM node:22.22.3-slim
# python3-requests: needed by the leadagent_perm/leadagent_main MCP servers
# that the backend launches inside this container via --mcp-config
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-requests && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
# Non-root: this container runs a prompt-injectable CLI — don't give it root
USER node
WORKDIR /home/node
# Auth tokens mounted at runtime: -v ~/.claude:/home/node/.claude
CMD ["tail", "-f", "/dev/null"]
