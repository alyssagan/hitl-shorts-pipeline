"""Evaluation workflow (docs/EVALUATION.md): reading RELEVANCE_LABELS.jsonl across projects, computing a
per-scoring-method/version report from it (`report.py`), and sampling assets for human review with a
recorded, honest sampling method (`sampling.py`) -- including a holdout discipline (`labels.is_holdout`)
so a future scoring version can be compared against a set nobody tuned against.

No database: everything here reads/writes the same flat files the rest of the pipeline already uses
(`pipeline/core/store.py`'s one-folder-per-project layout), so it works on the same `projects/` directory
the API serves, and needs nothing running to use.
"""
