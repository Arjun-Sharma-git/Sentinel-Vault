# Platform Architecture Overview

## Services
The platform runs 14 services on Kubernetes. The payments service is written in Go and talks to Postgres 17 through PgBouncer.

## Deployments
Every merge to main deploys to staging. Production deploys happen through canary releases: 5% of traffic for 30 minutes, then 50%, then 100%.

## On-call
Each team keeps a weekly on-call rotation. Pages must be acknowledged within 15 minutes.
