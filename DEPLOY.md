# Leaf & Loom — Live deployment (task t_bcf37257)

## Public URL (live)
- Storefront:  https://lexxautomates.github.io/leaf-loom-site/
- Privacy:     https://lexxautomates.github.io/leaf-loom-site/privacy.html
- Terms:       https://lexxautomates.github.io/leaf-loom-site/terms.html
- Repo:        https://github.com/lexxautomates/leaf-loom-site  (public, GitHub Pages from `main` `/`)

## Architecture (zero paid dependencies, fully free)
- **Front-end:** static HTML/CSS/JS on GitHub Pages (durable, public, free).
  - `index.html` — landing + email capture form (consent checkbox ships UNCHECKED).
  - `privacy.html`, `terms.html` — compliance pages (legal text from t_07d2d2e8; has
    bracketed placeholders the human must fill: [CONTROLLER NAME], [FULL POSTAL ADDRESS],
    [PRIVACY@LEAFANDLOOM.COM]).
  - `config.js` — holds `window.POD_ENDPOINT` (the capture backend URL). Only file to
    change when the backend host changes.
- **Capture backend:** `app.py` (Python stdlib only) — POST /subscribe, GET /pixel.gif,
  SQLite lead store, ESP forward. Hardened for public deploy: CORS for the Pages origin +
  server-side GDPR consent enforcement (rejects without explicit consent).
- **Live tunnel (now):** localhost.run free SSH tunnel → https://47c998f19f7ad2.lhr.life
  (forwards to local :8000). ESP mode = `mock` (logs leads, proves pipeline).

## Verified working (this run)
- Pages index / privacy / terms all HTTP 200.
- Public POST /subscribe with consent → `{ok:true}`, lead written to SQLite.
- Public POST without consent → 400 `consent_required` (GDPR server-side).
- Public POST invalid email → 400 `invalid_email`.
- CORS preflight returns `Access-Control-Allow-Origin: https://lexxautomates.github.io`.
- Pageview pixel + /health publicly 200.
- Consent checkbox present and UNCHECKED in served HTML; Privacy + Terms links resolve.

## To make capture permanent (human / follow-up)
1. The localhost.run tunnel is EPHEMERAL — it dies when this machine sleeps/reboots.
   Swap `app.py` to a free always-on host (no code change, just deploy + set env):
   - Render:        free web service (sleeps after 15m idle) — https://render.com
   - PythonAnywhere: free tier custom WSGI — https://www.pythonanywhere.com
   Then set `window.POD_ENDPOINT` in config.js to the new host's /subscribe URL and re-push.
2. Real confirmation emails: paste a free ESP key, no code change:
   - Mailchimp (500 contacts free):  `ESP_TARGET=mailchimp MAILCHIMP_API_KEY=... MAILCHIMP_LIST_ID=... python app.py`
   - Kit/ConvertKit (1000 subs free): `ESP_TARGET=convertkit CONVERTKIT_FORM_ID=... python app.py`
   Until then, `mock` mode stores leads + logs them; no email is sent to the human.
3. Fill the bracketed legal placeholders in privacy.html / terms.html and have an
   attorney review (text is a DRAFT, not legal advice — see t_07d2d2e8).

## Local re-run
```
python mock_esp.py          # terminal 1
ESP_TARGET=mock ALLOWED_ORIGIN="https://lexxautomates.github.io" python app.py   # terminal 2 (port 8000)
ssh -o StrictHostKeyChecking=no -R 80:localhost:8000 nokey@localhost.run          # terminal 3 (tunnel)
```
