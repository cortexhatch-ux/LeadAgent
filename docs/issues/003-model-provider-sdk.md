# Issue: Model-Agnostic Provider SDK (Plugin System)

## Summary
Abstract the current hardcoded CLI/PTY logic into a standardized provider interface. This allows users to plug in any local model (Ollama) or new API provider (DeepSeek, Grok, etc.) via a simple JSON manifest.

## Proposed Features
- **Provider Abstraction Layer**: Define a standard JSON/gRPC interface for sending prompts and receiving streams.
- **Local Ollama Support**: First-class integration for Ollama models running on local ports.
- **Manifest-based Discovery**: Add new agents by dropping a `.json` file into a `providers/` directory.
- **Diversity Constraints**: Router logic to ensure "Architectural Diversity" (never routing consecutive turns to the same model family).

## Success Criteria
- A user can add a new model to LeadAgent by editing a config file, without modifying Python code.
- Ollama models can participate in debates alongside Claude/Gemini.
