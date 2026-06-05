import html as _html
import logging
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to
    msg.attach(MIMEText(html, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, to, msg.as_string())
        log.info("[email] Sent '%s' to %s", subject, to)
        return True
    except Exception as e:
        log.error("[email] Failed to send '%s' to %s: %s", subject, to, e)
        return False


def send_team_invite_email(
    to: str,
    team_name: str,
    invited_by: str,
    invite_url: str,
) -> bool:
    safe_team_name = _html.escape(team_name.replace('\n', '').replace('\r', ''))
    safe_invited_by = _html.escape(invited_by)
    safe_invite_url = _html.escape(invite_url)
    subject = f"You've been invited to join {team_name.replace(chr(10), '').replace(chr(13), '')} on Keeper AI Gateway"
    html = f"""
<!DOCTYPE html>
<html>
<body style="font-family: sans-serif; background: #f9f9f9; padding: 32px;">
  <div style="max-width: 480px; margin: 0 auto; background: #fff; border-radius: 12px; padding: 32px; border: 1px solid #e5e7eb;">
    <h2 style="margin-top: 0; font-size: 20px;">You've been invited to a team</h2>
    <p style="color: #374151;">
      <strong>{safe_invited_by}</strong> has invited you to join the
      <strong>{safe_team_name}</strong> team on Keeper AI Gateway.
    </p>
    <p style="margin: 24px 0;">
      <a href="{safe_invite_url}"
         style="display: inline-block; padding: 12px 24px; background: #111827; color: #fff;
                text-decoration: none; border-radius: 8px; font-weight: 600;">
        Accept Invitation
      </a>
    </p>
    <p style="color: #6b7280; font-size: 13px;">
      This invitation expires in 7 days. If you didn't expect this email, you can ignore it.
    </p>
    <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 24px 0;">
    <p style="color: #9ca3af; font-size: 12px; margin: 0;">
      Or copy this link: <a href="{safe_invite_url}" style="color: #6b7280;">{safe_invite_url}</a>
    </p>
  </div>
</body>
</html>
"""
    return send_email(to, subject, html)
