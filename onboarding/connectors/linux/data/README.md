# `data/`: no feed file for the `linux` connector

Linux sources have no feed file: ISC (or its virtual appliance) reads the target system
directly after the source is created, so nothing is uploaded and nothing goes here.

The folder exists on purpose so that every connector has the same `apps/` + `data/` shape, which the layout tests and
the documentation rely on. Do not delete it.

If a future requirement adds a file for this connector, follow the pattern of [`../../delimited/data/`](../../delimited/data/README.md):
`sandbox/` and `production/`, `{env}` in the app YAML, and `production/*.csv` kept out of git.
Connector behaviour: [`../README.md`](../README.md).
