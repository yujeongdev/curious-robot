# Security policy

## Supported version

Security fixes target the current `main` branch and latest GitHub release.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not open a public issue for a suspected secret leak, path traversal, unsafe
artifact access, command injection, or authority-boundary bypass.

Include the affected commit, reproduction steps, impact, and any proposed
mitigation. You should receive an initial response within seven days.

## Deployment boundary

Agent Lab is a local research application. It binds to `127.0.0.1` by default
and does not provide authentication, tenant isolation, or internet-facing
hardening. Do not expose it to an untrusted network.

The hardware interfaces are fail-closed integration seams. This repository
does not authorize unattended robot operation; operators remain responsible
for vendor safety requirements, workspace controls, and emergency stops.
