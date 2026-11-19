# `docs/`: documentation and examples

Project documents that are not tied to one folder's code. Each connector and each code folder has its own README next to
it; start from the main [`README.md`](../README.md) for the overview.

| Path | What it is | Read it when |
|---|---|---|
| [`status.md`](status.md) | The schedule against reality: what is done, what is built but untested, what waits for the tenant. Update it when status changes. | you need to report progress or pick the next task |
| [`structure.md`](structure.md) | The repository layout and the decisions from the 4 Nov 2026 meeting behind it. | you wonder why a folder is where it is |
| [`validation-evidence.md`](validation-evidence.md) | The agreed checklist of screenshots to attach per onboarded application (run reference, account provisioning, correlation). Full SIT is out of scope. | you onboard an application on a real tenant |
| [`meeting-notes/`](meeting-notes/README.md) | Verbatim meeting notes. | you need the source of a decision |
| [`examples/`](examples/README.md) | A batch file, an apps CSV and sample feeds. Not read by the default run. | you want a worked example of an input |

## Rules
- `status.md` is a snapshot with a date; change the date when you change the content.
- Keep decisions in `structure.md` and the verbatim notes in `meeting-notes/`; do not rewrite the notes.
- Examples must contain made-up data only.
- Every link in these files is relative and is checked by `tests/test_docs.py`; run the tests after moving or renaming a file.
