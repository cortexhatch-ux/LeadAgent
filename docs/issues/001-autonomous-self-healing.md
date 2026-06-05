# Issue: Autonomous Self-Healing via Watchdog + Debate

## Summary
Transform LeadAgent from a reactive assistant into a proactive partner by linking the filesystem watchdog directly to the adversarial debate engine. When a build failure or test flake is detected, the system should autonomously trigger a debate between agents to identify the root cause and propose a verified patch.

## Proposed Features
- **Proactive Trigger**: Watchdog detects file changes -> triggers background build/test.
- **Root Cause Debate**: On failure, agents (e.g., Claude and Gemini) analyze logs and code to debate the fix.
- **Verification Sandbox**: The proposed patch is tested in a temporary sandbox before being presented to the user.
- **Dashboard Notification**: Verified patches appear in the UI as "LeadAgent suggests this fix."

## Success Criteria
- System detects a syntax error and proposes a fix without user intervention.
- System correctly identifies flaky tests and suggests isolation strategies.
