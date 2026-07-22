# BuyerReach V1 Architecture

## V1 Product Boundary

V1 implements the trusted data loop:

1. Create search task
2. Discover or import brands
3. Confirm website candidates
4. Parse public website evidence
5. Discover contacts
6. Discover or infer emails
7. Verify and score emails
8. Deduplicate brands, contacts, and emails
9. Manual review
10. Valid email pool
11. Assign, export, or provide data to later systems

V1 does not implement bulk outreach, email warmup, full CRM, LinkedIn login automation, or AI personalization.

## Service Topology

- `frontend`: Vue 3 + TypeScript + Vite + Element Plus
- `backend`: FastAPI REST API
- `postgres`: core business database
- `redis`: task broker and cache
- `celery-worker`: async task execution
- `celery-beat`: scheduled jobs such as re-verification
- `minio`: evidence and import/export file storage
- `n8n`: orchestration and notification workflows, not core state
- `email-verifier`: portable stateless Go service for local syntax, MX, SMTP and catch-all verification; accessed only through the versioned `aftership_local_v1` Provider adapter

The local verifier is optional. Its `disabled`, `shadow` and `active` modes are
snapshotted with each task. Any unavailable or inconclusive local result falls
through the existing ZeroBounce/Hunter waterfall. Redis coordinates cache,
duplicate suppression and domain concurrency across verifier replicas.

## Module Boundaries

- Identity: users, roles, permissions, data scope
- Brand: companies, brands, websites, source evidence
- Contact: people, positions, brand/company relationship
- Email: email address, status, score, pool, blacklist
- Discovery: search tasks, task items, progress
- Provider: replaceable third-party adapters
- Audit: operation and security logs
- ImportExport: CSV/XLSX import and export task boundary
- Notification: Feishu/Webhook provider boundary

## Provider Contract

Every external service must map its response into a standard result object before touching core services:

- `CompanySearchProvider.search_companies(criteria)`
- `ContactSearchProvider.search_contacts(company, titles, limit)`
- `EmailFinderProvider.find_emails(contact, domain)`
- `EmailVerifierProvider.verify_email(address)`
- `NotificationProvider.send(event, recipients, payload)`

Provider-specific fields stay in raw payload snapshots or source evidence, not in core tables.

## Event Model

V1 emits domain events such as:

- `brand.created`
- `website.verified`
- `contact.discovered`
- `email.discovered`
- `email.verified`
- `duplicate.detected`
- `task.completed`
- `provider.quota_low`

Future V1.5/V2 modules should subscribe to events or call APIs instead of bypassing services.
