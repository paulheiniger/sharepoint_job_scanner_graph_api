# Zapier handoff pattern

Use Microsoft Graph for SharePoint scanning and extraction. Use Zapier for the actions around the extracted job record.

## Recommended Zapier actions

### Teams

- Send a daily digest to an operations channel.
- Send alerts when a job has an invoice but no signed contract, or estimate and invoice amounts disagree.

### QuickBooks Online

- Search/create customer.
- Create estimate or invoice from approved records.
- Update invoice status when a job is marked complete.

### CompanyCam

- Create a project when a new job folder appears.
- Add project labels such as `Estimated`, `Contracted`, `Invoiced`, `Needs Review`.
- Add extracted summary to the CompanyCam project notepad.

## Payload generation

After scanning:

```bash
python -m jobscan.zapier_payloads output/job_index.json \
  --digest output/teams_digest.md \
  --payload output/zapier_payload.json
```

The digest is ready to paste/post to Teams. The JSON payload is compact enough to send through a Zapier webhook or to feed into a custom Zapier MCP Skill.

## Suggested first automation

1. Run the Graph scanner nightly or on demand.
2. Generate `teams_digest.md`.
3. Zapier posts the digest to Teams.
4. A human reviews exceptions.
5. Only after that, automate QuickBooks writes.

Do not start by auto-creating invoices from unreviewed estimate files. Let the scanner prove reliability first.
