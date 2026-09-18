---
name: servicenow-incident-enrichment-and-reassignment
description: Enrich and reassign a ServiceNow incident after the Azure SRE Agent has completed its investigation. Use this skill to write the evidence-based incident analysis to ServiceNow and move the ticket from {{SOURCE_QUEUE}} to {{DESTINATION_QUEUE}} through the snow-tickets-updater tool.
tools:
  - snow-tickets-updater
---

# ServiceNow Incident Enrichment and Reassignment

## When to use this skill

Use this skill only when all of the following conditions are true:

1. The Azure SRE Agent has completed the incident investigation or has a clearly identified best-supported diagnosis.
2. An evidence-based enrichment is ready to be written to the ServiceNow `u_enrichment_ai` field.
3. The ServiceNow incident number or `sys_id` is known.
4. The active Azure SRE Agent thread ID is available.
5. The incident is expected to be assigned to `{{SOURCE_QUEUE}}`.
6. The incident must be reassigned to `{{DESTINATION_QUEUE}}`.

Do not use this skill for investigation-only requests, speculative updates, or incidents that must remain in their current assignment group.

## Attached tool

Use the `snow-tickets-updater` Python tool.

The tool:

- reads ServiceNow OAuth ROPC credentials from Azure Key Vault by using the managed identity assigned to the Azure SRE Agent;
- authenticates to ServiceNow;
- retrieves exactly one incident by incident number or `sys_id`;
- verifies its current assignment group;
- appends the SRE Agent survey link to the enrichment;
- writes the enrichment and destination assignment group in the same ServiceNow external-update request;
- generates a new 32-character lowercase alphanumeric transaction ID for every update;
- emits structured JSONL diagnostic events in the thread trace without exposing credentials or enrichment content.
- NEVER call the tool using test parameters such as `FOR_TESTING`

## Configuration for this Azure SRE Agent

Always use these exact values:

| Tool parameter | Value |
|---|---|
| `snow_instance_name` | `{{SNOW_INSTANCE_NAME}}` |
| `key_vault_url` | `https://{{KEYVAULT_NAME}}.vault.azure.net/` |
| `source_system` | `{{SOURCE_SYSTEM}}` |
| `source_queue` | `{{SOURCE_QUEUE}}` |
| `destination_queue` | `{{DESTINATION_QUEUE}}` |

Do not infer, abbreviate, translate, or substitute any of these values. If this configuration does not match the active Azure SRE Agent environment, stop without invoking the tool and report `CONFIGURATION_MISMATCH`.

The tool environment may optionally provide:

- `SERVICENOW_HTTP_TIMEOUT_SECONDS`; otherwise the tool uses 30 seconds.

The Key Vault must contain enabled, non-empty secrets named:

- `clientid`
- `clientsecret`
- `username`
- `password`

Never request, display, repeat, or place the values of these secrets in an incident report, tool argument, trace summary, or user response.

## Required dynamic inputs

Resolve these values from the active incident and thread:

| Tool parameter | Source |
|---|---|
| `incident_id` | The ServiceNow incident number, such as `INC00101285`, or its 32-character `sys_id` |
| `agent_id` | The name of the agent resource |
| `sre_thread_id` | The current Azure SRE Agent thread ID |
| `enrichment_text` | The final evidence-based incident enrichment prepared from the investigation |
| `survey_system_url` | The configured absolute HTTP or HTTPS URL of the survey system |
| `azure_managed_identity_client_it` | The client ID of the user-assigned managed identity configured for the Azure SRE Agent |

Do not invent or guess a missing input. If one is unavailable, do not invoke the tool; report which input is missing.

## Enrichment requirements

Prepare concise HTML suitable for the ServiceNow `u_enrichment_ai` field. Include only claims supported by collected evidence.

When available, include:

1. Incident summary and customer or service impact.
2. Detection time, incident start time, mitigation time, and recovery time in UTC.
3. Affected Azure resources and components.
4. Important telemetry, request IDs, operation IDs, exceptions, or dependency failures.
5. Root cause, or the strongest supported hypothesis when the root cause is not proven.
6. Recurrence classification: `SPORADIC`, `INTERMITTENT`, `PERSISTENT`, or `DATA_GAP`.
7. Mitigation and recovery status.
8. MTTR for resolved incidents or MTTM for mitigated but unresolved incidents.
9. Recommended follow-up actions and remaining risks.

