"""Crawl-complete email: plain-text + HTML summary with the full table of triggered issues, sent over SMTP with the
settings from Config > Email. Sending is synchronous (smtplib); the server runs it in a thread."""
from __future__ import annotations

import html
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

from .config import CrawlConfig
from .exports import issues_overview_rows


class EmailError(Exception):
    pass


def _rows(con: sqlite3.Connection, cfg: CrawlConfig) -> list[tuple[Any, ...]]:
    return issues_overview_rows(con, cfg)


def build_email(con: sqlite3.Connection, cfg: CrawlConfig, status: dict[str, Any], compare: dict[str, Any] | None = None) -> tuple[str, str, str]:
    """(subject, text body, html body) for the crawl that `con` holds."""
    site = status.get("start_url") or cfg.start_url or "crawl"
    state = status.get("state", "finished")
    total = con.execute("SELECT count(*) FROM urls WHERE crawled IN (1,3)").fetchone()[0]
    pages = con.execute("SELECT count(*) FROM urls WHERE crawled=1 AND is_internal=1 AND type='HTML'").fetchone()[0]
    rows = _rows(con, cfg)
    issues = [r for r in rows if r[1] == "Issue"]
    elapsed = int(status.get("elapsed") or 0)
    subject = f"[Content Pilot Spider] {site}: {len(issues)} issues, {len(rows)} flags in {total:,} URLs"

    # --- text
    t = [f"Crawl {state}: {site}", f"{total:,} URLs ({pages:,} HTML pages) in {elapsed // 60}m {elapsed % 60}s", ""]
    if compare:
        s = compare["summary"]
        t += [f"Since the crawl of {compare['a'].get('started_at', '')[:16]}: {s['issues_up']} issue counts up, {s['issues_down']} down, "
              f"{s['issues_new']} new, {s['issues_fixed']} fixed; {s['added']} pages added, {s['removed']} removed, {s['changed']} changed.", ""]
    t += [f"{len(rows)} issue types triggered", "", f"{'Issue':60} {'Type':12} {'Priority':8} {'URLs':>8}", "-" * 92]
    t += [f"{name[:60]:60} {kind:12} {prio:8} {n:>8,}" for name, kind, prio, n, _pct, _desc, _fix in rows]
    t += ["", "Open Content Pilot Spider to see the affected URLs, or export the issues pack from the Export centre."]
    text = "\n".join(t)

    # --- html
    colour = {"Issue": "#d1242f", "Warning": "#b7791f", "Opportunity": "#1f7a3f"}
    tr = "".join(
        f"<tr><td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(name)}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:{colour.get(kind, '#15171a')};font-weight:600'>{html.escape(kind)}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb'>{html.escape(prio)}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;text-align:right'>{n:,}</td>"
        f"<td style='padding:6px 10px;border-bottom:1px solid #e5e7eb;color:#5b616b;font-size:12px'>{html.escape(fix or '')}</td></tr>"
        for name, kind, prio, n, _pct, _desc, fix in rows)
    cmp_html = ""
    if compare:
        s = compare["summary"]
        cmp_html = (f"<p style='margin:0 0 14px;padding:10px 12px;background:#f4f5f7;border-radius:6px'>Since the crawl of {html.escape(compare['a'].get('started_at', '')[:16])}: "
                    f"<b>{s['issues_up']}</b> issue counts up, <b>{s['issues_down']}</b> down, <b>{s['issues_new']}</b> new, <b>{s['issues_fixed']}</b> fixed · "
                    f"{s['added']} pages added, {s['removed']} removed, {s['changed']} changed.</p>")
    body_html = f"""<!doctype html><html><body style="font-family:'IBM Plex Sans',Segoe UI,Arial,sans-serif;color:#15171a;background:#ffffff;padding:20px">
<h2 style="margin:0 0 4px;font-size:18px">Crawl {html.escape(state)}: {html.escape(site)}</h2>
<p style="margin:0 0 14px;color:#5b616b">{total:,} URLs ({pages:,} HTML pages) in {elapsed // 60}m {elapsed % 60}s · {len(rows)} issue types triggered</p>
{cmp_html}
<table style="border-collapse:collapse;width:100%;font-size:13px">
<thead><tr style="text-align:left;background:#f4f5f7"><th style="padding:6px 10px">Issue</th><th style="padding:6px 10px">Type</th><th style="padding:6px 10px">Priority</th><th style="padding:6px 10px;text-align:right">URLs</th><th style="padding:6px 10px">How to fix</th></tr></thead>
<tbody>{tr or "<tr><td colspan='5' style='padding:10px'>No issues flagged.</td></tr>"}</tbody></table>
<p style="margin:16px 0 0;color:#5b616b;font-size:12px">Sent by Content Pilot Spider. Open the app to see the affected URLs, or export the issues pack from the Export centre.</p>
</body></html>"""
    return subject, text, body_html


def send_email(cfg: CrawlConfig, subject: str, text: str, body_html: str = "", to: str = "") -> str:
    """Send over SMTP with the Config > Email settings. Returns the recipient list on success, raises EmailError."""
    recipients = [a.strip() for a in (to or cfg.email_to).replace(";", ",").split(",") if a.strip()]
    if not recipients:
        raise EmailError("No recipient: set 'Send to' in Config > Email.")
    if not cfg.smtp_host.strip():
        raise EmailError("No SMTP server: set the host in Config > Email.")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.email_from.strip() or cfg.smtp_user.strip() or "spider@localhost"
    msg["To"] = ", ".join(recipients)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("contentpilot-spider")
    msg.set_content(text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    host, port, mode = cfg.smtp_host.strip(), int(cfg.smtp_port or 587), (cfg.smtp_security or "starttls").lower()
    try:
        if mode == "ssl":
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server:
            server.ehlo()
            if mode == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if cfg.smtp_user.strip():
                server.login(cfg.smtp_user.strip(), cfg.smtp_pass)
            server.send_message(msg, to_addrs=recipients)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError(f"The SMTP server rejected the login ({exc.smtp_code}). For Gmail/Outlook use an app password, not the account password.")
    except smtplib.SMTPException as exc:
        raise EmailError(f"SMTP error: {exc}")
    except (OSError, ssl.SSLError) as exc:
        raise EmailError(f"Could not connect to {host}:{port} ({mode}): {exc}")
    return ", ".join(recipients)
