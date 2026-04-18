# tokenpak.ai — static site

Minimal static site for tokenpak.ai. One `index.html`, one `pricing.html`, and a shared CSS file. Zero JavaScript, zero build step.

## Files

```
site/
  index.html        — landing page (hero, demo, pricing summary, support)
  pricing.html      — full pricing + feature comparison table
  assets/style.css  — shared stylesheet
  favicon.ico       — 16×16 ICO, generated from script (see below)
  README.md         — this file
```

## Deploy

The site is pure static HTML. Drop the `site/` directory onto any static host:

### GitHub Pages

```bash
# From repo root, push site/ to gh-pages branch:
git subtree push --prefix site origin gh-pages
```

Then configure the custom domain `tokenpak.ai` in GitHub Pages settings and set DNS:

```
A     @    185.199.108.153
A     @    185.199.109.153
A     @    185.199.110.153
A     @    185.199.111.153
CNAME www  tokenpak.github.io
```

### Netlify / Vercel static

Set **publish directory** to `site/` in the dashboard. No build command needed.

### Manual / VPS

```bash
rsync -av site/ user@host:/var/www/tokenpak.ai/
```

## DNS

Kevin must confirm the DNS target and host before publishing. Current placeholder host: GitHub Pages (`tokenpak/tokenpak` repo, post-transfer).

## Updating favicon

Regenerate with the embedded Python snippet in `gtm-06-landing-page-v1.md` (Cali submission notes). Requires Python 3 only, no third-party libraries.

## Constraints

- Zero JavaScript
- No external fonts or CDN assets
- Total payload target: &lt;200KB (current: ~12KB)
- HTML validates at https://validator.w3.org/
