import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from os import getenv

logger = logging.getLogger(__name__)


def _get_smtp_config() -> dict:
    return {
        "host": getenv("MAIL_SERVER", "smtp.gmail.com"),
        "port": int(getenv("MAIL_PORT", "587")),
        "user": getenv("MAIL_USERNAME", ""),
        "password": getenv("MAIL_PASSWORD", ""),
        "from_email": getenv("MAIL_DEFAULT_SENDER", ""),
        "use_tls": getenv("MAIL_USE_TLS", "true").lower() == "true",
    }


def send_verification_email(to_email: str, name: str, verification_url: str) -> None:
    cfg = _get_smtp_config()
    if not cfg["user"] or not cfg["password"]:
        logger.warning(
            "SMTP credentials not configured — skipping verification email to %s. "
            "Set MAIL_USERNAME and MAIL_PASSWORD env vars.",
            to_email,
        )
        logger.info("Verification link (dev): %s", verification_url)
        return

    sender = cfg["from_email"] or cfg["user"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Verify your account — Leadership Assessment"
    msg["From"] = sender
    msg["To"] = to_email

    text_body = (
        f"Hi {name},\n\n"
        f"Thanks for signing up! Please verify your email by visiting:\n\n"
        f"{verification_url}\n\n"
        f"If you didn't create this account, you can ignore this email.\n\n"
        f"— The Leadership Assessment Team"
    )

    html_body = f"""\
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f6f8; padding: 40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 520px; margin: 0 auto;">
        <tr>
          <td style="background: #ffffff; border-radius: 8px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
            <h2 style="margin: 0 0 16px; color: #1a1a2e;">Welcome, {name}!</h2>
            <p style="color: #444; line-height: 1.6; margin: 0 0 24px;">
              Thanks for signing up for <strong>Leadership Assessment</strong>.
              Please confirm your email address by clicking the button below.
            </p>
            <p style="text-align: center; margin: 0 0 24px;">
              <a href="{verification_url}"
                 style="display: inline-block; background: #4f46e5; color: #ffffff;
                        padding: 12px 32px; border-radius: 6px; text-decoration: none;
                        font-weight: 600; font-size: 16px;">
                Verify My Email
              </a>
            </p>
            <p style="color: #888; font-size: 13px; line-height: 1.5; margin: 0;">
              If the button doesn't work, copy and paste this link into your browser:<br/>
              <a href="{verification_url}" style="color: #4f46e5; word-break: break-all;">
                {verification_url}
              </a>
            </p>
          </td>
        </tr>
        <tr>
          <td style="text-align: center; padding: 20px; color: #aaa; font-size: 12px;">
            If you didn't create this account, you can safely ignore this email.
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    _send(cfg, sender, to_email, msg)


def send_reset_password_email(to_email: str, name: str, reset_url: str) -> None:
    cfg = _get_smtp_config()
    if not cfg["user"] or not cfg["password"]:
        logger.warning(
            "SMTP credentials not configured — skipping reset email to %s. "
            "Set MAIL_USERNAME and MAIL_PASSWORD env vars.",
            to_email,
        )
        logger.info("Password reset link (dev): %s", reset_url)
        return

    sender = cfg["from_email"] or cfg["user"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset your password — Leadership Assessment"
    msg["From"] = sender
    msg["To"] = to_email

    text_body = (
        f"Hi {name},\n\n"
        f"We received a request to reset your password. "
        f"Use the link below to set a new one:\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email.\n\n"
        f"— The Leadership Assessment Team"
    )

    html_body = f"""\
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f6f8; padding: 40px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 520px; margin: 0 auto;">
        <tr>
          <td style="background: #ffffff; border-radius: 8px; padding: 40px; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
            <h2 style="margin: 0 0 16px; color: #1a1a2e;">Password Reset</h2>
            <p style="color: #444; line-height: 1.6; margin: 0 0 8px;">
              Hi {name},
            </p>
            <p style="color: #444; line-height: 1.6; margin: 0 0 24px;">
              We received a request to reset your password.
              Click the button below to choose a new one.
            </p>
            <p style="text-align: center; margin: 0 0 24px;">
              <a href="{reset_url}"
                 style="display: inline-block; background: #4f46e5; color: #ffffff;
                        padding: 12px 32px; border-radius: 6px; text-decoration: none;
                        font-weight: 600; font-size: 16px;">
                Reset Password
              </a>
            </p>
            <p style="color: #888; font-size: 13px; line-height: 1.5; margin: 0;">
              If the button doesn't work, copy and paste this link into your browser:<br/>
              <a href="{reset_url}" style="color: #4f46e5; word-break: break-all;">
                {reset_url}
              </a>
            </p>
          </td>
        </tr>
        <tr>
          <td style="text-align: center; padding: 20px; color: #aaa; font-size: 12px;">
            If you didn't request a password reset, you can safely ignore this email.
          </td>
        </tr>
      </table>
    </body>
    </html>
    """

    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    _send(cfg, sender, to_email, msg)


def _send(cfg: dict, sender: str, to_email: str, msg: MIMEMultipart) -> None:
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
            if cfg["use_tls"]:
                server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(sender, to_email, msg.as_string())
        logger.info("Email sent to %s", to_email)
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        # Do not fail auth flows (signup/reset) when SMTP is misconfigured in local/dev.
        # Account creation and token generation should still complete successfully.
        return
