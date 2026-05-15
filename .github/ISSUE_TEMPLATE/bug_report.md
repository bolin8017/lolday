---
name: Bug report
about: Report a reproducible defect in the platform
title: ""
labels: bug
assignees: ""
---

## Summary

One sentence describing the bug.

## Reproduction

Steps to reproduce the behaviour:

1.
2.
3.

## Expected vs. actual

- Expected:
- Actual:

## Environment

- Chart version (from `charts/lolday/Chart.yaml`):
- Backend image tag (from `kubectl get deployment -n lolday backend -o jsonpath='{.spec.template.spec.containers[0].image}'`):
- Browser + version (if frontend):

## Logs / screenshots

Paste any relevant logs (`kubectl logs ...` output, backend stack traces,
browser console messages). For long output, attach as a file or paste
into a `<details>` block.

## Additional context

Anything else that helps triage (recent changes, time the bug started,
similar past issues).
