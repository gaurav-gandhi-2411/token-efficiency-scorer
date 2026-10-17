# Licence of this directory

This directory holds numbers and labels derived from trajectories in `nebius/SWE-agent-trajectories` (80)
and `nebius/SWE-rebench-openhands-trajectories` (70), both CC-BY-4.0. The trajectories themselves
(`traces_normalized/`) were removed from the repository in 0.16.0; `scripts/00_download_corpus.py`
re-creates them locally from the public datasets. The remaining files carry no trajectory text but
are derived from third-party data and are not covered by the Apache-2.0 licence of the tracegauge
code; they are offered under the datasets' CC-BY-4.0 terms. Attribution, caveats and URLs are in
`DATA_SOURCES.md` at the repository root.
