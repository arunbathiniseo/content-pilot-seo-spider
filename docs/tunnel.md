# Reaching the Spider at spider.contentpilot.cloud

The Spider runs on one computer. A Cloudflare Tunnel publishes that computer's port 7743 at
`https://spider.contentpilot.cloud`, and Cloudflare Access puts a login in front of it. Content Pilot's Site Audit
page opens that address instead of `127.0.0.1:7743`.

Limits to keep in mind: the address only works while the Spider app is open on the host computer, and everyone
who signs in sees the same crawls and settings. The hosted multi-user version (phase 4) removes both limits.

## 1. Tunnel (one-time, in the Cloudflare dashboard plus one command on the host computer)

1. Go to `https://one.dash.cloudflare.com` and pick the account that owns contentpilot.cloud.
2. Left menu: **Networks**, then **Tunnels**. Click **Create a tunnel**, choose **Cloudflared**, name it
   `contentpilot-spider`, click **Save tunnel**.
3. On the **Install and run a connector** page choose **Windows**, **64-bit**. The page shows a command that ends
   with a long token and looks like this:

   ```
   cloudflared.exe service install eyJhIjoi...
   ```

   On the host computer open PowerShell **as administrator** (Start menu, type `powershell`, right-click,
   Run as administrator) and run these two lines, pasting the dashboard's command as the second one:

   ```
   winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
   & "C:\Program Files (x86)\cloudflared\cloudflared.exe" service install <paste the token from the dashboard>
   ```

   If winget put cloudflared somewhere else, `where.exe cloudflared` prints the path. The service starts at once
   and with every boot. Within a few seconds the dashboard shows the connector as **Connected**. Click **Next**.
4. **Route tunnel**, tab **Public hostnames**: subdomain `spider`, domain `contentpilot.cloud`, path empty.
   Service type `HTTP`, URL `localhost:7743`. Click **Save tunnel**. Cloudflare creates the DNS record for you.

5. Tell the Spider it may answer to that name. The desktop app refuses every request that is not addressed to this
   computer (`127.0.0.1` or `localhost`), which is what stops a web page in your browser from reaching it. On the
   host computer run this once, then restart the Spider app:

   ```
   setx SPIDER_ALLOWED_HOSTS spider.contentpilot.cloud
   ```

Check: `https://spider.contentpilot.cloud/api/system/check` should return JSON while the Spider app is open on the
host computer. The address is now public, so continue with section 2 straight away.

## 2. Access (one-time, in the Cloudflare dashboard)

Until this is done the address is open to anyone who guesses it, and the Spider's settings hold API keys.

1. Still in `https://one.dash.cloudflare.com`: **Access controls**, then **Applications**. Click
   **Add an application**, choose **Self-hosted**.
2. Application name `Content Pilot Spider`. Session duration `1 month`.
3. Under **Application domain** click **Add public hostname**: subdomain `spider`, domain `contentpilot.cloud`,
   path empty.
4. **Access policies**: click **Create new policy**. Name `Owners`, action **Allow**. Under **Include** choose the
   selector **Emails** and enter the addresses that may use it, for example
   `you@example.com`, `teammate@example.com`. Save the policy
   and tick it for the application.
5. Login methods: leave **Accept all available identity providers** on. The default is a one-time PIN sent to
   the email address, which needs no extra setup. Adding **Google** under **Integrations, Identity providers**
   later gives a one-click login.
6. Open the application's **Advanced settings**, then **Cross-Origin Resource Sharing (CORS)**:
   - Allowed origins: `https://contentpilot.cloud`, `https://www.contentpilot.cloud`, `https://app.contentpilot.cloud`
   - Allowed methods: `GET`, `OPTIONS`
   - Allow all headers: on
   - Allow credentials: on

   This lets the Site Audit card on contentpilot.cloud ask the Spider for its status and latest Site Health once you
   have signed in to the Spider in the same browser.
7. Save the application.

Check: open `https://spider.contentpilot.cloud` in a private window. You should see the Cloudflare login, receive
the PIN by email, and then land in the Spider.

## 3. Content Pilot

The deployed site must be built with `NEXT_PUBLIC_SPIDER_URL=https://spider.contentpilot.cloud`. Add that line to
the `env:` block of `.github/workflows/deploy-cloudflare.yml` (it is a public address, not a secret) and push; the
next deploy picks it up. Local development keeps the default `http://127.0.0.1:7743`; add the same line to
`.env.local` to test the hosted address locally.

Behaviour of the Site Audit card with the hosted address:

- The **Open Spider** and **Open & start crawl** buttons work whenever the card can reach the Spider. The first
  click goes through the Cloudflare login if you have no session yet.
- The status badge reads **online** once the card can reach the Spider with your Access session. Before you have
  signed in it reads **sign in** and the card explains what to do.

## Undo

- Stop forwarding: `Stop-Service cloudflared` (start again with `Start-Service cloudflared`).
- Remove everything: `cloudflared service uninstall`, then delete the tunnel under **Networks**, **Tunnels** (this
  also removes the DNS record) and the application under **Access controls**, **Applications**.
