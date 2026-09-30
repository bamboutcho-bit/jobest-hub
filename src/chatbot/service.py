"""Multilingual AI Chatbot & Customer Support Assistant for AutoHunt.

Provides contextual platform guidance in any language (English, French, Arabic,
Spanish, German, etc.) and bridges users with live platform administrators.
"""
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings

logger = logging.getLogger("src.chatbot.service")

SYSTEM_KNOWLEDGE = """You are AutoHunt AI Assistant, the intelligent, friendly, and expert companion for the AutoHunt platform.
Your mission is to guide users step-by-step on how to use every feature of AutoHunt, troubleshoot questions, and explain application workflows.

CRITICAL INSTRUCTIONS:
1. ALWAYS DETECT THE LANGUAGE of the user's message and reply in that EXACT SAME LANGUAGE (e.g. English, French, Arabic / Moroccan Darija, Spanish, German, etc.).
2. Keep explanations clear, actionable, and structured with bullet points and bold highlights.
3. Be encouraging and professional.
4. If the user asks for a human, support agent, or administrator (e.g., "human", "admin", "talk to support", "parler à un humain", "مساعدة من مسؤول"), warmly let them know that platform administration has been notified and can reply directly in this chat!

PLATFORM KNOWLEDGE BASE:
- About AutoHunt: An automated AI job search, matching, and direct-to-inbox auto-apply platform built for software engineers, designers, data specialists, and professionals worldwide.
- Key Tabs & Sections:
  1. Job Postings (#postings): Scrapes and aggregates listings across 120+ public RSS feeds (RemoteOK, Jobicy, Himalayas, Arbeitnow, WeWorkRemotely) + LinkedIn, Indeed, and Google Jobs. Computes an AI Match Score (0-100%) and allows one-click auto-apply.
  2. Profiles & CV (#profiles): Users define job title (e.g. Fullstack, Graphic Designer), target locations, skills matrix, experience level, and upload their resume/CV (PDF/DOCX) for ATS alignment.
  3. Live Pipeline (#live): Shows real-time scraping runs, new postings discovered, AI scoring, and live application dispatch progress.
  4. Recruiter Inbox (#inbox): Automatically synchronizes with the user's email account to identify recruiter responses, interview invitations, and offers.
  5. Outbound Sent (#outbound): Real-time audit log of every email application dispatched, including timestamps and delivery status.
  6. Personal Settings (#user_settings): Connect personal Gmail / SMTP / IMAP credentials (using Gmail App Passwords) so outreach is sent directly from the candidate's real email address. Also configures Telegram alerts.
  7. Freelance Engine (#freelance): Discovers freelance contracts, clients, budget estimates, and generates custom pitches.
  8. Subscriptions & Pricing (#pricing):
     - Free Plan: 5 applications per day.
     - Starter Hunter ($15 / 150 MAD / 14 EUR / 15 USDT per month): 50 applications per day.
     - Pro Hunter & Freelancer ($35 / 350 MAD / 32 EUR / 35 USDT per month): 150 applications per day + Batch HR Discovery + Freelance Deal Engine.
     - Ultra Agency & Executive ($69 / 690 MAD / 65 EUR / 69 USDT per month): Unlimited applications (9,999/day) + 24/7 background engine.
     - Universal Zero-LTD Payments: Crypto (USDT TRC20/Polygon, SOL, BTC), PayPal, Cards (Ko-fi), Wise, Revolut, and Moroccan Bank Wire (CIH / Attijari).
     - Plan Management: Approved users can view their active plan badge in the top navigation header, Candidate Profiles, Settings, and Plans & Billing. They can renew, upgrade, or switch tiers with 1-click at any time!
  9. Admin Center (#admin): Dedicated platform management for administrators (user quotas, payment reviews, and real-time live chat).
"""


