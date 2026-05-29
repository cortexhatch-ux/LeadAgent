# Docker Setup

LeadAgent uses Docker Compose as its preferred environment, orchestrating the backend and agent daemons into an isolated network.

LeadAgent is **Docker-First but not Docker-Only** — it adapts automatically:

- **Mode A: Docker (Preferred)** — If Docker is running, it launches the containerized stack.
- **Mode B: Native (Zero Containers)** — If Docker is stopped, it runs natively on your host.

To run natively with zero containers, stop your Docker daemon and run `./start_backend.sh`. The onboarding wizard will guide you through local setup instead.

## Authentication Troubleshooting

When authenticating agents inside Docker (via `leadagent --onboarding` or `docker exec`):

- **Gemini Hang:** The Gemini CLI can occasionally hang or fail to terminate the TTY session after login. If the terminal becomes unresponsive, you can safely kill the terminal window — auth state is persisted in the container's volume.
- **Codex Autonomous Mode:** LeadAgent uses `codex exec --json` with the bypass flag for structured events without manual sandbox prompts.
