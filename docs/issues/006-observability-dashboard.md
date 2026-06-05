# Issue: Observability Dashboard & Memory Explorer

## Summary
Build a web-based UI that makes LeadAgent's "invisible" intelligence (memory graph, routing decisions, debate rounds) visible and debuggable for the user.

## Proposed Features
- **Memory Graph Explorer**: Interactive D3/Cytoscape visualization of the KuzuDB knowledge graph.
- **Routing Decision Log**: Show side-by-side responses and why the router picked a specific agent for a task.
- **Live Debate Viewer**: A clean interface for watching adversarial rounds and umpire synthesis.
- **Task Affinity Heatmap**: Visualize which agents are performing best for different categories of work (e.g., "coding" vs "refactoring").

## Success Criteria
- User can see a "cluster" of related files in the memory graph and understand how they are linked.
- Routing decisions are transparent, showing the scores from the LightGBM classifier.
