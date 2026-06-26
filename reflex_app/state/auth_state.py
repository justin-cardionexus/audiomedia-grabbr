"""State that finalizes a Google sign-in on the frontend.

The OAuth callback redirects to /auth/complete/[token]; this reads the token
(a LocalAuthSession id minted server-side) and writes it into the LocalStorage
auth_token so reflex_local_auth treats the user as authenticated.
"""

from __future__ import annotations

import reflex as rx
import reflex_local_auth
from reflex.utils.misc import run_in_thread

from .. import config
from ..services import email as email_service
from ..services import magic_link


class AuthCompleteState(reflex_local_auth.LocalAuthState):
    @rx.event
    def complete(self):
        token = getattr(self, "token", "")
        if token:
            self.auth_token = token
        return rx.redirect("/")


def _looks_like_email(value: str) -> bool:
    value = value.strip()
    return "@" in value and "." in value.split("@")[-1] and " " not in value


class MagicLinkState(reflex_local_auth.LocalAuthState):
    """Passwordless email sign-in: request a magic link."""

    email: str = ""
    sent: bool = False
    sending: bool = False
    error: str = ""

    @rx.var
    def smtp_enabled(self) -> bool:
        return config.smtp_enabled()

    @rx.event
    def set_email(self, value: str):
        self.email = value
        self.error = ""

    @rx.event
    def reset_form(self):
        self.sent = False
        self.error = ""
        self.email = ""

    @rx.event
    async def request_link(self):
        self.error = ""
        addr = self.email.strip()
        if not _looks_like_email(addr):
            self.error = "Please enter a valid email address."
            return
        if not config.smtp_enabled():
            self.error = "Email sign-in is not configured on this server."
            return

        self.sending = True
        yield  # flush spinner

        raw = magic_link.create_token(addr)
        link = f"{config.BACKEND_URL}/auth/magic/verify?token={raw}"
        try:
            await run_in_thread(lambda: email_service.send_magic_link(addr, link))
        except Exception:  # noqa: BLE001
            self.sending = False
            self.error = "Could not send the email. Please try again later."
            return

        self.sending = False
        # Neutral confirmation (no account enumeration).
        self.sent = True
