"""Отправка письма через SMTP (stdlib, в потоке — не блокирует event loop). Без SMTP_HOST email недоступен."""
import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings


class MailError(Exception):
    user_facing = True


def configured() -> bool:
    return bool(settings.smtp_host and (settings.smtp_from or settings.smtp_user))


def _send(to: list[str], subject: str, text: str, html: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from or settings.smtp_user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    cls = smtplib.SMTP_SSL if settings.smtp_ssl else smtplib.SMTP
    kwargs = {"context": ctx} if settings.smtp_ssl else {}
    with cls(settings.smtp_host, settings.smtp_port, timeout=30, **kwargs) as s:
        if settings.smtp_tls and not settings.smtp_ssl:
            s.starttls(context=ctx)
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


async def send(to: list[str], subject: str, text: str, html: str) -> None:
    if not configured():
        raise MailError("Email не настроен: задайте SMTP_HOST и SMTP_FROM в .env")
    if not to:
        raise MailError("Нет получателей")
    try:
        await asyncio.to_thread(_send, to, subject, text, html)
    except (OSError, smtplib.SMTPException) as e:
        raise MailError(f"Письмо не отправлено: {type(e).__name__}: {e}") from e
