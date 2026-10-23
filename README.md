# NH Bishop Election — live results

Unofficial live dashboard for the election of the 11th Bishop of New Hampshire, Saturday Oct 24, 2026 (St. Paul's Church, Concord).

- **Dashboard:** GitHub Pages serves `index.html`, which reloads `data/results.json` every 60 s. `?demo=1` shows sample data.
- **Poller:** `.github/workflows/poll.yml` checks https://www.nhepiscopal.org/diocesan-convention-2026 every 60 s, commits new ballots, and stops when a nominee has a majority in both orders. Scheduled for 09:00 and 14:40 EDT Oct 24; also runnable from Actions → *Poll election results* → *Run workflow*.
- **Manual fallback:** Actions → *Enter ballot manually* → *Run workflow*. Votes in order Cardoza, Gardner-Smith, Lindh-Payne, e.g. `20,28,14`; leave a slot blank if a nominee is off the ballot (`34,28,`). Manual entries are never overwritten by the poller.

## Setup
1. Settings → Pages → Deploy from branch → `main` / root.
2. Settings → Actions → General → Workflow permissions → **Read and write**.
3. Before 9:45 a.m. Saturday, start *Poll election results* manually (GitHub can delay scheduled runs).
