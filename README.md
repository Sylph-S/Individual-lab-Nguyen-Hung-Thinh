# Evidence-Driven Remediation Engine

## How to Run

Install PyYAML (`pip install pyyaml`), then run `python engine.py decide --incident eval/E01.json --history incidents_history.json --actions actions.yaml` to process a single evaluation incident. The engine will output a JSON decision to stdout and append one line to `audit.jsonl` containing the selected action, confidence score, and full evidence chain. To reproduce the complete audit log, delete `audit.jsonl` and run the command for all eight evaluation incidents (E01.json through E08.json). The engine implements a three-layer pipeline: Layer 1 extracts features from logs (normalized templates via regex), traces (per-edge error rates), and metrics (baseline vs recent deltas); Layer 2 retrieves similar historical incidents using weighted similarity (40% logs + 40% traces + 20% services) and performs outcome-weighted voting where successful actions get full weight, partial get 0.5×, and failed get 0.1×; Layer 3 computes expected value for each candidate action and applies conservative escalation gates (similarity < 0.2, confidence < 0.35, high blast radius, negative EV) to decide between auto-action and human escalation via `page_oncall`.

## Expected Output

Each line in `audit.jsonl` contains the incident ID, selected action with parameters, confidence (0-1), and evidence including the top similar historical incidents, voting weights, and decision reasoning. For auto-actions, the evidence shows which neighbors voted and the expected value calculation; for escalations, it shows which gate triggered (out_of_distribution, low_confidence, high_blast_radius, etc.).