Do not include:

- passwords, OAuth tokens, client secrets, managed identity tokens, or Authorization headers;
- unsupported conclusions;
- raw high-volume logs;
- the survey URL, because the tool appends it automatically;
- a transaction ID, because the tool generates it automatically.

## Procedure

1. Confirm that the investigation is complete enough to produce an evidence-based enrichment.
2. Resolve the ServiceNow `incident_id`.
3. Resolve the current `sre_thread_id`; never reuse a thread ID from another incident.
4. Resolve the name of the agent `agent_id`. This is always the name of THIS agent azure resource, never use a name assigned by users.
4. Build `enrichment_text` according to the enrichment requirements.
5. Verify the fixed configuration values in this skill.
6. Invoke `snow-tickets-updater` exactly once with:

```yaml
snow_instance_name: {{SNOW_INSTANCE_NAME}}
incident_id: <ServiceNow incident number or sys_id>
agent_id: <current Azure SRE Agent resource name>
sre_thread_id: <current Azure SRE Agent thread ID>
enrichment_text: <evidence-based HTML enrichment>
survey_system_url: https://rigid-wood-036d3e4d7d-westeurope.webapp.fabricapps.net/
azure_managed_identity_client_it: {{AGENT_MANAGED_IDENTITY_CLIENT_ID}}
source_queue: {{SOURCE_QUEUE}}
destination_queue: {{DESTINATION_QUEUE}}
source_system: {{SOURCE_SYSTEM}}
key_vault_url: https://{{KEYVAULT_NAME}}.vault.azure.net/
```

7. Read the authoritative final JSON result. Diagnostic JSONL records contain an `event` property; the final result contains `status` and no `event` property.
8. Treat the operation as successful only when the final result has `status: REASSIGNED`.
9. Never infer success from tool activation, an HTTP attempt, or an intermediate trace event.

## Result handling

### Successful update

For `status: REASSIGNED`, report:

- the ServiceNow incident ID;
- that enrichment and reassignment were submitted in one operation;
- the verified source and destination queues;
- the source system;
- the generated transaction ID;
- the ServiceNow HTTP status;
- the Key Vault URL used, without any secret values.

### Blocked update

For `status: REASSIGNMENT_BLOCKED`, do not retry. Report:

- the error code and message;
- the current assignment group when returned;
- the expected source assignment group;
- that no update was attempted because the safety check failed.

`SOURCE_QUEUE_MISMATCH` usually means another operator or workflow already reassigned the incident. Respect the current ownership and do not force an update.

### Failed update

For `status: REASSIGNMENT_FAILED`, inspect:

- `error.code`
- `error.stage`
- `error.message`
- `error.retryable`
- `error.http_status`, when present
- `error.details`, when present

Use the structured thread-trace events with the same `run_id`, `incident_id`, and `sre_thread_id` to locate the failing stage and relevant request IDs.

## Error response guide

