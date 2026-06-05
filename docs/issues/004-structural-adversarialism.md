# Issue: Structural Adversarialism & Role-Locking

## Summary
Prevent "collusive hallucination" and consensus bias by enforcing structural adversarialism in the debate engine. Instead of agents "seeking agreement," they should be forced into opposing roles to falsify each other's reasoning.

## Proposed Features
- **Role-Locking**: Assign agents hard-coded roles (Attacker, Defender, Skeptic) before the debate starts.
- **Forced Divergence**: router constraints that prevent agents from converging on "easy but flawed" solutions.
- **Adversarial Scoring**: Reward agents for finding flaws in others' logic rather than for agreeing.

## Success Criteria
- Debates consistently surface logical inconsistencies in agent proposals.
- The Umpire can identify when two models are "agreeing for the sake of agreement."
