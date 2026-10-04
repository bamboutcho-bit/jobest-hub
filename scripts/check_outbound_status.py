from src.storage.db import get_session
from src.storage.models import JobPosting, UserJobApplication, EmailEvent, OutboundMessage, InboxMessage, OutboundSuppression
from sqlalchemy import select, desc

with get_session() as s:
    outbounds = s.scalars(select(OutboundMessage).order_by(desc(OutboundMessage.id)).limit(25)).all()
    print("=== Recent outbound messages ===")
    for m in outbounds:
        sub = (m.subject[:35] + "...") if m.subject else "None"
        print(f"ID {m.id} | to: {m.recipient_email} | status: {m.status} | sub: {sub} | fail: {m.failure_reason}")

    inbox = s.scalars(select(InboxMessage).order_by(desc(InboxMessage.id)).limit(20)).all()
    print("\n=== Recent Inbox messages ===")
    for i in inbox:
        print(f"ID {i.id} | from: {i.sender_email} | sub: {i.subject} | class: {getattr(i, 'classification', None)}")

    supp = s.scalars(select(OutboundSuppression).order_by(desc(OutboundSuppression.id)).limit(20)).all()
    print("\n=== Recent Suppressions ===")
    for sp in supp:
        print(f"ID {sp.id} | email: {sp.recipient_email} | domain: {sp.domain} | reason: {sp.reason}")
