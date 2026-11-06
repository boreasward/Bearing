"""Shared foundation of the five-layer layout (spec §4.1, v2.12–v2.15).

Task 0 sinks the pieces every layer needs — transactions (``tx``), schema readiness
(``schema``), convention version strings (``versions``), budget and environment
constants (``constants``), front/back-aligned Chinese texts (``texts``), idempotency
(``idempotency``), secret redaction (``redact``), the stage registry definition
(``stages``) and the run locks (``locks``).
``workbench`` modules re-export from here until Task 1 removes them."""
