import html as _html
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

log = logging.getLogger(__name__)

_LOGO_URL = "https://hubgate.io/hubgate/hubgate-logo.svg"

_CARD_STYLE = (
    "font-family: 'Helvetica Neue', Arial, sans-serif; background: #f5f5f4; padding: 40px 16px;"
)
_INNER_STYLE = (
    "max-width: 520px; margin: 0 auto; background: #ffffff; border-radius: 12px;"
    "border: 1px solid #e7e5e4; overflow: hidden;"
)
_LOGO_HEADER = (
    f'<div style="height: 4px; background: #2563eb;"></div>'
    f'<div style="background: #ffffff; padding: 20px 32px; border-bottom: 1px solid #e7e5e4;">'
    f'<img src="{_LOGO_URL}" alt="Hubgate" height="28" style="display: block;">'
    f'</div>'
)
_CONTENT_STYLE = "padding: 32px 32px 28px 32px;"
_HEADING_STYLE = (
    "margin: 0 0 20px 0; font-size: 17px; font-weight: 700; color: #1c1917;"
    "padding-bottom: 16px; border-bottom: 1px solid #e7e5e4;"
)
_BODY_STYLE = "color: #57534e; font-size: 14px; line-height: 1.6; margin: 0 0 16px 0;"
_CODE_STYLE = (
    "display: block; background: #f5f5f4; border: 1px solid #e7e5e4; border-radius: 6px;"
    "padding: 10px 14px; font-family: monospace; font-size: 12px; color: #292524;"
    "word-break: break-all; margin: 8px 0 16px 0;"
)
_FOOTER_STYLE = (
    "color: #a8a29e; font-size: 12px; margin: 20px 0 0 0;"
    "padding-top: 16px; border-top: 1px solid #e7e5e4;"
)


def _sanitize_header(value: str) -> str:
    """Strip CR/LF to prevent SMTP header injection."""
    return value.replace("\r", "").replace("\n", "")


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block; padding: 2px 10px; border-radius: 9999px;'
        f'background: {color}; color: #fff; font-size: 12px; font-weight: 600;">{_html.escape(text)}</span>'
    )


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an HTML email. Returns True on success, False if SMTP is not configured or sending fails."""
    from open_webui.env import (
        SMTP_FROM_EMAIL,
        SMTP_FROM_NAME,
        SMTP_HOST,
        SMTP_PASSWORD,
        SMTP_PORT,
        SMTP_USERNAME,
    )

    if not SMTP_HOST or not SMTP_FROM_EMAIL:
        log.debug("[email] SMTP not configured, skipping email to %s", to)
        return False

    from open_webui.env import SMTP_USE_TLS

    safe_to = _sanitize_header(to)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = _sanitize_header(subject)
    msg["From"] = f"{_sanitize_header(SMTP_FROM_NAME)} <{_sanitize_header(SMTP_FROM_EMAIL)}>"
    msg["To"] = safe_to
    msg.attach(MIMEText(html, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls(context=context)
                server.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                if not SMTP_USE_TLS:
                    log.warning("[email] Sending SMTP credentials over a plaintext (non-TLS) connection")
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, safe_to, msg.as_string())
        log.info("[email] Sent '%s' to %s", subject, to)
        return True
    except Exception as e:
        log.error("[email] Failed to send '%s' to %s: %s", subject, to, e)
        return False


def _email_wrapper(content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="{_CARD_STYLE}">
  <div style="{_INNER_STYLE}">
    {_LOGO_HEADER}
    <div style="{_CONTENT_STYLE}">
      {content}
    </div>
  </div>
</body>
</html>"""


def send_team_invite_email(
    to: str,
    team_name: str,
    invited_by: str,
    invite_url: str,
) -> bool:
    from open_webui.env import WEBUI_NAME
    safe_team_name = _html.escape(team_name.replace('\n', '').replace('\r', ''))
    safe_invited_by = _html.escape(invited_by)
    safe_invite_url = _html.escape(invite_url)
    subject = f"You've been invited to join {team_name.replace(chr(10), '').replace(chr(13), '')} on {WEBUI_NAME}"
    html = _email_wrapper(f"""
    <h2 style="{_HEADING_STYLE}">You've been invited to a team</h2>
    <p style="{_BODY_STYLE}">
      <strong>{safe_invited_by}</strong> has invited you to join the
      <strong>{safe_team_name}</strong> team on {_html.escape(WEBUI_NAME)}.
    </p>
    <p style="margin: 24px 0;">
      <a href="{safe_invite_url}"
         style="display: inline-block; padding: 12px 24px; background: #2563eb; color: #fff;
                text-decoration: none; border-radius: 8px; font-weight: 600;">
        Accept Invitation
      </a>
    </p>
    <p style="{_BODY_STYLE}">
      This invitation expires in 7 days. If you didn't expect this email, you can ignore it.
    </p>
    <p style="{_FOOTER_STYLE}">
      Or copy this link: <a href="{safe_invite_url}" style="color: #57534e;">{safe_invite_url}</a>
    </p>""")
    return send_email(to, subject, html)


