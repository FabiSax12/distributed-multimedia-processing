# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Distributed multimedia processing platform. Clients submit
"cases" (batches of media files); the system decomposes each file into one or more sub-tasks (e.g. a
video produces a convert + audio-extract + thumbnail sub-task), fans them out across pools of EC2
workers by media type, and a single coordinator tracks completion via a barrier pattern until the case
closes and a consolidated report is written to S3.

Code and comments in this repo are in Spanish; follow that convention when editing existing files.

## Current state

Most feature directories are scaffolding only (`.gitkeep` placeholders, no code yet):
`coordinator/`, `workers/`, `dashboard/`, `client/`, `dataset/`, `docs/adr/`. Only `shared/` has real
code — the Pydantic contracts (messages, models, routing, state ordering) that every other component
will depend on. The Terraform infra is fully written and applies real AWS resources, but the EC2
`user_data` templates stop at provisioning: they install Python (via `uv`) and ffmpeg but leave a
`TODO` where the application would be cloned and started as a systemd unit — app deployment is not
wired up yet.

The root `README.md` directory tree is aspirational/partially stale (it still shows `infra/bootstrap/`
and `infra/envs/dev/`). The infra was deliberately flattened to a single `infra/` directory with local
Terraform state (no remote backend, no bootstrap step) — `infra/README.md` reflects the current layout
and is the source of truth for infra.

## Commands

No Python project files (`pyproject.toml`, `requirements.txt`) or test suite exist yet for
`shared/`/`coordinator/`/`workers/`. EC2 instances provision Python 3.12 via `uv` and ffmpeg via a
static build (`johnvansickle.com/ffmpeg`), so the application is expected to use `uv` once it exists.

Infra (from `infra/`):

```bash
cd infra
terraform init
terraform plan  -var="alert_email=tu-correo@ejemplo.com"
terraform apply -var="alert_email=tu-correo@ejemplo.com"
```

`alert_email` has no default (budget alarm recipient) — always pass it explicitly.

Scale a worker pool (video/audio/metadatos) without touching other resources:

```bash
terraform apply -var='worker_count={video=3,audio=1,metadatos=1}' -var="alert_email=..."
```

Instances have no SSH key pair or open port 22 — access is only via SSM:

```bash
aws ssm start-session --target <instance-id>   # IDs from: terraform output instance_ids
```

## Architecture

**Domain contracts (`shared/`)** — the schema every other component must agree with:

- `routing.py` — the file-extension → `MediaType` → `Operation` → `Pool` → queue-name table. This is
  the single mapping that Terraform's queue names (`modules/queues`), worker pool assignment
  (`modules/compute`), and the coordinator's task planning must all stay consistent with. Also defines
  `POLL_ORDER`: each worker pool's queue priority order, including which *other* pools' normal-priority
  queue it helps drain when its own queues are empty (cross-pool load help).
- `states.py` — `SubTaskStatus`/`CaseStatus` plus `state_order()`. SQS Standard queues can deliver
  out-of-order/duplicate messages, so every status update carries a monotonic order number
  (`attempt * 10 + rank`, terminal states pinned to a max) and DynamoDB writes are conditioned on
  `state_order < :new` so a late "running" from an earlier attempt can never overwrite a newer state.
- `messages.py` — wire format for the two SQS message types: `SubTaskMessage` (coordinator → work
  queues) and `ResultMessage` (worker → results queue, one per status change). Workers only ever
  produce `ResultMessage`s; **the coordinator is the sole writer of `Cases`/`SubTasks` state**.
- `models.py` — DynamoDB item shapes (`CaseItem`, `SubTaskItem`, `WorkerItem`), the case barrier
  contract (`pending_count` decremented per finished sub-task; `closed` string-set attribute lives only
  in DynamoDB, not in the Pydantic model, because DynamoDB can't initialize an empty set), the
  consolidated `CaseReport`, and S3 key helpers (`uploads/...`, `results/{case_id}/{subtask_id}/...`,
  `results/{case_id}/report.json`, `metrics/{day}.jsonl`).
- `api.py` — REST contracts for the coordinator's `/api/*` endpoints, meant to be the source FastAPI
  generates OpenAPI from, which the dashboard in turn types via `openapi-typescript`.

**Coordinator vs. workers split**: the coordinator owns all case/sub-task state and orchestration
(routing incoming files into sub-tasks, the completion barrier, consolidated reports, queue-depth
monitoring for scaling decisions). Workers are one shared codebase, with pool (`video`/`audio`/
`metadatos`) selected via the `POOL` env var at deploy time — they pull from their pools's queues,
process, and report results; they never touch `Cases`/`SubTasks` directly, including on failure (a
message that exhausts its SQS redrive count lands in the DLQ, which the coordinator itself consumes to
record the failure via `result_from_dlq()`).

**Infra (`infra/`)** — four Terraform modules composed in `infra/main.tf`:

- `modules/queues` — 6 work queues (`{video,audio,metadatos}-{alta,normal}`) + 1 results queue + 1 DLQ
  with redrive. The DLQ's `redrive_allow_policy` references the work queues' ARNs computed from SQS's
  deterministic ARN format (not the resource attribute) specifically to avoid a resource cycle with the
  work queues' own `redrive_policy`.
- `modules/data` — 3 DynamoDB tables (`Cases`/`SubTasks`/`Workers`, all `PAY_PER_REQUEST`) + dataset/
  results S3 buckets (private, SSE-S3, `force_destroy = true` since this is a student project that
  needs to tear down cleanly).
- `modules/compute` — 1 coordinator EC2 instance (with Elastic IP) + 3 worker pools scaled
  independently via `var.worker_count.{video,audio,metadatos}`. AMI is always resolved from the public
  SSM parameter for latest Amazon Linux 2023, never hardcoded. IAM instance profiles grant only
  `AmazonSSMManagedInstanceCore` plus the specific queue/table/bucket permissions needed — no SSH.
- `modules/frontend` — S3 site bucket + CloudFront, proxying `/api/*` to the coordinator's public DNS.

A few resources live in `infra/main.tf` itself rather than inside a module, specifically to break
circular module dependencies (each side needs an output from the other): the CORS rules on the
dataset/results buckets (need CloudFront's domain, which needs the frontend module, which needs
`modules/data`'s bucket first), and the `X-Origin-Verify` shared secret (frontend needs compute's DNS;
compute needs the secret). See "Notas de diseño" in `infra/README.md` for the full reasoning. That
same file also generates a gitignored `../.env.local` (via `local_sensitive_file`) with the same env
vars the EC2 instances get, for running the coordinator/workers from a laptop against real AWS
resources.
