---
title: Each installer owns their own GitHub App
status: accepted
project: credential-broker
updated: 2026-11-03
---

# Each installer owns their own GitHub App

**Context.** The App should work for any user or organization, each pointing it at their own
broker, without creating the App by hand. A GitHub App has one webhook URL and one private key.
The key mints installation tokens up to the App's ceiling on every installation, and the key
owner alone can use it.

**Decision.** There is no shared App and no shared key. Every installer registers their own App
through GitHub's manifest flow, driven by `scripts/github-app-register`. The broker URL is an input
to that registration and defaults to `localhost`. A public URL sets the webhook, the Setup URL and the
events in the manifest. GitHub refuses a non-public webhook URL, so a `localhost` URL registers the App
without them, to be added once the broker has a public address. The installer owns the App, holds its key, and runs their own broker. The App built for
the maintainer's accounts is one such App.

**Consequences.**
- A key leak or a broker compromise reaches only that installer's repos, not everyone's.
- Registration costs one click per installer, and each installer runs a broker.
- Changing the broker later needs two edits: the webhook URL (`scripts/github-app-webhook`) and
  the Setup URL (the App's General page, no API).
- The helper runs on the installer's machine. A hosted registration page uses the same manifest
  flow but needs a public host; it is not built.
- An App is private by default and installs only on its owner. Set `--public` to install it on
  other accounts, or register it under the organization with `--org`.

**Set aside.** One shared public App with a hosted multi-tenant broker [^shared]: a single
install, but the maintainer holds write access to every installer's repos, and a relay must
forward webhooks to each installer's URL.

[^shared]: Rejected on key custody. The App's write ceiling on every installer's repos would sit
    behind one operator's key, which contradicts the broker's goal of holding keys away from runners.
