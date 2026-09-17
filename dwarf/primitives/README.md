# DWARF Primitive Catalog

`registry.json` is the canonical name-to-executor contract for every primitive
available to a DWARF scenario. Each record declares one of six families, its
parameter schema, supported runtimes and node implementations, executor module
and class, and primitive version. The JSON schemas live under the matching
family directories in this folder.

The complete rendered inventory is available at `/operate/primitives`; the
contract and authoring lifecycle are documented at `/learn/primitives` and in
`dwarf/docs/primitives-reference.md`.

Registration, a parseable schema, and a loadable executor establish catalog
validity. They do not prove runtime behavior. That claim requires a real
scenario run and retained evidence. Adding a primitive therefore requires
implementation, tests, registry merge, documentation regeneration, image
rebuild/deployment, and end-to-end exercise.
