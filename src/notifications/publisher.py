"""Notification publisher supporting RabbitMQ AMQP/HTTP, Kafka, and direct WebSocket hub."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from config.settings import settings
from src.storage.db import get_session
from src.storage.models import Notification

logger = logging.getLogger(__name__)

_exchange_declared = False


def _get_rabbitmq_params() -> tuple[str, int, int, str, str, str]:
    host = settings.rabbitmq_host or "rabbitmq"
    port = settings.rabbitmq_port or 5672
    mgmt_port = settings.rabbitmq_management_port or 15672
    user = settings.rabbitmq_user or "autohunt"
    password = settings.rabbitmq_password or "autohunt_rabbit_pass"
    exchange = settings.rabbitmq_exchange or "autohunt_notifications"
    return host, port, mgmt_port, user, password, exchange


def _ensure_rabbitmq_exchange() -> None:
    """Ensure the topic exchange exists in RabbitMQ via Management HTTP API or AMQP."""
    global _exchange_declared
    if _exchange_declared:
        return
    host, _, mgmt_port, user, password, exchange = _get_rabbitmq_params()
    try:
        url = f"http://{host}:{mgmt_port}/api/exchanges/%2F/{exchange}"
        auth = (user, password)
        payload = {"type": "topic", "durable": True, "auto_delete": False}
        resp = requests.put(url, json=payload, auth=auth, timeout=1.5)
        if resp.status_code in (201, 204):
            _exchange_declared = True
    except Exception:
        pass


def _publish_rabbitmq_http(routing_key: str, payload_dict: dict[str, Any]) -> bool:
    """Publish to RabbitMQ using the built-in HTTP Management API (reliable fallback)."""
    host, _, mgmt_port, user, password, exchange = _get_rabbitmq_params()
    try:
        _ensure_rabbitmq_exchange()
        url = f"http://{host}:{mgmt_port}/api/exchanges/%2F/{exchange}/publish"
        auth = (user, password)
        body = {
            "properties": {"content_type": "application/json"},
            "routing_key": routing_key,
            "payload": json.dumps(payload_dict),
            "payload_encoding": "string",
        }
        resp = requests.post(url, json=body, auth=auth, timeout=1.5)
        return resp.status_code == 200 and resp.json().get("routed", True)
    except Exception as exc:
        logger.debug("RabbitMQ HTTP publish skipped or unavailable: %s", exc)
        return False


def _publish_rabbitmq_amqp(routing_key: str, payload_dict: dict[str, Any]) -> bool:
    """Publish to RabbitMQ using AMQP protocol via pika if available."""
    try:
        import pika
        host, port, _, user, password, exchange = _get_rabbitmq_params()
        credentials = pika.PlainCredentials(user, password)
        params = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=credentials,
            connection_attempts=1,
            retry_delay=1,
            socket_timeout=1.5,
        )
        conn = pika.BlockingConnection(params)
        channel = conn.channel()
        channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(payload_dict).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )
        conn.close()
        return True
    except Exception as exc:
        logger.debug("RabbitMQ AMQP publish unavailable (%s), trying HTTP...", exc)
        return False


def _publish_kafka(topic: str, payload_dict: dict[str, Any]) -> bool:
    """Publish to Kafka topic if kafka-python is available."""
    try:
        from kafka import KafkaProducer
        bootstrap = settings.kafka_bootstrap_servers or "localhost:9092"
        producer = KafkaProducer(
            bootstrap_servers=bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            request_timeout_ms=1500,
        )
        producer.send(topic, value=payload_dict)
        producer.flush(timeout=1.5)
        return True
    except Exception as exc:
        logger.debug("Kafka publish skipped: %s", exc)
        return False


ADMIN_EVENT_TYPES = {
    "user_registered",
    "user_created",
    "critical_security",
    "system_maintenance",
    "system_error",
    "security_alert",
    "payment_verification",
    "support_chat",
    "admin_chat_event",
}


def publish_notification(
    user_id: Optional[int] = None,
    event_type: str = "system",
    title: str = "",
    message: str = "",
    link: Optional[str] = None,
    data: Optional[dict[str, Any]] = None,
    admin_only: bool = False,
) -> Optional[int]:
    """Publish a real-time notification to RabbitMQ/Kafka, persist to DB, and push to WebSocket hub.
    
    Guarantees 'keep the last thing up' by saving with the current UTC timestamp.
    If admin_only is True (or event_type is an admin event), the notification is strictly isolated to admins.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    notification_id = None
    is_admin_event = admin_only or (event_type in ADMIN_EVENT_TYPES)

    payload_data = dict(data or {})
    if is_admin_event:
        payload_data["admin_only"] = True

    # 1. Persist notification to database (so history is available and sorted newest-first)
    try:
        with get_session() as session:
            notif = Notification(
                user_id=user_id,
                event_type=event_type,
                title=title,
                message=message,
                link=link,
                data_json=json.dumps(payload_data),
                is_read=False,
                created_at=now,
            )
            session.add(notif)
            session.commit()
            notification_id = notif.id
    except Exception as exc:
        logger.warning("Failed to persist notification to database: %s", exc)

    payload = {
        "id": notification_id,
        "user_id": user_id,
        "event_type": event_type,
        "title": title,
        "message": message,
        "link": link,
        "data": payload_data,
        "admin_only": is_admin_event,
        "created_at": now_iso,
    }

    # 2. Publish to Message Broker (RabbitMQ or Kafka)
    broker = (settings.message_broker or "rabbitmq").lower()
    if broker == "rabbitmq":
        routing_key = f"notify.user.{user_id}" if user_id else ("notify.admin" if is_admin_event else "notify.broadcast")
        published = _publish_rabbitmq_amqp(routing_key, payload)
        if not published:
            _publish_rabbitmq_http(routing_key, payload)
    elif broker == "kafka":
        topic = settings.kafka_topic or "autohunt_notifications"
        _publish_kafka(topic, payload)

    # 3. Direct local WebSocket dispatch (strictly isolated via NotificationHub RBAC)
    try:
        from src.notifications.hub import notification_hub
        notification_hub.broadcast_sync(user_id=user_id, payload=payload, admin_only=is_admin_event)
    except Exception as exc:
        logger.debug("Local hub broadcast: %s", exc)

    logger.info("Published notification #%s [%s]: '%s' (User: %s, AdminOnly: %s)", notification_id, event_type, title, user_id or "All", is_admin_event)
    return notification_id


def publish_admin_alert(
    title: str,
    message: str,
    level: str = "critical",
    link: Optional[str] = "#admin",
    data: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Publish a high-priority system maintenance or security alert strictly to administrators."""
    event_type = "critical_security" if level in ("security", "critical") else "system_maintenance"
    return publish_notification(
        user_id=None,
        event_type=event_type,
        title=title,
        message=message,
        link=link,
        data={**(data or {}), "alert_level": level, "admin_only": True},
        admin_only=True,
    )
