## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Project docs

- Human-facing overview (goals, architecture, data pack, endpoints, dev setup): `readme.md`
- Living TODO + detailed spec of the milestone in flight: `TODO.md` — check it at the start of a work session and tick items off as they land.

## Project conventions

- All compounding math lives in `app/compute.py`; the `/compute` endpoint calls `plan_compound(input_data, rules)`. Domain failures are raised as `DomainError` and converted to HTTP 422 with structured detail — never let them surface as 500s.
- Keep compute functions pure: no hidden global state, inputs injected explicitly.
- **No PHI persistence.** Patient fields exist on `ComputeInput` for PDF output only (`repr=False` to keep them out of logs) and must never appear in `ComputeOutput`, JSON responses, or log lines.
- `app/compute_request.py` is a legacy prototype, not wired to the API and out of sync with current rule models — do not extend it; the active path is `app/compute.py`.
- Rules are data: anything that can change without a code change belongs in the YAML pack under `rules/`, validated by `app/rules_loader.py` (intrinsic checks + cross-file checks).
- YAML IDs are `UPPER_SNAKE` and stable; names are human-readable.
- Start new functions with a short `SPEC:` comment (inputs, outputs, edge cases). Use TODO tags for follow-ups, e.g. `# TODO(M3): auto-upsize container`.
- Prefer **pytest** for tests; curl only for quick manual smoke checks.
- Do not invent dependencies; stick to what's in `requirements.txt`.

## Working with this user

The user is a newly graduated software engineer using this project to learn. Explain the reasoning behind changes and design choices; prefer small incremental steps over large code dumps.
