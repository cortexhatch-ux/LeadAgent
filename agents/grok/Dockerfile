# Pinned base — floating tags can break a working build with no code change
FROM node:22.22.3-slim
# git is required by the grok CLI (git_cli for repo context, worktrees, status banners, --worktree, --restore-code etc.)
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN npm install -g @xai-official/grok && npm cache clean --force
# Pre-create .grok owned by node — Docker's file-level bind mount of auth.json
# would otherwise create the parent dir as root, blocking session writes.
RUN mkdir -p /home/node/.grok && chown node:node /home/node/.grok
# Non-root: this container runs a prompt-injectable CLI — don't give it root
USER node
WORKDIR /home/node
CMD ["tail", "-f", "/dev/null"]
