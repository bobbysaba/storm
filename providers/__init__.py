"""Data providers: packages that add data a [[sources]] table can't describe.

nssl/ ships with STORM and plugs in like any other. A group's own provider
(a code provider) is a package at providers/<name>/ (git-ignored, so private
code stays out of the repo) or at ``[providers.<name>] path``, turned on
with ``[providers.<name>] enabled = true``. Its other keys are its options.
settings/code_providers.py loads it; the interface is in docs/providers.md.
"""
