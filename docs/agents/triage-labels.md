# Triage Labels

The skills use canonical triage roles. The default local contract maps those
roles to GitHub label strings by identity in the target repository.

| Canonical role | Live GitHub label |
| --- | --- |
| `needs-triage` | `needs-triage` |
| `needs-info` | `needs-info` |
| `ready-for-agent` | `ready-for-agent` |
| `ready-for-human` | `ready-for-human` |
| `wontfix` | `wontfix` |

Use the live GitHub label exactly as written in the right-hand column. Label
descriptions and colors are informational and may change without changing this
contract. Before triaging in a checkout, verify these labels exist there with
`gh label list`. If any required label is missing, stop and ask the maintainer
to create or remap labels before changing issue state.

Every triaged issue should carry exactly one state role from this table. If an
issue has conflicting state labels, stop and ask the maintainer before changing
it.
