---
name: codex-reset-install
description: Install or resume Codex reset alerts from the reviewed Release package without cloning the repository. Use for this monitor's deployment and setup diagnostics.
---

Use the bundled installation core; do not reconstruct OAuth, fork or Secret upload flows.

1. Explain the remaining official user actions: fork if needed, install the real GitHub App on only the selected repository, device authorization, Feishu app authorization and permission/publication/self-scope verification, and final target confirmation. No real installer GitHub App is bundled: its public client_id and slug must be provided.
2. Run `python3 scripts/launch.py` only for the offline plan. Inspect non-secret progress after a failure; never request tokens, App Secret, device_code, refresh tokens, or full authentication logs in chat.
3. Give the user the command `python3 scripts/launch.py --run --client-id PUBLIC_ID --app-slug PUBLIC_SLUG`. The user runs it in their own interactive terminal and performs sensitive authorization/confirmation there. Do not run the live wizard through agent tools, capture its terminal output, or fill the confirmations on the user's behalf.
4. Use the core's validated resume operations. A possibly completed dispatch or Secret upload must be inspected on GitHub before retrying. Do not recreate an app after interruption, overwrite an unrelated same-name repository, clear the ledger, fill SEND_TEST/RECEIVED/ENABLE confirmations for the user, or expand permissions to bypass a failure.

Release CLI and this Skill invoke the same `.pyz`. Verify the reviewed release SHA256SUMS before running it. The build packages only an explicit file allowlist; it includes no runtime state, credential file, GitHub App private key or client_secret. A successful safe preview proves checks ran, not Feishu delivery or permanent scheduler availability.

The live user wizard defaults to creating a new Feishu app through the official confirmation page; explicit reuse requires only its public App ID. After safe preview, the user may personally authorize one installation test, confirm the matching request ID was received, and explicitly enable monitoring. Agents must not execute these real actions. Unknown delivery requires inspection and never a blind retry.
