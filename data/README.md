# Aggregated data

Trial-level data underlying the figures and tables of the paper. Each CSV begins
with a `# provenance:` header recording the run(s) and the extraction script.

| File | Contents |
|---|---|
| `per_trial.csv` | Per-trial metrics across scenario × baseline × fault × interpreter (safety, radius, task success, gate decisions, confidence) |
| `htraj.csv` | Clearance-to-floor trajectories for the Tier 1 isolated floor verification (Figure: clearance trajectories) |
| `latency.csv` | Per-call intent-interpreter inference latency and the safety command-stream issuing period |
| `T1.md`–`T4.md` | Table source data |

The raw simulation logs (ROS bags, ~1 GB) are not deposited here; they can be
regenerated from the code and configuration in this repository (see the top-level
`README.md`).
