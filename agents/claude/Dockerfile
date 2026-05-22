FROM node:22-slim
RUN npm install -g @anthropic-ai/claude-code
# Auth tokens mounted at runtime: -v ~/.claude:/root/.claude
CMD ["tail", "-f", "/dev/null"]
