Maç Analiz Pro v30 - Expanded score-derived markets

Upload only these three files to the existing GitHub repository, preserving paths:
  app.js
  sw.js
  scripts/analysis_engine.py

Then run the existing Update football archive GitHub Action.
Existing SQLite, JSON data, prediction history and workflow files must remain untouched.

Markets are calculated from the SAME Elo-adjusted Poisson expected goals, with no new API requests.
Old frozen predictions are not recomputed or rewritten. Newly generated snapshots gain new markets.
Only markets with >=50% are displayed in the detail screen. Goal-difference markets are exclusive
outcomes, and combinations are joint score probabilities (not products of marginals).
No first scorer, corners, players, cards or first-half markets are added without sufficient event data.
The baseline Elo model remains unchanged; no new time-decay model is activated.

IMPORTANT: Expanded markets are mathematically derived but not individually backtested yet.
