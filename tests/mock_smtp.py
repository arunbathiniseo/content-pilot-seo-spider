"""Tiny SMTP sink for tests (Python 3.12 dropped smtpd). Accepts any mail, stores it, and serves the inbox over HTTP.

    python tests/mock_smtp.py [smtp_port] [http_port]      GET http://127.0.0.1:<http_port>/  -> JSON list of messages
"""
from __future__ import annotations

import asyncio
import email
import json
import sys
from email import policy

SMTP_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8825
HTTP_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8826
INBOX: list[dict] = []


async def smtp_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def send(line: str) -> None:
        writer.write((line + "\r\n").encode()); await writer.drain()
    await send("220 mock ESMTP")
    data_mode, buf, envelope = False, [], {"from": "", "to": []}
    while True:
        raw = await reader.readline()
        if not raw:
            break
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if data_mode:
            if line == ".":
                data_mode = False
                m = email.message_from_string("\n".join(buf), policy=policy.default)
                body_text = m.get_body(("plain",)); body_html = m.get_body(("html",))
                INBOX.append({"from": envelope["from"], "to": envelope["to"], "subject": m["subject"],
                              "text": body_text.get_content() if body_text else "", "html": body_html.get_content() if body_html else ""})
                buf, envelope = [], {"from": "", "to": []}
                await send("250 OK queued")
            else:
                buf.append(line[1:] if line.startswith("..") else line)
            continue
        cmd = line.split(" ", 1)[0].upper()
        if cmd in ("EHLO", "HELO"):
            await send("250-mock"); await send("250 AUTH PLAIN LOGIN")
        elif cmd == "AUTH":
            await send("235 ok")
        elif cmd == "MAIL":
            envelope["from"] = line.split(":", 1)[-1].strip().strip("<>"); await send("250 OK")
        elif cmd == "RCPT":
            envelope["to"].append(line.split(":", 1)[-1].strip().strip("<>")); await send("250 OK")
        elif cmd == "DATA":
            data_mode = True; await send("354 go")
        elif cmd == "QUIT":
            await send("221 bye"); break
        elif cmd in ("NOOP", "RSET"):
            await send("250 OK")
        else:
            await send("502 not implemented")
    writer.close()


async def http_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    await reader.readuntil(b"\r\n\r\n")
    body = json.dumps(INBOX).encode()
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    await writer.drain(); writer.close()


async def main() -> None:
    s1 = await asyncio.start_server(smtp_session, "127.0.0.1", SMTP_PORT)
    s2 = await asyncio.start_server(http_session, "127.0.0.1", HTTP_PORT)
    async with s1, s2:
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
