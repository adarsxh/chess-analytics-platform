"""Application services: use cases that orchestrate adapters and repositories.

`ingestion` and `storage` stay independent of each other; a service is where they meet.
Entry points (the CLI now, job handlers in Phase 5) call services and nothing deeper.
"""
