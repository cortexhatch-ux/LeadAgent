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
