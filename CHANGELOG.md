# Changelog

All notable changes to LeadAgent will be documented in this file.

## [0.6.0] - 2026-06-18

### Breaking Changes
- **Gemini CLI replaced by Antigravity (`agy`):** Google deprecated the `@google/gemini-cli` npm package and OAuth sign-in for individuals. LeadAgent now uses the `agy` CLI from [antigravity.google](https://antigravity.google). Run `curl -fsSL https://antigravity.google/cli/install.sh | bash` to install, then re-run onboarding.

### Changed
- **Docker:** `leadagent-gemini` container rebuilt with `agy` instead of `gemini`. Re-run `docker compose build gemini-agent` after upgrading.
- **Model ladder:** Updated fallback models to Gemini 3.5 Flash → Gemini 3.1 Pro (matching `agy`'s available models).
- **Auth check:** Gemini availability and auth detection now probe `agy --version` instead of `gemini --version`.

## [0.5.1] - 2026-06-14

### Added
- **Ollama Intelligence:** Automatic check for `llama3.2:3b` in onboarding.
- **Docker Parity:** Expanded container mapping for Codex, Grok, and Ollama in the setup wizard.
- **Graceful Fallbacks:** Debate engine now uses "Umpire Fallbacks" when an agent fails (auth/quota/model) to ensure debates always complete.

### Fixed
- **Codex Visibility:** Improved error reporting for Codex authentication. No more silent failures when logged out.
- **Grok Hardening:** Resolved git-related spawn errors in Docker by providing a proper workspace context.
- **CLI Versioning:** Correctly reporting v0.5.1 in `leadagent --version`.

## [0.5.0] - 2026-06-14

### Added
- **Multi-Agent Debates:** Run asynchronous debates between agents using `/debate`.
- **Grok Support:** Full integration for xAI's Grok CLI.
- **Codex Integration:** Support for OpenAI's Codex CLI with autonomous execution modes.
- **Observability Dashboard:** Real-time monitoring at `http://localhost:8000/dashboard`.

### Changed
- **Unified Installer:** `install.sh` now handles the entire stack setup.
- **Enhanced Routing:** Improved model affinity scoring for code vs. research tasks.

## [0.4.0] - 2026-06-10

### Added
- Initial support for Claude and Gemini CLI routing.
- Context-aware project indexing.
- Persistent memory via `agentmemory`.
