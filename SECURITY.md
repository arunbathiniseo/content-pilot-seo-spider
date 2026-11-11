# Security

Content Pilot Spider runs on your own computer, holds your API keys in `settings.json`, and loads pages from
websites it does not control. Reports of anything that lets a crawled site or a web page reach the app, its
settings or your files are very welcome.

## Reporting a vulnerability

Please do not open a public issue. Use **Security, Report a vulnerability** on this repository (GitHub private
vulnerability reporting). Include the version or commit, what you did and what happened.

## How the desktop app is protected

- It listens on `127.0.0.1` only.
- It answers only requests whose `Host` is this computer (`127.0.0.1`, `localhost`), which blocks DNS rebinding.
  `SPIDER_ALLOWED_HOSTS` adds host names when you publish it through your own tunnel.
- Writes from another origin are refused, so a page open in your browser cannot change settings or start work.
- Text from crawled sites is escaped in the interface and in the emails, and is never written as a formula in
  CSV or XLSX exports.
- Pages are rendered in headless Chromium with its sandbox on. `SPIDER_NO_SANDBOX=1` turns it off for
  containers that cannot run it.
- Basic auth and custom headers are sent to the crawled site only, never to external links.
- API keys and passwords are kept out of crawl files, so a crawl `.db` can be shared.
- The settings API returns saved keys, passwords, connector secrets and custom header values as a mask; the real
  values stay in `settings.json`.

`tests/check_security.py` covers these.

## Known limits

- In desktop mode the crawler will follow links to private and local addresses, because crawling a staging or
  intranet site is a normal use. The hosted mode (`SPIDER_API_KEY` set) refuses them.
