FROM node:22-slim
RUN npm install -g @google/gemini-cli
# Auth tokens mounted at runtime: -v ~/.gemini:/root/.gemini
CMD ["tail", "-f", "/dev/null"]
