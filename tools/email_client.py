"""邮件客户端 — 调用 email-service 微服务发送邮件。"""

import json
import urllib.request

EMAIL_SVC = "http://email.email-service.svc.cluster.local"


def send_email(to: str, subject: str, body: str) -> bool:
    """发送邮件，成功返回 True，失败返回 False（不抛异常）"""
    try:
        data = json.dumps({"to": to, "subject": subject, "body": body}).encode("utf-8")
        req = urllib.request.Request(f"{EMAIL_SVC}/send", data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False
