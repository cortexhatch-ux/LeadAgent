# Issue: Temporal Memory Graph & Logic Tombs

## Summary
Evolve the current static knowledge graph into a temporal system that links Git history, PR comments, and past agent reasoning. This allows LeadAgent to answer "why" a design decision was made in the past.

## Proposed Features
- **Git History Integration**: Index commit messages and diffs into KuzuDB.
- **Logic Tombs**: Store refuted arguments and failed reasoning paths (from debates) to prevent models from repeating historical hallucinations.
- **No-Fly Zones**: Index known logical fallacies or architectural mismatches for the specific codebase.
- **Historical Context Ingestion**: Feed the last 6 months of "evolutionary context" into the prompt before major refactors.

## Success Criteria
- User can ask "Why did we stop using the PTY-based approach?" and get a grounded answer based on past debate outcomes.
- Agents avoid proposing a solution that was previously refuted in a recorded debate.
