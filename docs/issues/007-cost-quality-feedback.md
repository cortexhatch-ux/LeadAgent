# Issue: Cost/Quality Scorecard (ML Feedback Loop)

## Summary
Close the feedback loop for the routing brain by logging actual costs and human-labeled quality signals. This data feeds back into the LightGBM classifier to improve routing accuracy over time.

## Proposed Features
- **Quality Scorecard**: Simple "thumbs up/down" interface for users to rate routing decisions and debate outcomes.
- **Token/Cost Tracker**: Log the precise cost of each agent turn based on vendor pricing.
- **ML Training Pipeline**: Periodic background task that retrains the routing brain using the new labeled data.
- **ROI Dashboard**: Show the user how much time/money LeadAgent has saved by routing to cheaper/faster models without loss of quality.

## Success Criteria
- The routing brain's accuracy improves statistically over a 30-day period.
- User can see a cost-per-task breakdown in the dashboard.
