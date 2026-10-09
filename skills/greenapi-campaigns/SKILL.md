---
name: greenapi-campaigns
description: Manage the Property Connect GREEN-API WhatsApp queue through Codex, importing Excel or CSV contacts, reviewing personalized messages, scheduling campaigns and reporting actual sending results.
---

Use the repository's `backend/bridge.py` and email/password-authenticated `backend/server.py` API. Set `GREENAPI_DATA_DIR` to the persistent private queue directory; default location next to the bridge is only for local development. Read the repository README before deployment or migration.

Create a draft from the user's actual contact file and text. Inspect the full preview before activation. Activate only the specified campaign, recipients and text authorized by the user; general setup does not authorize contacting clients. Existing accepted messages must not be recreated for retries. On VPS operate on the single server queue; do not start a second local queue for the same account.

The state accepted is API acceptance, not delivery. Use `/api/message-status` to query the saved provider ID. Unknown results remain uncertain and block campaign activation until reviewed; never reset uncertain to pending without reconciliation. Inbound replies and unread counts are currently unavailable; do not infer them or populate with demo values.

Never print or commit secrets.json, .env, queue.sqlite, contact files, message logs or complete credential URLs. Store only source and templates on GitHub. Use the existing persistent database and a backup during migration. Stop the Mac scheduler before enabling the VPS worker. Verify one executor, daily cap, persistent volume, HTTPS and dashboard authentication before activating a campaign.
