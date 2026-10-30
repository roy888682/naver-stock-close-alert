"""TLS-only SMTP; passwords never enter the database or HTTP responses."""
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


class MailError(RuntimeError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def send_mail(settings, subject, body):
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    if not password:
        raise MailError("SMTP_PASSWORD가 없습니다. .env에 Gmail 앱 비밀번호를 설정하고 다시 실행하세요.")
    if not settings["smtp_username"] or not settings["smtp_from"]:
        raise MailError("SMTP 사용자명과 발신 주소를 설정하세요.")
    msg = EmailMessage()
    msg["From"] = settings["smtp_from"]
    msg["To"] = settings["recipient"]
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="gmail.com")
    msg.set_content(body)
    smtp = None
    sending = False
    try:
        context = ssl.create_default_context()
        if settings["smtp_security"] == "ssl":
            smtp = smtplib.SMTP_SSL(settings["smtp_host"], settings["smtp_port"], timeout=25, context=context)
        else:
            smtp = smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=25)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        smtp.login(settings["smtp_username"], password)
        sending = True
        refused = smtp.send_message(msg, from_addr=settings["smtp_from"], to_addrs=[settings["recipient"]])
        if refused:
            raise MailError("수신 서버가 수신 주소를 거절했습니다.")
    except MailError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise MailError("Gmail 인증 실패: 계정과 앱 비밀번호를 확인하세요.") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        raise MailError("메일 서버가 발신·수신 주소 또는 본문을 거절했습니다.") from None
    except (OSError, smtplib.SMTPException):
        raise MailError(
            "발송 중 연결이 끊겨 접수 여부를 확인할 수 없습니다. 보낸 편지함을 확인하세요." if sending
            else "메일 서버 연결 실패: 서버·포트·TLS 설정과 네트워크를 확인하세요.", uncertain=sending
        ) from None
    finally:
        if smtp:
            # QUIT failure must not turn an accepted DATA response into a retry.
            try:
                smtp.quit()
            except (OSError, smtplib.SMTPException):
                smtp.close()
    return str(msg["Message-ID"])
