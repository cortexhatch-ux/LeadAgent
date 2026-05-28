FROM node:22-slim
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code
# Auth tokens mounted at runtime: -v ~/.claude:/root/.claude
CMD ["tail", "-f", "/dev/null"]
