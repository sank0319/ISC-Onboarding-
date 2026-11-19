# `docs/meeting-notes/`: source notes

Verbatim notes from project meetings. They are the evidence behind the decisions recorded in
[`../structure.md`](../structure.md); the decisions are summarized there and applied to the layout.

| File | Meeting | Subject |
|---|---|---|
| [`2026-11-04-utility-structure-and-migration.md`](2026-11-04-utility-structure-and-migration.md) | 4 Nov 2026 | utility structure, feed file management and migration utilities |

## Rules
- Add a new note as `YYYY-MM-DD-<topic>.md` and list it in the table above.
- Do not edit a note after the fact. Record new decisions in `structure.md` and link back to the note.
- The notes describe the server-side migration utilities; this repository is the CI/CD pipeline, so only the parts that
  shape app configs, feed files and pipeline inputs are applied (see `structure.md`).
