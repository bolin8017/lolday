"""Lolday job pod init-container helpers.

- write_config: fetches resolved_config + dataset CSVs from backend, writes to /mnt/config
- fetch_model: downloads an MLflow run's model artifacts to /mnt/source-model
"""
