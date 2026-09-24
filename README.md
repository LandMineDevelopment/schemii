# Schemii desktop and mobile usability audit — September 23, 2026

Audit of the running authenticated application, not a claim that the checked-out source was rebuilt. Local `https://localhost:8001/login` and the private Tailscale HTTPS login route both returned 200. Deployed account administration differs from the checkout (`aba9cd40644969757209d23248bebc76bbe228a2`); static asset fingerprints are in [verified-metrics.json](verified-metrics.json).

Chromium: desktop 1440×900 and mobile 390×844 (touch emulation for product workflows); additional 320px checks for sign-in/account navigation. These are browser layout/emulation checks, not tests on physical iOS/Android hardware. Screenshots are actual app captures; no proposed fix is depicted as implemented.

[Audit findings and coverage](REPORT.md) · [Screenshot gallery](GALLERY.md) · [Administration/maps notes](admin-maps-findings.md) · [Semantic-model notes](model-notes.md) · [Dashboard notes](report-findings.md)

No application source changes, migrations, credential changes, role changes or paid AI requests were made. Two temporary schema workspaces, six model copies and two dashboards were removed after the workflow checks. Existing user objects were preserved. Read-only queries add ordinary query-history records. This evidence branch is archival and does not require a code PR or deployment.
