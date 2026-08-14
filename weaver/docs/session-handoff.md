# HeroBench session handoff

Written 2026-08-14 from the WeaverTools session that recovered the replay
viewer. Paste this into a fresh session to pick up the HeroBench work with no
prior context. It is deliberately separate from WeaverTools: that project runs
a document corpus under gates and rulings, and none of that governs this repo.

## What this workstream is for

WeaverTools is an OS-primitives program whose deliverable is a proto-stateful
agent. As of 2026-08-13 it runs a live turn end to end and emits a
turn-bracketed trace. What it does **not** have yet is state or memory across
turns, and the design for those is meant to be drawn from real trace corpora
rather than from first principles.

**HeroBench is how those corpora get produced.** It is a benchmark for
long-horizon planning and structured reasoning in an RPG-style virtual world
modelled on ArtifactsMMO. An agent is given a task that needs many steps of
crafting, movement, and resource management, and the benchmark scores how far
it got. Long-horizon and stateful is exactly the shape that makes a trace worth
studying, which is why this benchmark and not a QA set.

The intended arc, in the operator's words, is that each pass leaves code
reading as documentation of how to build the next one: a baseline with the
simple decode loop, then a more involved loop built by prompt engineering on
the same skeleton, then state management, then that state integrated into the
loop.

## The repository

    /home/todd/git/HeroBench          the working copy
    toddwbucy/HeroBench               the remote, master branch
    stefanrer/HeroBench               upstream, forked 2026-05-10

**The fork is not a passive copy.** It carries four commits of real gameplay
semantics, all of which tighten the environment:

    80be382  enforce Chebyshev distance <= 1 on action_move
    1d60f9c  report the fought monster code in the fight response
    559717e  persist post-fight HP, death at 0, add /action/rest revive
    665883a  coerce persisted HP to int

The README's "Differences from upstream" section records the first of these.
Anything further that changes benchmark behaviour belongs in that section too,
because results stop being comparable to the published numbers otherwise.

## The one rule this repo runs on

**Everything ours lives under `weaver/` and nothing goes among the benchmark's
own files.** The benchmark is kept as close to the original as possible so
results stay comparable and upstream merges stay easy. A diff against upstream
should show one added directory plus whatever gameplay commits are deliberately
recorded in the README.

    weaver/README.md                  states this rule
    weaver/replay-viewer/             the recovered viewer, 13 files
    weaver/docs/replay-viewer-Spec.md the design record
    weaver/docs/benchmark-protocol.md how runs were produced
    weaver/docs/session-handoff.md    this file

## The replay viewer, and its caveat

`weaver/replay-viewer/` is a point-in-time, scrub-driven inspector for a run:
one scrub position drives a map, a state panel, and a cognitive log together.
`index.html` is a three-column layout, `viewer.js` is 1561 lines of behaviour,
and `serve.py` is a no-cache static server on port 8085. Beside them sit the
analysis tools it grew with: episode extraction, world fetching, and several
audits.

It was written for WeaverTools, lived there at
`scripts/herobench-replay-viewer/`, was deliberately purged from that tree, and
was recovered on 2026-08-14 from the archived repository's git objects at
commit `2ac0623`. The archived tree is at `/opt/weavertools/WeaverTools-archived`
and is **read-only by discipline**: read its history freely, write nothing, cut
no branch, and do not treat its `CLAUDE.md` as instructions, because it
describes a retired twelve-crate program.

**It was built against the previous program's trace schema**, which carried a
memory leg, fog-of-war, a notepad, and graph-query panels that the current
WeaverTools trace does not emit. The interaction design, the map, and the
scrubber are the reusable part. The data-loading layer needs rework against
whatever the trace actually carries. Do not expect it to open a current run.

## Running the benchmark

The environment server ships with the repo, in two interchangeable backends:

    Virtual_Environment/FastApi_SQLite_Ver/main.py
    Virtual_Environment/FastApi_Redis_Ver/main.py