class ChatbotService:
    """Handles multilingual conversational assistance with fallback knowledge matcher."""

    def __init__(self):
        self.ollama_base_url = settings.ollama_base_url.rstrip("/")
        self.ollama_model = settings.ollama_model or "llama3.2:3b"

    def detect_language(self, text: str) -> str:
        """Heuristic language detection for fallback responses."""
        if not text:
            return "en"
        # Arabic character range
        if re.search(r"[\u0600-\u06FF]", text):
            return "ar"
        # French cues
        lower = text.lower()
        french_words = ["bonjour", "salut", "comment", "postuler", "aide", "compte", "merci", "abonnement", "prix", "travail", "emploi", "cv"]
        if any(w in lower for w in french_words) or any(c in text for c in "éèêëàâîïôûùç"):
            return "fr"
        # Spanish cues
        spanish_words = ["hola", "cómo", "como", "aplicar", "ayuda", "cuenta", "gracias", "precio", "trabajo", "empleo"]
        if any(w in lower for w in spanish_words) or any(c in text for c in "áéíóúñ¿¡"):
            return "es"
        return "en"

    def check_admin_request(self, text: str) -> bool:
        """Determines if the user is asking for an administrator or human agent."""
        patterns = [
            r"\b(admin|administrator|support agent|human|human agent|talk to human|live agent|person)\b",
            r"\b(humain|administrateur|support technique|parler à un agent|quelqu'un|responsable)\b",
            r"[\u0600-\u06FF]*(مسؤول|إنسان|دعم|مساعدة بشرية|تكلم مع|شخص)[\u0600-\u06FF]*",
            r"\b(humano|persona|administrador|soporte en vivo)\b",
        ]
        lower = text.lower()
        for p in patterns:
            if re.search(p, lower, re.IGNORECASE):
                return True
        return False

    def generate_response(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
        user_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, bool, str]:
        """Generate response via Ollama LLM, with fallback to structured multilingual responder."""
        lang = self.detect_language(user_message)
        needs_admin = self.check_admin_request(user_message)

        # Contextual user details if logged in
        user_context = ""
        if user_info:
            user_context = f"\nUser info: Name: {user_info.get('full_name')}, Plan: {user_info.get('current_plan')}, Role: {user_info.get('role')}."

        # 1. Try Ollama LLM
        llm_reply = self._call_ollama(user_message, history, user_context)
        if llm_reply:
            return llm_reply, needs_admin, lang

        # 2. Try Gemini if configured
        if getattr(settings, "gemini_api_key", None):
            gemini_reply = self._call_gemini(user_message, history, user_context)
            if gemini_reply:
                return gemini_reply, needs_admin, lang

        # 3. Fallback to rich multilingual knowledge matcher
        fallback_reply = self._multilingual_fallback(user_message, lang, needs_admin)
        return fallback_reply, needs_admin, lang

    def _call_ollama(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]],
        user_context: str,
    ) -> Optional[str]:
        """Call Ollama /api/chat with timeout."""
        try:
            messages = [
                {"role": "system", "content": SYSTEM_KNOWLEDGE + user_context}
            ]
            if history:
                for h in history[-6:]:  # Keep recent context
                    role = "assistant" if h.get("sender_role") in ("bot", "admin") else "user"
                    messages.append({"role": role, "content": h.get("message", "")})
            messages.append({"role": "user", "content": user_message})

            url = f"{self.ollama_base_url}/api/chat"
            payload = {
                "model": self.ollama_model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.5,
                    "num_predict": 450,
                },
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data.get("message", {}).get("content", "").strip()
                    if content:
                        return content
        except Exception as exc:
            logger.warning("Ollama chat generation failed or timed out: %s. Using fallback.", exc)
        return None

    def _call_gemini(
        self,
        user_message: str,
        history: Optional[List[Dict[str, str]]],
        user_context: str,
    ) -> Optional[str]:
        """Call Google Gemini API if key is present."""
        try:
            api_key = settings.gemini_api_key
            model = getattr(settings, "gemini_model", "gemini-1.5-flash")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            
            prompt = f"{SYSTEM_KNOWLEDGE}{user_context}\n\nUser Question: {user_message}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
        except Exception as exc:
            logger.warning("Gemini generation skipped: %s", exc)
        return None

    def _multilingual_fallback(self, query: str, lang: str, needs_admin: bool) -> str:
        """Rich predefined responses in French, Arabic, Spanish, and English."""
        lower = query.lower()

        # If user asked for human admin
        if needs_admin:
            if lang == "fr":
                return (
                    "👋 **Un administrateur de la plateforme a été notifié !**\n\n"
                    "Votre demande d'assistance a été transmise à notre équipe. "
                    "Un administrateur peut vous répondre directement dans ce chat en temps réel. "
                    "N'hésitez pas à préciser votre question ou problème ci-dessous !"
                )
            elif lang == "ar":
                return (
                    "👋 **تم إشعار إدارة المنصة بنجاح!**\n\n"
                    "لقد تم إرسال طلبك إلى فريق الإدارة. سيتمكن المسؤول من الرد عليك مباشرة في هذه المحادثة. "
                    "يرجى كتابة أي تفاصيل إضافية حول استفسارك لتسريع مساعدتك."
                )
            elif lang == "es":
                return (
                    "👋 **¡El administrador de la plataforma ha sido notificado!**\n\n"
                    "Tu solicitud ha sido transmitida a nuestro equipo. Un administrador te responderá directamente en este chat. "
                    "¡Puedes dejar más detalles sobre tu consulta a continuación!"
                )
            else:
                return (
                    "👋 **A Platform Administrator has been notified!**\n\n"
                    "Your request has been routed to our support team. An administrator can answer you directly in this chat thread. "
                    "Feel free to share any additional details about your question below!"
                )

        # Plan & Pricing questions
        if any(w in lower for w in ["prix", "tarif", "abonnement", "payer", "mad", "cih", "attijari", "plan", "upgrade", "price", "subscription", "اشتراك", "سعر", "ثمن"]):
            if lang == "fr":
                return (
                    "💳 **Plans & Abonnements Universels AutoHunt :**\n\n"
                    "• **Plan Gratuit :** 5 candidatures automatiques / jour.\n"
                    "• **Starter Hunter ($15 / 150 MAD / mois) :** 50 candidatures / jour.\n"
                    "• **Pro Hunter & Freelance ($35 / 350 MAD / mois) :** 150 candidatures / jour + Détection emails RH + Moteur de missions freelance.\n"
                    "• **Ultra Agency & Executive ($69 / 690 MAD / mois) :** Candidatures illimitées (9 999 / jour) + Automatisation 24/7.\n\n"
                    "🌍 **Moyens de paiement universels (Sans LTD) :**\n"
                    "- **Crypto :** USDT (TRC-20, Polygon), Solana (SOL), Bitcoin (BTC).\n"
                    "- **PayPal & Cartes :** PayPal ou Carte bancaire via Ko-fi.\n"
                    "- **Banque :** Wise, Revolut, ou virement bancaire marocain (CIH / Attijariwafa).\n\n"
                    "🔄 **Afficher & Modifier votre formule :**\n"
                    "Votre plan actif est affiché directement dans l'en-tête (badge), dans **Profils**, et dans l'onglet **💎 Plans & Facturation**. Vous pouvez changer ou renouveler votre formule en 1 clic !"
                )
            elif lang == "ar":
                return (
                    "💳 **خطط وباقات AutoHunt العالمية (متعددة العملات) :**\n\n"
                    "• **الباقة المجانية :** 5 تقديمات تلقائية يومياً.\n"
                    "• **باقة Starter Hunter ($15 / 150 درهم شهرياً) :** 50 تقديماً يومياً.\n"
                    "• **باقة Pro Hunter & Freelance ($35 / 350 درهم شهرياً) :** 150 تقديماً يومياً + كشف إيميلات مسؤولي التوظيف + محرك صفقات العمل الحر.\n"
                    "• **باقة Ultra Agency ($69 / 690 درهم شهرياً) :** تقديمات غير محدودة (9,999 يومياً) + تشغيل آلي 24/7.\n\n"
                    "🌍 **طرق الدفع المتاحة للجميع دون قيود :**\n"
                    "- **العملات الرقمية :** USDT (TRC20/Polygon)، سولانا (SOL)، بيتكوين (BTC).\n"
                    "- **بايبال والبطاقات :** PayPal أو البطاقات البنكية العالمية عبر Ko-fi.\n"
                    "- **التحويلات :** Wise، Revolut، أو تحويل بنكي مغربي (CIH / التجاري وفا بنك).\n\n"
                    "🔄 **معرفة خطتك وتغييرها :**\n"
                    "يظهر نوع باقتك الحالية في أعلى الصفحة، وفي قسم الملف الشخصي، وقسم **Plans & Billing**. يمكنك ترقية أو تجديد أو تغيير باقتك بنقرة واحدة في أي وقت!"
                )
            else:
                return (
                    "💳 **AutoHunt Universal Subscription Plans & Pricing :**\n\n"
                    "• **Free Plan:** 5 automated applications / day.\n"
                    "• **Starter Hunter ($15 / 150 MAD / 14 EUR / month):** 50 applications / day.\n"
                    "• **Pro Hunter & Freelancer ($35 / 350 MAD / 32 EUR / month):** 150 applications / day + HR discovery + Freelance deal engine.\n"
                    "• **Ultra Agency & Executive ($69 / 690 MAD / 65 EUR / month):** Unlimited applications (9,999/day) + 24/7 daemon engine.\n\n"
                    "🌍 **Universal Payment Methods (No-LTD Required):**\n"
                    "- **Crypto:** USDT (TRC-20, Polygon), Solana (SOL), Bitcoin (BTC).\n"
                    "- **PayPal & Cards:** PayPal.me or Debit/Credit Cards via Ko-fi.\n"
                    "- **Transfer:** Wise, Revolut, or Moroccan Bank Wire (CIH / Attijariwafa).\n\n"
                    "🔄 **Show Plan & Switch Anytime:**\n"
                    "Your active plan is always visible in the top navbar badge, Candidate Profiles, Settings, and **💎 Plans & Billing**. You can renew, upgrade, or switch tiers with 1-click anytime!"
                )

        # Auto-apply instructions
        if any(w in lower for w in ["apply", "postuler", "comment", "how to", "marche", "fonctionne", "auto apply", "تقديم", "كيف"]):
            if lang == "fr":
                return (
                    "🚀 **Comment fonctionne la candidature automatique :**\n\n"
                    "1. **Configurez votre profil (#profiles) :** Renseignez vos compétences clés, votre titre visé et importez votre CV.\n"
                    "2. **Connectez votre email (#user_settings) :** Configurez votre adresse Gmail et mot de passe d'application SMTP pour envoyer vos candidatures depuis votre propre boîte.\n"
                    "3. **Trouvez des offres (#postings) :** Lancez la recherche pour scanner plus de 120 flux d'offres d'emploi.\n"
                    "4. **Postulez en un clic :** Cliquez sur **Auto Apply** pour générer une lettre de motivation sur-mesure et envoyer votre CV directement au recruteur !"
                )
            elif lang == "ar":
                return (
                    "🚀 **كيفية تشغيل التقديم التلقائي على الوظائف :**\n\n"
                    "1. **إعداد الملف الشخصي (#profiles):** أدخل مهاراتك ومسماك الوظيفي وحمّل سيرتك الذاتية (CV).\n"
                    "2. **ربط بريدك الإلكتروني (#user_settings):** قم بضبط إعدادات Gmail SMTP لإرسال طلبات التوظيف من إيميلك الحقيقي.\n"
                    "3. **البحث عن عروض العمل (#postings):** افحص أحدث الوظائف المطابقة لمؤهلاتك.\n"
                    "4. **التقديم التلقائي:** اضغط على **Auto Apply** لصياغة رسالة مخصصة لكل شركة وإرسال السيرة الذاتية فوراً!"
                )
            else:
                return (
                    "🚀 **How Automated Applications Work :**\n\n"
                    "1. **Set Up Profile (#profiles):** Add your target title, key skills, and upload your resume/CV.\n"
                    "2. **Connect Outbound Email (#user_settings):** Set up your Gmail SMTP credentials so applications are sent directly from your email.\n"
                    "3. **Explore Job Postings (#postings):** Browse live matching jobs scored by AI.\n"
                    "4. **Auto-Apply:** Click **Auto Apply** to generate tailored recruiter pitches and send your application instantly!"
                )

        # SMTP & Settings
        if any(w in lower for w in ["smtp", "email", "gmail", "imap", "settings", "paramètre", "mot de passe", "إيميل", "بريد"]):
            if lang == "fr":
                return (
                    "⚙️ **Configuration Email & SMTP (#user_settings) :**\n\n"
                    "• Pour envoyer des emails depuis votre compte Gmail personnel :\n"
                    "  1. Activez la **validation en deux étapes** sur votre compte Google.\n"
                    "  2. Créez un **Mot de passe d'application (App Password)** dans la sécurité Google.\n"
                    "  3. Dans l'onglet **Settings**, entrez votre email et collez ce mot de passe de 16 caractères.\n"
                    "  4. Cliquez sur **Test Connection** pour valider !"
                )
            elif lang == "ar":
                return (
                    "⚙️ **إعدادات البريد الإلكتروني و SMTP (#user_settings) :**\n\n"
                    "• لإرسال الطلبات من بريدك الخاص Gmail:\n"
                    "  1. فعّل ميزة التحقق بخطوتين في حساب Google.\n"
                    "  2. أنشئ **كلمة مرور للتطبيقات (App Password)** من إعدادات الأمان في Google.\n"
                    "  3. في تبويب **Settings**، أدخل بريدك وكلمة مرور التطبيق المكونة من 16 حرفاً.\n"
                    "  4. اضغط على **Test Connection** للتأكد من نجاح الاتصال!"
                )
            else:
                return (
                    "⚙️ **Email & SMTP Configuration (#user_settings) :**\n\n"
                    "• To send job applications from your personal Gmail account:\n"
                    "  1. Enable 2-Step Verification in your Google Account security.\n"
                    "  2. Generate a 16-character **App Password** for Mail.\n"
                    "  3. In the **Settings** tab, enter your Gmail address and paste the App Password.\n"
                    "  4. Click **Test Connection** to verify delivery!"
                )

        # General welcoming assistance
        if lang == "fr":
            return (
                "👋 **Bonjour ! Je suis votre assistant AutoHunt.**\n\n"
                "Je peux vous aider à :\n"
                "• Découvrir comment configurer vos profils et CV pour maximiser vos matchs.\n"
                "• Configurer votre envoi d'emails SMTP pour postuler automatiquement.\n"
                "• Comprendre les abonnements et les quotas journaliers (Free, Starter, Pro).\n"
                "• Vous connecter directement avec l'administrateur de la plateforme.\n\n"
                "Posez-moi votre question en toute simplicité !"
            )
        elif lang == "ar":
            return (
                "👋 **أهلاً بك! أنا المساعد الذكي لمنصة AutoHunt.**\n\n"
                "يمكنني مساعدتك في:\n"
                "• ضبط ملفك الشخصي والسيرة الذاتية لزيادة نسبة التوافق مع الوظائف.\n"
                "• تفعيل التقديم التلقائي على الوظائف وربط البريد الإلكتروني SMTP.\n"
                "• شرح الباقات المتاحة وطرق الدفع بالدرهم المغربي.\n"
                "• التواصل المباشر مع إدارة المنصة إذا واجهت أي استفسار خاص.\n\n"
                "تفضل بسؤالك وسأجيبك فوراً!"
            )
        else:
            return (
                "👋 **Hello! I am your AutoHunt AI Assistant.**\n\n"
                "I can assist you with:\n"
                "• Setting up your candidate profile and optimizing your CV for ATS scoring.\n"
                "• Configuring automated application workflows and personal SMTP sending.\n"
                "• Choosing subscription plans (Free, Starter, Pro) and quotas.\n"
                "• Reaching an administrator for custom support.\n\n"
                "Ask me anything in any language — I am here to help!"
            )


chatbot_service = ChatbotService()
