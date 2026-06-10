# Pinned base — floating tags can break a working build with no code change
FROM node:22.22.3-slim
RUN npm install -g @openai/codex && npm cache clean --force
# Non-root: this container runs a prompt-injectable CLI — don't give it root
USER node
WORKDIR /home/node
CMD ["tail", "-f", "/dev/null"]
