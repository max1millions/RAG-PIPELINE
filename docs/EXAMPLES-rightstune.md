# RightsTune Deployment (Portfolio Context)

Built for [RightsTune](https://rightstune.com) (CWR, CIS-Net, MUSO API, local MySQL). This public repo documents **one real production deployment** — not a reusable framework or overlay kit for other teams. Product names in prompts and eval cases are intentional; they show how Orion is actually operated.

## RightsTune-specific vs generic


| In overlay / examples                        | In public framework code                       |
| -------------------------------------------- | ---------------------------------------------- |
| 17 SQL watchdog checks, MCP `rightstune`     | Watchdog runner, incident FSM, notify backends |
| REPOS (CWR-INTERFACE, CIS-NET-AUTOMATION, …) | RAG indexers, retrieve, orion-fix              |
| Production MySQL dump, CWR golden files      | `db/provision.sql`, synthetic golden manifest  |
| `rag/eval_cases.example.yaml` queries        | Eval CLI                                       |


## Production layout

- **Orion:** Linux on Mac Mini (OpenClaw VM)
- **Pipelines:** macOS production host via Tailscale MCP
- **Local web:** nginx + php-fpm (not Docker)
- **Alerts:** BlueBubbles/iMessage when `notify_backend: bluebubbles` in overlay

Host-specific ops: `$ORION_OVERLAY_ROOT/OPS.md` (private overlay, not in this repo).

## Deep research reports

Long-form architecture research for the autonomous supervisor and local testing stack lives in `[Deep Research Reports/](./Deep%20Research%20Reports/)`. 

## Related

- [DECISIONS.md §6](./DECISIONS.md#6-rightstune-as-reference-deployment)
- [ARCHITECTURE.md](./ARCHITECTURE.md)