def send_ecb_unreachable_email(to: str, startup_time: str, error_detail: str) -> bool:
    from open_webui.env import WEBUI_NAME
    subject = f"[{WEBUI_NAME}] ECB exchange rate service unreachable"
    html = _email_wrapper(f"""
    <h2 style="{_HEADING_STYLE}">{_badge("Action required", "#dc2626")} &nbsp;ECB rate service unreachable</h2>
    <p style="{_BODY_STYLE}">
      The ECB exchange rate API has been unreachable since process startup
      (<strong>{_html.escape(startup_time)}</strong>).
      New LLM observations are being stored without EUR cost and will not be counted
      toward billing until the service recovers.
    </p>
    <p style="color: #57534e; font-size: 13px; margin: 0 0 6px 0;">Last error:</p>
    <code style="{_CODE_STYLE}">{_html.escape(error_detail)}</code>
    <p style="{_BODY_STYLE}">The poller will retry every 15 minutes automatically.</p>
    <p style="{_FOOTER_STYLE}">{_html.escape(WEBUI_NAME)} billing poller</p>""")
    return send_email(to, subject, html)


def send_unpriced_models_email(to: str, model_names: List[str]) -> bool:
    from open_webui.env import WEBUI_NAME
    count = len(model_names)
    subject = (
        f"[{WEBUI_NAME}] Unpriced model in Langfuse: {_sanitize_header(model_names[0])}"
        if count == 1
        else f"[{WEBUI_NAME}] {count} unpriced models in Langfuse"
    )
    model_rows = "".join(
        f'<tr><td style="padding: 8px 12px; border-bottom: 1px solid #e7e5e4;">'
        f'<code style="font-size: 13px; color: #292524;">{_html.escape(m)}</code></td></tr>'
        for m in model_names
    )
    html = _email_wrapper(f"""
    <h2 style="{_HEADING_STYLE}">{_badge("Billing gap", "#f97316")} &nbsp;Unpriced model{"s" if count > 1 else ""} detected</h2>
    <p style="{_BODY_STYLE}">
      The following model{"s" if count > 1 else ""} {"have" if count > 1 else "has"} no pricing configured in Langfuse.
      Observations will be stored with <code>cost_eur=NULL</code> and excluded from billing until pricing is added.
    </p>
    <table style="width: 100%; border-collapse: collapse; border: 1px solid #e7e5e4; border-radius: 8px; overflow: hidden; margin-bottom: 20px;">
      {model_rows}
    </table>
    <p style="{_BODY_STYLE}">Configure pricing under <strong>Settings → Models</strong> in your Langfuse instance.</p>
    <p style="{_FOOTER_STYLE}">{_html.escape(WEBUI_NAME)} billing poller &mdash; you will receive another email when pricing is restored.</p>""")
    return send_email(to, subject, html)


def send_model_pricing_recovered_email(to: str, model_names: List[str]) -> bool:
    from open_webui.env import WEBUI_NAME
    count = len(model_names)
    subject = (
        f"[{WEBUI_NAME}] Model pricing restored: {_sanitize_header(model_names[0])}"
        if count == 1
        else f"[{WEBUI_NAME}] {count} models pricing restored in Langfuse"
    )
    model_rows = "".join(
        f'<tr><td style="padding: 8px 12px; border-bottom: 1px solid #e7e5e4;">'
        f'<code style="font-size: 13px; color: #292524;">{_html.escape(m)}</code></td></tr>'
        for m in model_names
    )
    html = _email_wrapper(f"""
    <h2 style="{_HEADING_STYLE}">{_badge("Resolved", "#16a34a")} &nbsp;Model pricing restored</h2>
    <p style="{_BODY_STYLE}">
      The following model{"s" if count > 1 else ""} {"are" if count > 1 else "is"} now producing priced observations in Langfuse.
      New observations will be counted toward billing correctly.
    </p>
    <table style="width: 100%; border-collapse: collapse; border: 1px solid #e7e5e4; border-radius: 8px; overflow: hidden; margin-bottom: 20px;">
      {model_rows}
    </table>
    <p style="{_BODY_STYLE}">Note: past observations stored with <code>cost_eur=NULL</code> are not retroactively updated.</p>
    <p style="{_FOOTER_STYLE}">{_html.escape(WEBUI_NAME)} billing poller</p>""")
    return send_email(to, subject, html)
