# Issue: External Ground-Truth Anchors (Deterministic Validation)

## Summary
Ground the Debate Engine's synthesis in reality by anchoring factual claims to deterministic tools. The Umpire should not judge a debate based on eloquence, but against execution results.

## Proposed Features
- **Tool-Augmented Umpire**: The Umpire uses `ls`, `grep`, `linter`, and `compiler` to verify agent claims during the synthesis phase.
- **Confidence Bankruptcy**: Automatically zero out an agent's confidence score if it proposes a non-existent file path or a non-compiling function.
- **Grounding Protocol**: All factual assertions in a debate must be backed by a deterministic check.

## Success Criteria
- The Umpire rejects a consensus if the proposed code fails a basic lint check.
- Agents are penalized for hallucinating API signatures that don't exist in the current project context.
