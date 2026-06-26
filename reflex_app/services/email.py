"""Send transactional email over SMTP (stdlib only).

Pure module — no Reflex imports. `smtplib` is blocking, so callers invoke
`send_magic_link` via `reflex.utils.misc.run_in_thread`.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from .. import config


class EmailConfigError(RuntimeError):
    """Raised when SMTP is not configured (missing host/username/password)."""


def _build_message(to_email: str, link: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = "Your AudioMedia sign-in link"
    msg["From"] = config.smtp_from()
    msg["To"] = to_email
    ttl = config.MAGIC_LINK_TTL_MINUTES
    msg.set_content(
        f"Click the link below to sign in to AudioMedia:\n\n{link}\n\n"
        f"This link expires in {ttl} minutes and can be used once. "
        f"If you didn't request it, you can ignore this email."
    )
    msg.add_alternative(
        f"""\
<html><body style="font-family:sans-serif">
  <p>Click the button below to sign in to <strong>AudioMedia</strong>:</p>
  <p><a href="{link}"
        style="display:inline-block;padding:10px 18px;background:#3b82f6;
               color:#fff;border-radius:6px;text-decoration:none">Sign in</a></p>
  <p style="color:#666;font-size:13px">Or paste this URL: <br>{link}</p>
  <p style="color:#666;font-size:13px">This link expires in {ttl} minutes and can
     be used once. If you didn't request it, ignore this email.</p>
</body></html>""",
        subtype="html",
    )
    return msg


def send_magic_link(to_email: str, link: str) -> None:
    """Email a magic sign-in link. Raises EmailConfigError if SMTP is unset."""
    host = config.smtp_hostname()
    user = config.smtp_username()
    password = config.smtp_password()
    if not (host and user and password):
        raise EmailConfigError("SMTP is not configured (SMTP_HOSTNAME/USERNAME/PASSWORD).")

    msg = _build_message(to_email, link)
    with smtplib.SMTP(host, config.SMTP_PORT, timeout=20) as server:
        if config.SMTP_STARTTLS:
            server.starttls(context=ssl.create_default_context())
        server.login(user, password)
        server.send_message(msg)
