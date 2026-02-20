# Image Handling Findings

This directory contains findings from the [Image Handling Audit](../image-handling-audit.md).

One file per completed ticket: `ticket-N-findings.md`.

## Template

Use this structure when depositing findings. Copy the block below into `ticket-N-findings.md`.

---

```markdown
# Ticket N: [Title]

**Investigator:** [agent/person]
**Date:** YYYY-MM-DD
**Status:** Complete

## Summary

One paragraph: what was found, how it compares to the initial severity estimate.

## Observations

### [Question 1 from the ticket]

[Answer with evidence: code references, measurements, log excerpts.]

### [Question 2 from the ticket]

[Answer with evidence.]

### [... repeat for each question in the ticket]

## Data

Any measurements, counts, or metrics collected during investigation.

| Metric | Value |
|--------|-------|
| ... | ... |

## Recommendations

Numbered list of concrete actions, each with effort estimate.

1. **[Action]** — [description]. Effort: [hours/day/days].
2. ...

## Severity Reassessment

| Criterion | Initial | Reassessed | Notes |
|-----------|---------|------------|-------|
| Severity | [from ticket] | [your assessment] | |
| Frequency | — | [every run / occasional / rare / theoretical] | |
| Product Impact | — | [Marcel notices / silent degradation / no impact] | |
| Fix Effort | [from ticket] | [hours / day / days] | |
| Fix Benefit | — | [critical / worthwhile / marginal / skip] | |
```

## Completed Tickets

_None yet._
