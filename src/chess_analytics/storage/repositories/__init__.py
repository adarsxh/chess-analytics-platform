"""Repositories: the only code that reads and writes tables (ARCHITECTURE.md).

Convention: a repository wraps a SQLAlchemy `Session` and never commits. The caller (a
service) owns the transaction and decides where its boundaries are.
"""
