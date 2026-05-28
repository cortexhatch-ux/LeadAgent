FROM node:22-slim
RUN apt-get update && apt-get install -y python3 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @google/gemini-cli
# Auth tokens mounted at runtime: -v ~/.gemini:/root/.gemini
CMD ["tail", "-f", "/dev/null"]
