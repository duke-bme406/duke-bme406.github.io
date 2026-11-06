# BME406 Ordering App guide

Student instructions for the BME406 Biotech Design class ordering app, published with GitHub Pages.

- `ordering/index.html` is the instructions page (https://duke-bme406.github.io/ordering/).
- `index.html` at the top level just forwards to `/ordering/`.
- `ordering-app/index.html` sends visitors straight to the live app (the Apps Script `/exec` URL). Put this
  page's address in the app under Settings > Branding and app link > Public app URL, so the buttons in
  the app's emails and Slack messages use the short link.
- `.nojekyll` tells GitHub Pages to serve the files as they are.

To change the instructions, edit `ordering/index.html` on GitHub (pencil icon) and commit. The site updates in
about a minute. If the app's deployment URL ever changes, update the two places it appears in
`ordering-app/index.html`.

This repository contains no app code. Other labs get the app by copying the template spreadsheet.
