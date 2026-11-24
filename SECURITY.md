# Security

## Reporting a vulnerability

Please don't open a public issue. Report it privately through GitHub:
**Security > Report a vulnerability** on this repository. You'll get a reply
there.

## What's in scope

- STORM's own code: the app, the headless publisher and the scripts.
- Anything that leaks a broker password, API key or client certificate,
  or lets a read-only login publish.

## Good to know

- Secrets live in `.env` and certificates in `certs/`; neither is in the repo.
- The NSSL viewer certificate in `providers/nssl/certs/viewer/` is committed
  on purpose: its broker policy is subscribe-only on `storm/*`.
- Access to a team's data is enforced by its broker and APIs, not by the
  app. Anyone can read and change this code, so a check that only exists in
  the app isn't a security boundary.
