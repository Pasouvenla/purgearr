# Third-party code bundled with this project

## htmx 1.9.10

- Files: `app/static/htmx-1.9.10.min.js`, `app/static/htmx-1.9.10.LICENSE.txt`
- Upstream: https://github.com/bigskysoftware/htmx
- Licence: BSD 2-Clause, Copyright (c) 2020 Big Sky Software. Full text in
  `app/static/htmx-1.9.10.LICENSE.txt`, unmodified.
- sha256: `b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e`
  (47755 bytes), verified byte-identical from two independent origins, unpkg
  and jsDelivr, before being committed. Vendored verbatim, not modified.

### Why it is vendored rather than loaded from a CDN

It used to be loaded from `https://unpkg.com/htmx.org@1.9.10`. That URL answers
with a **301 whose Content-Type is `text/plain`**. Chromium follows the redirect
and judges the final response, so it worked there; Safari applies the
`X-Content-Type-Options: nosniff` of the FIRST response and refuses outright:

    Refused to execute https://unpkg.com/htmx.org@1.9.10 as script because
    "X-Content-Type-Options: nosniff" was given and its Content-Type is not a
    script MIME type.

htmx is what issues the requests behind every `hx-*` attribute, so with it gone
no button emitted anything at all: measured on the reverse proxy, 4489 GET and
**zero POST** over 48 hours. The interface looked normal, since the styling
comes from a different source, and did nothing.

Pinning the full path would have been enough to fix that one symptom. Serving
the file ourselves removes the whole class: a third party cannot take the
interface down, nor see who uses it.