| Error code or family | Meaning | Agent action |
|---|---|---|
| `INVALID_ARGUMENTS`, `INVALID_CONFIGURATION`, `INVALID_INCIDENT_ID`, `INVALID_KEY_VAULT_URL` | A required value is missing or invalid | Correct the input or skill configuration; do not retry unchanged |
| `MANAGED_IDENTITY_CONFIGURATION_INVALID` | Managed identity configuration is invalid | Verify the agent identity configuration |
| `KEY_VAULT_AUTHENTICATION_FAILED` | The managed identity could not authenticate | Verify that managed identity is enabled and available to the tool |
| `KEY_VAULT_ACCESS_DENIED` | The identity lacks permission to read secrets | Grant the identity Key Vault secret-read access; do not expose credentials as a workaround |
| `KEY_VAULT_SECRET_NOT_FOUND`, `KEY_VAULT_SECRET_EMPTY` | A required ServiceNow secret is absent or empty | Correct the named secret in the configured vault |
| `KEY_VAULT_UNREACHABLE`, `KEY_VAULT_REQUEST_FAILED` | Key Vault could not be reached or returned an error | Use Azure request IDs from the trace; retry only when `retryable` is `true` |
| `SERVICENOW_AUTHENTICATION_FAILED`, `SERVICENOW_ACCESS_TOKEN_MISSING` | ServiceNow rejected ROPC authentication | Verify the four Key Vault secrets and ServiceNow OAuth configuration without revealing their values |
| `INCIDENT_NOT_FOUND` | No incident matched the supplied identifier | Verify the incident number or `sys_id` and ServiceNow instance |
| `INCIDENT_NOT_UNIQUE` | More than one incident matched | Stop and validate ServiceNow data; do not update |
| `ASSIGNMENT_GROUP_MISSING`, `INCIDENT_NUMBER_MISSING`, `INCIDENT_SYS_ID_MISSING` | ServiceNow returned incomplete incident data | Stop and report the malformed response |
| `SERVICENOW_INCIDENT_READ_FAILED*` | Incident read failed, timed out, or returned invalid JSON | Retry only when `retryable` is `true` |
| `SOURCE_QUEUE_MISMATCH` | The incident is no longer owned by the expected source queue | Do not retry or override ownership |
| `SERVICENOW_UPDATE_REJECTED` | ServiceNow explicitly rejected the update payload | Report the safe error details and correct the underlying data |
| `SERVICENOW_UPDATE_FAILED*` | The update failed, timed out, or had an ambiguous response | Do not automatically retry; first verify the current incident state |
| `UNEXPECTED_ERROR` | An unclassified tool failure occurred | Use the trace `run_id`, stage, and safe traceback frames for troubleshooting |

An error family ending in `_NETWORK` or `_TIMEOUT` is normally transient, but an update-stage network error or timeout is ambiguous because ServiceNow may have committed the request before the connection failed.

## Retry safety

- Never retry `REASSIGNMENT_BLOCKED`.
- Never retry a validation, missing-secret, authentication, authorization, or malformed-data failure without correcting its cause.
- A failure before `incident_update` may be retried only when `error.retryable` is `true`.
- Do not automatically retry any `incident_update` timeout, network failure, HTTP 5xx response, or other ambiguous update result.
- Before considering another update attempt, retrieve or verify the current ServiceNow incident state. If the incident is already assigned to `{{DESTINATION_QUEUE}}`, treat the previous operation as potentially successful and do not invoke the updater again.
- If a retry is explicitly approved after state verification, invoke the tool once. The new invocation generates a different transaction ID.

## Trace troubleshooting

Use the JSONL trace in chronological order:

1. Find `tool_started` and note its `run_id`.
2. Confirm `configuration_validated`.
3. Review managed identity and Key Vault events.
4. Review each `http_request_started`, `http_request_completed`, or `http_request_failed` event.
5. Correlate `x-request-id`, `x-ms-request-id`, `x-correlation-id`, or `x-transaction-id` with ServiceNow or Azure platform diagnostics.
6. Find `source_queue_checked` to confirm whether the safety gate passed.
7. Find `update_payload_prepared` and record its generated transaction ID. The enrichment content is intentionally not logged.
8. Find `external_update_accepted` and `tool_completed` for a successful path.
9. For a failure, use `tool_failed` or `tool_failed_unexpectedly` and the final structured error.

The absence of `tool_completed` means the operation did not reach a confirmed successful result.

## Expected response

Return a concise incident-update summary in this form:

```text
Incident: <incident number>
Status: REASSIGNED | REASSIGNMENT_BLOCKED | REASSIGNMENT_FAILED
Source queue: <verified current queue or unavailable>
Destination queue: {{DESTINATION_QUEUE}}
Source system: {{SOURCE_SYSTEM}}
Transaction ID: <generated ID or unavailable>
HTTP status: <status or unavailable>
Details: <short non-sensitive explanation>
```

Never include ServiceNow credentials, Key Vault secret values, OAuth tokens, Authorization headers, or the full enrichment body in the response.
