---
title: AGPL-3.0 with a commercial license
status: accepted
project: credential-broker
updated: 2026-11-03
---

# AGPL-3.0 with a commercial license

**Context.** The broker is a network service. Sandy, its sibling, is already dual-licensed.

**Decision.** AGPL-3.0-only for open use, plus a commercial license for use the AGPL does not
fit. Contributors grant the maintainer relicensing rights through the CLA in `CONTRIBUTING.md`.

**Consequences.**
- Anyone who runs a modified broker as a service must publish their changes.
- A commercial license needs the maintainer to hold rights in every contribution, so the CLA
  stays in force from the first outside pull request.

**Set aside.** A permissive license (Apache-2.0 or MIT) [^perm]: no CLA, but a hosted fork could
stay closed.

[^perm]: Rejected to match sandy and to keep the option of a commercial offer.
