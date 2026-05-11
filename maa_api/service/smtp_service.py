import smtplib

from email.mime.text import MIMEText
from email.header import Header

from maa_api.config.config import Config

def send_email(subject: str, body: str):
    smtp_server = Config.get_config('smtp', 'server')
    smtp_port = Config.get_config('smtp', 'port')
    stmp_email = Config.get_config('smtp', 'email')
    smtp_password = Config.get_config('smtp', 'password')

    if not smtp_server or not stmp_email or not smtp_password:
        raise RuntimeError("SMTP 配置错误")

    # 构建邮件
    msg = MIMEText(body, 'html', 'utf-8')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] =  stmp_email
    msg['To'] = stmp_email

    with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(stmp_email, smtp_password)
            server.sendmail(stmp_email, [msg['To']], msg.as_string())
            server.quit()