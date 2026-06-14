# Changelog

All notable changes to LeadAgent will be documented in this file.

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
