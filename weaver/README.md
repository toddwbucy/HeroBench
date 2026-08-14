# weaver/

**Everything under this directory is ours and none of it is upstream's.**

This fork tracks [stefanrer/HeroBench](https://github.com/stefanrer/HeroBench),
and the benchmark itself is kept as close to the original as possible so that
results stay comparable and upstream changes stay easy to merge. Nothing we add
is placed among the benchmark's own files. It all lives here instead, so
`git diff` against upstream shows exactly one added directory and the benchmark
reads as it always did.

## What is here

    replay-viewer/    a static HTML and JS replay viewer for agent runs
    docs/             the design record for the above

## replay-viewer

A point-in-time, scrub-driven inspector for a run's trace. One scrub position
drives everything: pick a moment and the map, the state panel, and the
cognitive log all reflect that moment. `index.html` is a three-column layout,
`viewer.js` carries the behaviour, and `serve.py` is a no-cache static server
on port 8085. The Python files beside them are the analysis tooling the viewer
grew alongside: episode extraction, world fetching, and several audits.

`docs/replay-viewer-Spec.md` is the specification it was built to, and
`docs/benchmark-protocol.md` records how the runs it reads were produced.

## Provenance, and the caveat that matters

This code was written for **WeaverTools** and lived there at
`scripts/herobench-replay-viewer/`. It was removed from that tree in a
deliberate purge, and it is recovered here from the archived repository's git
objects at commit `2ac0623`, which is the last tree that held it. It is
recovered whole: thirteen files, `viewer.js` at 1561 lines, matching the
Spec's own account of itself.

**It was built against the previous program's trace schema.** That schema
carried a memory leg, fog-of-war, notepad, and graph-query panels that the
current WeaverTools trace does not emit. So the interaction design, the
scrubber, and the map machinery are the reusable part, and the data-loading
layer needs rework against whatever the trace actually carries before the
viewer will open a current run. Treat it as a working reference rather than as
something that runs today.
