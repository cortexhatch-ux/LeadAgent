# Debate Engine

LeadAgent's Debate Engine forces models into adversarial collaboration to surface the "ground truth" instead of echoing your premises back at you.

**See it in action:** [Ontological Debate: The Game of Thrones Blueprint Crisis](../DEBATE_EXAMPLE.md)

## How It Works

Debate runs as a GAN-style multi-round tournament:

1. **Round N** — all debaters argue, seeing previous round positions.
2. **Umpire** — a neutral agent reads all positions anonymously, then injects one sharp question to force an unexplored angle.
3. **Round N+1** — debaters must address the umpire's question and attack the others' reasoning.
4. **Synthesis** — each agent declares consensus, remaining disputes, and what changed their mind.

## Why It Matters

- **Bias Neutralization:** Different models critiquing each other flushes out model-specific biases.
- **Hallucination Defense:** Adversaries are incentivized to call out hallucinations to "win" the round.
- **Umpire Protocol:** A neutral agent identifies logical fallacies and drives toward a final **Consensus**.

## CLI Usage

```bash
# Basic debate
leadagent debate "Should we use Microservices or a Monolith for this MVP?"

# Options
leadagent debate "Topic" --rounds 5       # Set number of adversarial rounds
leadagent debate "Topic" --no-context     # Skip local project indexing
```

Debate results are stored in KuzuDB episodic memory and available to future queries.

## Case Study: The "Product Trap" (v0.5.0 Update)

**Topic:** "Should LeadAgent implement its own web-based IDE, or stay as a CLI-first 'Global Brain' orchestrator?"

### ━━━━━━━━━━━━━━━━━━━━━━━━ ROUND 1 ━━━━━━━━━━━━━━━━━━━━━━━━━

**CLAUDE:** "Stay CLI-first. Building a web IDE is a distraction that betrays the architecture. LeadAgent's core value is universal orchestration across projects — invoked from any directory, context-aware via the Knowledge Graph, model-agnostic. A web IDE contradicts this: it centralizes what should be ambient."

**GEMINI:** "LeadAgent must remain a CLI-first 'Global Brain'. Building a web-based IDE is a strategic suicide mission that ignores the project's core moat: ubiquity. The 'Cursor Trap' — competing with VS Code or Cursor is a losing battle. Diverting engineering cycles to reinvent text editing, linting, and extension support is a waste."

**CODEX:** "LeadAgent should stay CLI-first and become the best 'Global Brain' orchestrator, not build its own web IDE. A web IDE is a product trap. It drags LeadAgent into editor UX, file trees, terminals, diff viewers... That is a full company's surface area, and VS Code/Cursor already own it."

### ━━━━━━ FINAL SYNTHESIS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### 🏆 CONSENSUS REACHED
- **No Web IDE:** LeadAgent will not build a browser-based code editor. The engineering cost is prohibitive and the competitive landscape is saturated.
- **Ubiquity is the Moat:** Value lies in working everywhere — any directory, any repo, any editor.
- **Protocol-First:** The path to dominance is through the Model Context Protocol (MCP) and LSP, becoming the backend for existing editors rather than a replacement.
- **CLI Primary:** The CLI remains the canonical, context-aware interface for the "Global Brain."

### ─── ● FINAL VERDICT
LeadAgent will strictly remain a CLI-first 'Global Brain' orchestrator. It will focus its engineering effort on deepening the orchestration core, the Knowledge Graph, and protocol adapters (MCP/LSP) for existing professional editors like VS Code and JetBrains. Writable coding surfaces are rejected in favor of "ambient intelligence" that follows the developer across any environment.
