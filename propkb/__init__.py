"""propkb -- per-property knowledge base built ON TOP OF docrag.

A separate "instance" of the docrag RAG: same engine (extract/chunk/embed/index/
query/answer from the ``docrag`` package), different data. Each property is a
corpus living under ``properties/<slug>/`` and indexed into ``properties/.index/``,
fully isolated from the general ``corpora/`` + ``.index/`` building-codes instance.

Dependency direction is one-way: ``propkb`` imports ``docrag``; ``docrag`` never
imports ``propkb``. That keeps the engine general and lets propkb be split into
its own repo later with no untangling.
"""
