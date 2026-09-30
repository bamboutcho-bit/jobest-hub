"""Centralized, environment-driven runtime configuration for the job-search system."""
# pyrefly: ignore [missing-import]
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Admin account (synced on startup from .env)
    admin_email: str = "admin@autohunt.internal"
    admin_password: str = "changeme"

    # AI: Multi-tier Hybrid (Gemini, Ollama, Claude, Heuristic)
    ai_provider: str = "ollama"
    ai_fallback_providers: str = "gemini,heuristic"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-1.5-flash"
    claude_api_key: str = ""
    claude_model: str = "claude-3-5-sonnet-20241022"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ai_request_timeout_seconds: int = 180
    ai_transient_retry_count: int = 1
    ai_transient_retry_delay_seconds: int = 2
    max_ai_calls_per_day: int = 150
    max_ai_calls_per_run: int = 30
    ai_cooldown_base_seconds: int = 30
    ai_cooldown_max_seconds: int = 1800

    database_url: str = "sqlite:///./jobsearch.db"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notify_only_interviews: bool = True

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_alerts_enabled: bool = False
    alert_email_to: str = ""

    # Universal Multi-Method Payments (No LTD required — Individuals, Freelancers & Worldwide)
    payment_usdt_trc20_wallet: str = "TKr4pY4sVq4H2L5K8PqXjF6hS9wE3aM1bN"
    payment_usdt_polygon_wallet: str = "0x71C8360662A3F36116316AC603e5491176f16183"
    payment_solana_wallet: str = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    payment_btc_wallet: str = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh"
    payment_paypal_me_url: str = "https://paypal.me/AutoHuntAI"
    payment_paypal_email: str = "hamzaoukhouyaxx@gmail.com"
    payment_card_checkout_url: str = "https://ko-fi.com/autohunt"
    payment_wise_email: str = "hamzaoukhouyaxx@gmail.com"
    payment_revolut_tag: str = "@hamzaoukhouya"
    payment_cih_rib: str = "230 780 0000000000000000 00"
    payment_attijari_rib: str = "007 780 0000000000000000 00"
    payment_cashplus_info: str = "Hamza Oukhouya (Casablanca, Morocco)"

    # Event & Notification Message Broker (RabbitMQ / Apache Kafka)
    message_broker: str = "rabbitmq"
    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_management_port: int = 15672
    rabbitmq_user: str = "autohunt"
    rabbitmq_password: str = "autohunt_rabbit_pass"
    rabbitmq_exchange: str = "autohunt_notifications"
    rabbitmq_queue: str = "autohunt_notifications_queue"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "autohunt_notifications"

    # Discovery: broad but bounded. JobSpy itself fans out to supported boards.
    min_match_score: int = 65
    min_visa_confidence: bool = False
    results_wanted_per_query: int = 20
    hours_old: int = 120
    scrape_workers: int = 4
    scrape_sites: str = "linkedin,indeed,google"
    public_feed_sources: str = "remoteok,jobicy,arbeitnow,arbeitnow_uk,himalayas,weworkremotely,himalayas_rss"
    public_feed_limit: int = 120
    linkedin_fetch_description: bool = False
    evaluation_backlog_limit_per_run: int = 30
    jobspy_proxies: str = ""
    jobspy_delay_seconds: float = 0.0

    # Public-web expansion: follow employer/ATS career pages discovered from seed jobs.
    # This widens discovery beyond job boards without bypassing access controls.
    open_web_discovery_enabled: bool = True
    open_web_max_domains: int = 35
    open_web_max_pages_per_domain: int = 18
    open_web_sitemap_limit: int = 40
    open_web_max_total_pages: int = 500
    open_web_workers: int = 6
    open_web_delay_seconds: float = 0.5

    # Kept for compatibility with earlier releases; it is a soft local relevance check.
    claude_prefilter_enabled: bool = False
    max_claude_calls_per_run: int = 30
    max_claude_calls_per_day: int = 150
    visa_signal_keywords: str = (
        "visa sponsorship,visa sponsor,sponsor visa,work permit,work authorization,"
        "relocation,relocation package,relocation support,immigration,eu blue card,"
        "skilled worker,visa support,sponsorship available,global mobility"
    )
    alert_on_zero_scrape: bool = True
    min_expected_scrape_results: int = 1

    # Application routing.
    auto_apply_mode: str = "send"
    auto_apply_web_enabled: bool = True
    auto_apply_known_ats_only: bool = True
    auto_apply_min_score: int = 75
    # Do not require an explicit visa phrase before considering a strong role.
    # Ollama must still avoid misrepresenting work authorization.
    auto_apply_require_visa_or_remote: bool = False
    auto_apply_allow_missing_resume: bool = False
    allow_personal_application_recipient_domains: bool = False

    candidate_phone: str = ""
    candidate_linkedin_url: str = ""
    candidate_github_url: str = ""
    candidate_portfolio_url: str = ""
    candidate_resume_path: str = "/app/candidate_data/resume.pdf"
    candidate_cover_letter_path: str = ""

    # Browser automation.
    apply_page_timeout_seconds: int = 40
    apply_headless: bool = True
    apply_screenshot_dir: str = "/app/outreach_drafts/application_screenshots"
    playwright_ws_endpoint: str = "ws://browser:3000/browser"

    auto_reply_mode: str = "draft"
    auto_negotiate_mode: str = "draft"

    sender_email: str = ""
    sender_smtp_host: str = ""
    sender_smtp_port: int = 587
    sender_smtp_password: str = ""
    sender_display_name: str = ""

    imap_host: str = ""
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""
    imap_folder: str = "INBOX"
    inbox_poll_minutes: int = 15
    follow_up_after_days: int = 7
    max_follow_ups: int = 2

    candidate_timezone: str = "Africa/Casablanca"
    availability_notes: str = (
        "Weekdays 14:00-19:00 and weekends, Morocco time (GMT+1); "
        "flexible for early-morning CET/GST calls with notice."
    )

    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8080
    dashboard_username: str = "admin"
    dashboard_password: str = "local-admin-change-me"

    # Email/application safety. Keep external sending off until the user explicitly enables it.
    outbound_send_enabled: bool = False
    max_outbound_emails_per_day: int = 2000
    max_outbound_emails_per_hour: int = 250
    max_applications_per_day: int = 2000
    min_seconds_between_external_emails: int = 5
    outbound_min_body_chars: int = 120
    outbound_max_urls: int = 2
    outbound_max_exclamation_marks: int = 2
    outbound_send_kill_switch: bool = False

    app_environment: str = "production"
    startup_require_dashboard_password: bool = True
    pipeline_interval_hours: int = 3

    # Freelance & Client Acquisition Engine.
    freelance_enabled: bool = True
    freelance_discovery_sources: str = "hackernews,reddit,remoteok,jobicy,weworkremotely,google"
    freelance_max_leads_per_scan: int = 100
    freelance_min_score: int = 60
    freelance_auto_pitch: bool = False  # When True, send pitches automatically for high-scoring leads
    freelance_auto_pitch_min_score: int = 75
    freelance_max_pitches_per_day: int = 5
    freelance_max_pitches_per_hour: int = 2
    freelance_follow_up_1_days: int = 3
    freelance_follow_up_2_days: int = 7
    freelance_max_follow_ups: int = 2
    freelance_hourly_rate_usd: int = 50
    freelance_daily_rate_eur: int = 400
    freelance_preferred_currency: str = "EUR"
    calendar_booking_url: str = ""  # e.g. https://cal.com/your-name (free booking link)

    # HR & Talent Acquisition Email Discovery & Scraping
    enable_hr_enrichment: bool = True
    hunter_api_key: str = ""
    apollo_api_key: str = ""
    hr_max_enrich_per_run: int = 20


settings = Settings()

