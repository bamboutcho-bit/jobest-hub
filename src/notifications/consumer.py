"""Background Notification Consumer for RabbitMQ / Kafka.

Listens to the notification message queue/topic and dispatches events to
the WebSocket hub and configured notification channels (Telegram, Email).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

from config.settings import settings
from src.notifications.hub import notification_hub

logger = logging.getLogger(__name__)

_consumer_thread: Optional[threading.Thread] = None
_consumer_running = False


def _dispatch_received_message(payload: dict[str, Any]) -> None:
    """Handle a notification message consumed from RabbitMQ / Kafka."""
    try:
        user_id = payload.get("user_id")
        event_type = payload.get("event_type", "system")
        title = payload.get("title", "Notification")
        message = payload.get("message", "")

        logger.info("Notification consumed from broker: [%s] %s (User #%s)", event_type, title, user_id)

        # 1. Forward to WebSocket client hub
        notification_hub.broadcast_sync(user_id=user_id, payload=payload)

        # 2. For high-priority notifications, check if Telegram alert should be sent
        # 2. For high-priority notifications, check if Telegram alert should be sent
        is_interview = (
            event_type == "interview_scheduled"
            or (event_type == "inbox_reply" and payload.get("data", {}).get("intent") in ("interview_request", "interview_scheduled"))
        )

        only_interviews = getattr(settings, "telegram_notify_only_interviews", True)
        should_send_tg = is_interview if only_interviews else (event_type in ("job_match", "inbox_reply", "interview_scheduled", "payment_approved", "quota_exhausted"))

        if should_send_tg:
            try:
                from src.storage.db import get_session
                from src.storage.models import UserSetting
                with get_session() as session:
                    us = None
                    if user_id:
                        us = session.query(UserSetting).filter(UserSetting.user_id == user_id).first()
                    tg_token = (us and us.telegram_bot_token) or settings.telegram_bot_token
                    tg_chat = (us and us.telegram_chat_id) or settings.telegram_chat_id
                    if tg_token and tg_chat:
                        from src.alerting.telegram_bot import send_telegram_interview_alert, send_telegram_alert
                        if is_interview:
                            send_telegram_interview_alert({
                                "title": payload.get("data", {}).get("title") or title,
                                "company": payload.get("data", {}).get("company", "AutoHunt"),
                                "summary": message or payload.get("data", {}).get("summary"),
                                "proposed_times": payload.get("data", {}).get("proposed_times") or payload.get("data", {}).get("times"),
                                "url": payload.get("link", "http://localhost:8080/app#inbox"),
                            }, bot_token=tg_token, chat_id=tg_chat)
                        else:
                            send_telegram_alert({
                                "title": title,
                                "company": payload.get("data", {}).get("company", "AutoHunt"),
                                "match_score": payload.get("data", {}).get("score", ""),
                                "url": payload.get("link", ""),
                            })
            except Exception as e:
                logger.debug("Consumer Telegram dispatch exception: %s", e)
    except Exception as exc:
        logger.warning("Error processing notification payload: %s", exc)


def _run_rabbitmq_amqp_consumer() -> None:
    """Consume notifications using pika AMQP connection with automatic reconnect."""
    global _consumer_running
    try:
        import pika
    except ImportError:
        logger.info("pika not installed; AMQP consumer disabled (HTTP management fallback active).")
        return

    host = settings.rabbitmq_host or "rabbitmq"
    port = settings.rabbitmq_port or 5672
    user = settings.rabbitmq_user or "autohunt"
    password = settings.rabbitmq_password or "autohunt_rabbit_pass"
    exchange = settings.rabbitmq_exchange or "autohunt_notifications"
    queue_name = settings.rabbitmq_queue or "autohunt_notifications_queue"

    credentials = pika.PlainCredentials(user, password)
    parameters = pika.ConnectionParameters(
        host=host,
        port=port,
        credentials=credentials,
        connection_attempts=3,
        retry_delay=2,
        heartbeat=60,
    )

    while _consumer_running:
        try:
            logger.info("Connecting RabbitMQ AMQP consumer to %s:%s...", host, port)
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()

            # Declare exchange and durable queue
            channel.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
            channel.queue_declare(queue=queue_name, durable=True)
            channel.queue_bind(exchange=exchange, queue=queue_name, routing_key="notify.#")

            logger.info("RabbitMQ AMQP consumer active and listening on queue '%s'", queue_name)

            for method_frame, properties, body in channel.consume(queue=queue_name, auto_ack=True, inactivity_timeout=1.0):
                if not _consumer_running:
                    break
                if body:
                    try:
                        data = json.loads(body.decode("utf-8"))
                        _dispatch_received_message(data)
                    except Exception as exc:
                        logger.debug("Failed parsing AMQP message body: %s", exc)

            channel.cancel()
            connection.close()
        except Exception as exc:
            if not _consumer_running:
                break
            logger.debug("RabbitMQ consumer connection interrupted (%s). Retrying in 10s...", exc)
            time.sleep(10)


def _consumer_worker_loop() -> None:
    """Main worker thread running the broker consumer."""
    global _consumer_running
    _consumer_running = True
    broker_type = (settings.message_broker or "rabbitmq").lower()

    if broker_type == "rabbitmq":
        _run_rabbitmq_amqp_consumer()
    elif broker_type == "kafka":
        _run_kafka_consumer()
    else:
        logger.info("Message broker disabled (broker_type=%s). Direct WebSocket hub in use.", broker_type)


def _run_kafka_consumer() -> None:
    """Optional Kafka consumer loop if kafka-python is available."""
    global _consumer_running
    try:
        from kafka import KafkaConsumer
    except ImportError:
        logger.info("kafka-python not installed; Kafka consumer inactive.")
        return

    bootstrap = settings.kafka_bootstrap_servers or "localhost:9092"
    topic = settings.kafka_topic or "autohunt_notifications"

    while _consumer_running:
        try:
            logger.info("Connecting Kafka consumer to %s (topic: %s)...", bootstrap, topic)
            consumer = KafkaConsumer(
                topic,
                bootstrap_servers=bootstrap.split(","),
                auto_offset_reset="latest",
                enable_auto_commit=True,
                group_id="autohunt-notification-group",
                value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                consumer_timeout_ms=1000,
            )
            logger.info("Kafka consumer active on topic '%s'", topic)
            for message in consumer:
                if not _consumer_running:
                    break
                if message and message.value:
                    _dispatch_received_message(message.value)
        except Exception as exc:
            if not _consumer_running:
                break
            logger.debug("Kafka consumer connection error: %s. Retrying in 10s...", exc)
            time.sleep(10)


def start_notification_consumer() -> None:
    """Start background notification consumer thread."""
    global _consumer_thread, _consumer_running
    if _consumer_thread and _consumer_thread.is_alive():
        return
    _consumer_running = True
    _consumer_thread = threading.Thread(
        target=_consumer_worker_loop,
        name="notification-broker-consumer",
        daemon=True,
    )
    _consumer_thread.start()
    logger.info("Background notification broker consumer started.")


def stop_notification_consumer() -> None:
    """Stop background notification consumer thread."""
    global _consumer_running
    _consumer_running = False