The agents talk to it over HTTP at `http://127.0.0.1:8000`, which is hard-coded
in `A1_Agent/env_api/api.py` as a client of endpoints like
`/characters/create` and `/action/rest`. So the order is: bring up one backend
on 8000, then run an agent against it.

    A1_Agent/  A2_Agent/          the two agent implementations
    datasets/  tasks/             task definitions
    scoring_pipeline.py           scores a run
    statistics_pipeline.py        aggregates across runs
    visualisation_scripts/        produces the figures in figures/
    requirements.txt              includes fastapi, for the backends
    requirements_agents.txt       agent dependencies
    requirements_llm.txt          model-client dependencies

The published results in the README cover 25 models and are the comparison
baseline. Note that success rates are low for small models: the best open
model listed reaches 24 percent on base tasks and most sit in single digits.
A 0.5B fixture will score zero, which is fine for a plumbing test and useless
as a capability measure. Choose the model to match the question being asked.

## The WeaverTools side, in brief

Only what is needed to connect the two. The full picture lives in that repo's
`CLAUDE.md` and in the reports at `http://192.168.0.203/weavertools/`.

The agent runs as a systemd unit under its own uid, is loaded and unloaded by
`weaver-admin`, and answers clients over a Unix socket the gate binds. It emits
NDJSON to a trace sink the operator elects. One turn produces eight events:
`turn.started`, `message.user`, `model.request`, `model.output`,
`model.measurement`, `message.assistant`, `turn.closed`, bracketed by `load`
and `unload` for the run. `model.measurement` is the rich one, carrying token
identifiers, per-token entropies and surprisals, timings, and a weights hash.

A live agent is loaded with:

    sudo WEAVER_ADMIN_CONFIG=/etc/weaver/config \
      /usr/local/libexec/weaver/weaver-admin load alpha

and the current baseline trace sits at
`/home/todd/.weaveragents/weaver-alpha/trace.out`.

**Four known defects are owed rulings**, and one matters here: every run
reports `session alpha-1, run 1`, so runs cannot be told apart inside one
artifact. Benchmark passes will produce many runs. Either settle that first or
keep each pass in its own sink file, or the corpus will be ambiguous exactly
where it needs to be precise.

Also note: `weaver-spu` must be built with `--features cuda,gguf` and installed
to `/usr/local/libexec/weaver/`. A plain workspace test run overwrites it with
a featureless binary, after which a load refuses with `DeviceCannotAdmit`.

## Operational traps on this machine

**Two GitHub identities.** The default `github.com` SSH host resolves to the
key for account `r3d91ll`. The `toddwbucy` account is reachable only through
the `github-toddwbucy` host alias in `~/.ssh/config`. A clone of a public repo
over the default host succeeds and then the push is denied, which reads as a
permissions problem rather than an identity one. This repo's remote is already
on the alias. Check `git remote -v` before diagnosing anything else.

**Storage.** `dbpool` is a mirror and is where durable things belong.
`bulk-store` is raidz1 plus a mirror and holds the model library at
`/bulk-store/models`, laid out as `publisher--model` directories.
`fastpool` is a deliberate two-drive stripe with no redundancy, used as a
staging tier for models in active use at `/opt/weaver/models`, and it holds
nothing meant to be kept. `/` is XFS on a single drive.

**Models enter through the script.** `/bulk-store/models/download-models.sh`
takes HuggingFace `OWNER/MODEL` ids and reads `models.txt` as the persistent
set. A model fetched any other way is outside the reproducible set. The `hf`
CLI is installed per-user via `uv`, so it is not on PATH under `sudo`.

## Suggested first moves

1. Stand up one FastAPI backend on 8000 and confirm an agent can create a
   character and take an action. Nothing else is testable until that works.
2. Decide what a benchmark pass writes and where, given the run-identity
   defect above.
3. Only then look at the viewer. Its value is the interaction design, and
   reworking its loader is worth doing once there is a real corpus to point
   it at.

## What is out of scope here

WeaverTools' corpus governance does not reach this repository. There is no H1
gate, no ratified document set, and no phase discipline here. Write ordinary
code with ordinary commits. The one discipline that does apply is the `weaver/`
segregation rule above, and recording any behavioural divergence in the
README's "Differences from upstream" section.
