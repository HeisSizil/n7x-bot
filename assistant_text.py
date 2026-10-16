"""
assistant_text.py - Everything the mod assistant (and tagged-question replies) say, in several languages.

The confirmation text is always built by CODE from the checked plan (never
written by the AI), so what the mod confirms is exactly what will run.

To add a language: copy the "en" block, translate the right-hand side,
and keep the {placeholders} unchanged. Other languages are translated by the AI
once (see ensure_language) and fall back to English if that fails.
Mod-log notes always stay in English.
"""

import asyncio
import json
import logging
import re
import string
import time

log = logging.getLogger("n7x.assistant")

TEXTS = {
    "en": {
        "what_to_do": "What would you like to do with {member}? For example:",
        "opt_show_warnings": "show their warnings",
        "opt_warn": "warn them (say why)",
        "opt_timeout": "time them out (say for how long)",
        "opt_remove_timeout": "remove their timeout",
        "opt_clear_warnings": "clear their warnings",
        "opt_kick": "kick them (say why)",
        "opt_cancel_scheduled": "cancel their scheduled steps",
        "cancel_scheduled": "Cancel {count} scheduled step(s) for {member}",
        "no_scheduled": "{member} has no scheduled steps to cancel.",
        "cancel_alone": "Cancelling scheduled steps can't be combined with other steps or delayed. Ask for it on its own.",
        "not_understood": "I didn't understand that, try rephrasing.",
        "unsure": "I'm not sure what you meant, so I did nothing. Did you mean:",
        "rephrase": "Please rephrase your request.",
        "need_mention": "Please **@mention** the member (or paste their user ID) so I know exactly who you mean.",
        "need_reason": "Please include a reason, e.g. `warn @user for spamming links`.",
        "need_channel": "Please mention a text channel like #general.",
        "too_many_steps": "I can do at most {max} steps at once. Please split your request.",
        "delay_too_long": "Delays can be at most 7 days.",
        "timeout_range": "Timeouts can be 1 minute to 28 days.",
        "readonly_alone": "Showing warnings and summaries can't be combined with other steps or delayed. Ask for them on their own.",
        "after_kick": "{member} is kicked in step {n}, so no step can come after that for them.",
        "not_timed_out": "{member} isn't timed out, and no earlier step in this request times them out.",
        "no_warnings": "{member} has no warnings to clear, and no earlier step in this request warns them.",
        "warning_number_high": "{member} only has {count} warning(s).",
        "member_missing": "I can't find that member in this server.",
        "blocked": "I did nothing, because step {n} isn't allowed: {problem}",
        "confirm_title": "**Please confirm this plan:**",
        "only_you": "_Only {mod} can confirm. Expires in {sec}s._",
        "starts_now": "(starts now)",
        "now": "(now)",
        "after": "(after {delay})",
        "warn": "Warn {member}",
        "clear_all": "Clear ALL warnings from {member}",
        "clear_one": "Clear warning #{number} from {member}",
        "timeout": "Timeout {member} for {duration}",
        "remove_timeout": "Remove {member}'s timeout",
        "kick": "Kick {member}",
        "reason": " — reason: {reason}",
        "note_removed_early": "ℹ️ The timeout would run for {duration}, but it will be removed after {after}.",
        "note_ends_first": "ℹ️ The timeout ends after {duration}, before step {n} runs, so step {n} may have nothing to remove.",
        "note_replaced": "ℹ️ Step {n} replaces the timeout from step {m}.",
        "working": "⏳ Working...",
        "cancelled": "✖️ Cancelled. Nothing was done.",
        "expired": "⌛ Expired after {sec}s. Nothing was done.",
        "only_asker": "Only the mod who asked can use these buttons.",
        "step_done": "Step {n}: ✅ {result}",
        "step_failed": "Step {n}: ❌ {result}",
        "step_skipped": "Step {n}: ⏭️ skipped, because step {m} didn't succeed.",
        "step_scheduled": "Step {n}: ⏰ scheduled for {when}.",
        "scheduled_report": "⏰ Scheduled step from {mod}'s plan:",
        "member_left": "{member} is no longer in the server.",
        "mod_left": "The mod who made this plan is no longer in the server.",
        "not_timed_out_now": "{member} isn't timed out anymore, so there was nothing to remove.",
        "chat_only_mods": "Sorry, only mods can do that. 🙂 I'm happy to answer your questions about AI, Web3, crypto or N7X instead!",
        "chat_hello": "👋 Hi! Ask me anything about AI agents, Web3, crypto or N7X. For example: `what is an AI agent?` or `what is N7X building?`",
        "chat_rephrase": "🤔 I'm not sure how to answer that. Could you rephrase it?",
    },
    "es": {
        "what_to_do": "¿Qué quieres hacer con {member}? Por ejemplo:",
        "opt_show_warnings": "ver sus advertencias",
        "opt_warn": "advertirle (di por qué)",
        "opt_timeout": "aislarlo (di por cuánto tiempo)",
        "opt_remove_timeout": "quitarle el aislamiento",
        "opt_clear_warnings": "borrar sus advertencias",
        "opt_kick": "expulsarlo (di por qué)",
        "opt_cancel_scheduled": "cancelar sus pasos programados",
        "cancel_scheduled": "Cancelar {count} paso(s) programado(s) para {member}",
        "no_scheduled": "{member} no tiene pasos programados que cancelar.",
        "cancel_alone": "Cancelar pasos programados no se puede combinar con otros pasos ni retrasar. Pídelo por separado.",
        "not_understood": "No entendí eso, intenta reformularlo.",
        "unsure": "No estoy seguro de lo que querías decir, así que no hice nada. ¿Quisiste decir:",
        "rephrase": "Por favor, reformula tu solicitud.",
        "need_mention": "Por favor, **@menciona** al miembro (o pega su ID de usuario) para saber exactamente a quién te refieres.",
        "need_reason": "Por favor, incluye un motivo, p. ej. `warn @usuario por enviar enlaces spam`.",
        "need_channel": "Por favor, menciona un canal de texto como #general.",
        "too_many_steps": "Puedo hacer como máximo {max} pasos a la vez. Divide tu solicitud.",
        "delay_too_long": "Los retrasos pueden ser de 7 días como máximo.",
        "timeout_range": "Los aislamientos pueden durar de 1 minuto a 28 días.",
        "readonly_alone": "Mostrar advertencias y resúmenes no se puede combinar con otros pasos ni retrasar. Pídelos por separado.",
        "after_kick": "{member} es expulsado en el paso {n}, así que no puede haber más pasos para él después.",
        "not_timed_out": "{member} no está aislado y ningún paso anterior de esta solicitud lo aísla.",
        "no_warnings": "{member} no tiene advertencias que borrar y ningún paso anterior de esta solicitud lo advierte.",
        "warning_number_high": "{member} solo tiene {count} advertencia(s).",
        "member_missing": "No encuentro a ese miembro en este servidor.",
        "blocked": "No hice nada, porque el paso {n} no está permitido: {problem}",
        "confirm_title": "**Por favor, confirma este plan:**",
        "only_you": "_Solo {mod} puede confirmar. Caduca en {sec}s._",
        "starts_now": "(empieza ahora)",
        "now": "(ahora)",
        "after": "(dentro de {delay})",
        "warn": "Advertir a {member}",
        "clear_all": "Borrar TODAS las advertencias de {member}",
        "clear_one": "Borrar la advertencia n.º {number} de {member}",
        "timeout": "Aislar a {member} durante {duration}",
        "remove_timeout": "Quitar el aislamiento de {member}",
        "kick": "Expulsar a {member}",
        "reason": " — motivo: {reason}",
        "note_removed_early": "ℹ️ El aislamiento duraría {duration}, pero se quitará después de {after}.",
        "note_ends_first": "ℹ️ El aislamiento termina después de {duration}, antes del paso {n}, así que el paso {n} puede no tener nada que quitar.",
        "note_replaced": "ℹ️ El paso {n} reemplaza el aislamiento del paso {m}.",
        "working": "⏳ Trabajando...",
        "cancelled": "✖️ Cancelado. No se hizo nada.",
        "expired": "⌛ Caducó después de {sec}s. No se hizo nada.",
        "only_asker": "Solo el moderador que lo pidió puede usar estos botones.",
        "step_done": "Paso {n}: ✅ {result}",
        "step_failed": "Paso {n}: ❌ {result}",
        "step_skipped": "Paso {n}: ⏭️ omitido, porque el paso {m} no tuvo éxito.",
        "step_scheduled": "Paso {n}: ⏰ programado para {when}.",
        "scheduled_report": "⏰ Paso programado del plan de {mod}:",
        "member_left": "{member} ya no está en el servidor.",
        "mod_left": "El moderador que hizo este plan ya no está en el servidor.",
        "not_timed_out_now": "{member} ya no está aislado, así que no había nada que quitar.",
        "chat_only_mods": "Lo siento, solo los moderadores pueden hacer eso. 🙂 ¡Con gusto respondo tus preguntas sobre IA, Web3, cripto o N7X!",
        "chat_hello": "👋 ¡Hola! Pregúntame lo que quieras sobre agentes de IA, Web3, cripto o N7X. Por ejemplo: `¿qué es un agente de IA?` o `¿qué está construyendo N7X?`",
        "chat_rephrase": "🤔 No sé bien cómo responder a eso. ¿Puedes decirlo de otra forma?",
    },
    "fr": {
        "what_to_do": "Que veux-tu faire avec {member} ? Par exemple :",
        "opt_show_warnings": "voir ses avertissements",
        "opt_warn": "l'avertir (dis pourquoi)",
        "opt_timeout": "l'exclure temporairement (dis pour combien de temps)",
        "opt_remove_timeout": "lever son exclusion",
        "opt_clear_warnings": "effacer ses avertissements",
        "opt_kick": "l'expulser (dis pourquoi)",
        "opt_cancel_scheduled": "annuler ses étapes programmées",
        "cancel_scheduled": "Annuler {count} étape(s) programmée(s) pour {member}",
        "no_scheduled": "{member} n'a aucune étape programmée à annuler.",
        "cancel_alone": "Annuler des étapes programmées ne peut pas être combiné à d'autres étapes ni retardé. Demande-le séparément.",
        "not_understood": "Je n'ai pas compris, essaie de reformuler.",
        "unsure": "Je ne suis pas sûr de ce que tu voulais dire, donc je n'ai rien fait. Voulais-tu dire :",
        "rephrase": "Merci de reformuler ta demande.",
        "need_mention": "Merci de **@mentionner** le membre (ou de coller son ID) pour que je sache exactement de qui tu parles.",
        "need_reason": "Merci d'indiquer une raison, p. ex. `warn @membre pour spam de liens`.",
        "need_channel": "Merci de mentionner un salon textuel comme #general.",
        "too_many_steps": "Je peux faire au maximum {max} étapes à la fois. Merci de diviser ta demande.",
        "delay_too_long": "Les délais peuvent être de 7 jours au maximum.",
        "timeout_range": "Les exclusions temporaires vont de 1 minute à 28 jours.",
        "readonly_alone": "Afficher les avertissements ou un résumé ne peut pas être combiné à d'autres étapes ni retardé. Demande-les séparément.",
        "after_kick": "{member} est expulsé à l'étape {n}, donc aucune étape ne peut suivre pour ce membre.",
        "not_timed_out": "{member} n'est pas exclu temporairement, et aucune étape précédente de cette demande ne l'exclut.",
        "no_warnings": "{member} n'a aucun avertissement à effacer, et aucune étape précédente de cette demande ne l'avertit.",
        "warning_number_high": "{member} n'a que {count} avertissement(s).",
        "member_missing": "Je ne trouve pas ce membre sur ce serveur.",
        "blocked": "Je n'ai rien fait, car l'étape {n} n'est pas autorisée : {problem}",
        "confirm_title": "**Merci de confirmer ce plan :**",
        "only_you": "_Seul {mod} peut confirmer. Expire dans {sec} s._",
        "starts_now": "(commence maintenant)",
        "now": "(maintenant)",
        "after": "(dans {delay})",
        "warn": "Avertir {member}",
        "clear_all": "Effacer TOUS les avertissements de {member}",
        "clear_one": "Effacer l'avertissement n° {number} de {member}",
        "timeout": "Exclure temporairement {member} pendant {duration}",
        "remove_timeout": "Lever l'exclusion de {member}",
        "kick": "Expulser {member}",
        "reason": " — raison : {reason}",
        "note_removed_early": "ℹ️ L'exclusion durerait {duration}, mais elle sera levée après {after}.",
        "note_ends_first": "ℹ️ L'exclusion se termine après {duration}, avant l'étape {n}, donc l'étape {n} n'aura peut-être rien à lever.",
        "note_replaced": "ℹ️ L'étape {n} remplace l'exclusion de l'étape {m}.",
        "working": "⏳ En cours...",
        "cancelled": "✖️ Annulé. Rien n'a été fait.",
        "expired": "⌛ Expiré après {sec} s. Rien n'a été fait.",
        "only_asker": "Seul le modérateur qui a fait la demande peut utiliser ces boutons.",
        "step_done": "Étape {n} : ✅ {result}",
        "step_failed": "Étape {n} : ❌ {result}",
        "step_skipped": "Étape {n} : ⏭️ ignorée, car l'étape {m} n'a pas réussi.",
        "step_scheduled": "Étape {n} : ⏰ prévue pour {when}.",
        "scheduled_report": "⏰ Étape programmée du plan de {mod} :",
        "member_left": "{member} n'est plus sur le serveur.",
        "mod_left": "Le modérateur qui a fait ce plan n'est plus sur le serveur.",
        "not_timed_out_now": "{member} n'est plus exclu, il n'y avait donc rien à lever.",
        "chat_only_mods": "Désolé, seuls les modérateurs peuvent faire ça. 🙂 Je réponds volontiers à tes questions sur l'IA, le Web3, la crypto ou N7X !",
        "chat_hello": "👋 Salut ! Pose-moi tes questions sur les agents IA, le Web3, la crypto ou N7X. Par exemple : `c'est quoi un agent IA ?` ou `que construit N7X ?`",
        "chat_rephrase": "🤔 Je ne sais pas trop comment répondre. Peux-tu reformuler ?",
    },
    "pt": {
        "what_to_do": "O que você quer fazer com {member}? Por exemplo:",
        "opt_show_warnings": "ver as advertências",
        "opt_warn": "advertir (diga o motivo)",
        "opt_timeout": "colocar de castigo (diga por quanto tempo)",
        "opt_remove_timeout": "remover o castigo",
        "opt_clear_warnings": "apagar as advertências",
        "opt_kick": "expulsar (diga o motivo)",
        "opt_cancel_scheduled": "cancelar as etapas agendadas",
        "cancel_scheduled": "Cancelar {count} etapa(s) agendada(s) para {member}",
        "no_scheduled": "{member} não tem etapas agendadas para cancelar.",
        "cancel_alone": "Cancelar etapas agendadas não pode ser combinado com outras etapas nem atrasado. Peça separadamente.",
        "not_understood": "Não entendi, tente reformular.",
        "unsure": "Não tenho certeza do que você quis dizer, então não fiz nada. Você quis dizer:",
        "rephrase": "Por favor, reformule o seu pedido.",
        "need_mention": "Por favor, **@mencione** o membro (ou cole o ID dele) para eu saber exatamente de quem se trata.",
        "need_reason": "Por favor, inclua um motivo, ex.: `warn @usuário por spam de links`.",
        "need_channel": "Por favor, mencione um canal de texto como #general.",
        "too_many_steps": "Posso fazer no máximo {max} etapas de uma vez. Divida o seu pedido.",
        "delay_too_long": "Os atrasos podem ser de no máximo 7 dias.",
        "timeout_range": "Os castigos podem durar de 1 minuto a 28 dias.",
        "readonly_alone": "Mostrar advertências e resumos não pode ser combinado com outras etapas nem atrasado. Peça separadamente.",
        "after_kick": "{member} é expulso na etapa {n}, então nenhuma etapa pode vir depois para ele.",
        "not_timed_out": "{member} não está de castigo, e nenhuma etapa anterior deste pedido o coloca de castigo.",
        "no_warnings": "{member} não tem advertências para apagar, e nenhuma etapa anterior deste pedido o adverte.",
        "warning_number_high": "{member} só tem {count} advertência(s).",
        "member_missing": "Não encontro esse membro neste servidor.",
        "blocked": "Não fiz nada, porque a etapa {n} não é permitida: {problem}",
        "confirm_title": "**Por favor, confirme este plano:**",
        "only_you": "_Só {mod} pode confirmar. Expira em {sec}s._",
        "starts_now": "(começa agora)",
        "now": "(agora)",
        "after": "(daqui a {delay})",
        "warn": "Advertir {member}",
        "clear_all": "Apagar TODAS as advertências de {member}",
        "clear_one": "Apagar a advertência nº {number} de {member}",
        "timeout": "Castigar {member} por {duration}",
        "remove_timeout": "Remover o castigo de {member}",
        "kick": "Expulsar {member}",
        "reason": " — motivo: {reason}",
        "note_removed_early": "ℹ️ O castigo duraria {duration}, mas será removido depois de {after}.",
        "note_ends_first": "ℹ️ O castigo termina depois de {duration}, antes da etapa {n}, então a etapa {n} pode não ter nada para remover.",
        "note_replaced": "ℹ️ A etapa {n} substitui o castigo da etapa {m}.",
        "working": "⏳ Trabalhando...",
        "cancelled": "✖️ Cancelado. Nada foi feito.",
        "expired": "⌛ Expirou após {sec}s. Nada foi feito.",
        "only_asker": "Só o moderador que pediu pode usar estes botões.",
        "step_done": "Etapa {n}: ✅ {result}",
        "step_failed": "Etapa {n}: ❌ {result}",
        "step_skipped": "Etapa {n}: ⏭️ ignorada, porque a etapa {m} não deu certo.",
        "step_scheduled": "Etapa {n}: ⏰ agendada para {when}.",
        "scheduled_report": "⏰ Etapa agendada do plano de {mod}:",
        "member_left": "{member} não está mais no servidor.",
        "mod_left": "O moderador que fez este plano não está mais no servidor.",
        "not_timed_out_now": "{member} não está mais de castigo, então não havia nada para remover.",
        "chat_only_mods": "Desculpe, só os moderadores podem fazer isso. 🙂 Mas respondo com prazer suas perguntas sobre IA, Web3, cripto ou N7X!",
        "chat_hello": "👋 Oi! Pergunte o que quiser sobre agentes de IA, Web3, cripto ou N7X. Por exemplo: `o que é um agente de IA?` ou `o que a N7X está construindo?`",
        "chat_rephrase": "🤔 Não sei bem como responder isso. Pode reformular?",
    },
    "de": {
        "what_to_do": "Was möchtest du mit {member} machen? Zum Beispiel:",
        "opt_show_warnings": "Verwarnungen anzeigen",
        "opt_warn": "verwarnen (sag warum)",
        "opt_timeout": "ein Timeout geben (sag wie lange)",
        "opt_remove_timeout": "das Timeout aufheben",
        "opt_clear_warnings": "Verwarnungen löschen",
        "opt_kick": "kicken (sag warum)",
        "opt_cancel_scheduled": "geplante Schritte abbrechen",
        "cancel_scheduled": "{count} geplante(n) Schritt(e) für {member} abbrechen",
        "no_scheduled": "{member} hat keine geplanten Schritte zum Abbrechen.",
        "cancel_alone": "Geplante Schritte abbrechen kann nicht mit anderen Schritten kombiniert oder verzögert werden. Frag separat danach.",
        "not_understood": "Das habe ich nicht verstanden, bitte formuliere es anders.",
        "unsure": "Ich bin nicht sicher, was du meinst, daher habe ich nichts getan. Meintest du:",
        "rephrase": "Bitte formuliere deine Anfrage neu.",
        "need_mention": "Bitte **@erwähne** das Mitglied (oder füge seine Nutzer-ID ein), damit ich genau weiß, wen du meinst.",
        "need_reason": "Bitte gib einen Grund an, z. B. `warn @nutzer wegen Link-Spam`.",
        "need_channel": "Bitte erwähne einen Textkanal wie #general.",
        "too_many_steps": "Ich kann höchstens {max} Schritte auf einmal ausführen. Bitte teile deine Anfrage auf.",
        "delay_too_long": "Verzögerungen dürfen höchstens 7 Tage betragen.",
        "timeout_range": "Timeouts können 1 Minute bis 28 Tage dauern.",
        "readonly_alone": "Verwarnungen anzeigen und Zusammenfassungen können nicht mit anderen Schritten kombiniert oder verzögert werden. Frag separat danach.",
        "after_kick": "{member} wird in Schritt {n} gekickt, danach kann kein Schritt mehr für dieses Mitglied folgen.",
        "not_timed_out": "{member} hat kein Timeout, und kein früherer Schritt dieser Anfrage gibt ein Timeout.",
        "no_warnings": "{member} hat keine Verwarnungen zum Löschen, und kein früherer Schritt dieser Anfrage verwarnt das Mitglied.",
        "warning_number_high": "{member} hat nur {count} Verwarnung(en).",
        "member_missing": "Ich finde dieses Mitglied auf diesem Server nicht.",
        "blocked": "Ich habe nichts getan, weil Schritt {n} nicht erlaubt ist: {problem}",
        "confirm_title": "**Bitte bestätige diesen Plan:**",
        "only_you": "_Nur {mod} kann bestätigen. Läuft in {sec} s ab._",
        "starts_now": "(beginnt jetzt)",
        "now": "(jetzt)",
        "after": "(in {delay})",
        "warn": "{member} verwarnen",
        "clear_all": "ALLE Verwarnungen von {member} löschen",
        "clear_one": "Verwarnung Nr. {number} von {member} löschen",
        "timeout": "{member} für {duration} ein Timeout geben",
        "remove_timeout": "Das Timeout von {member} aufheben",
        "kick": "{member} kicken",
        "reason": " — Grund: {reason}",
        "note_removed_early": "ℹ️ Das Timeout würde {duration} dauern, wird aber nach {after} aufgehoben.",
        "note_ends_first": "ℹ️ Das Timeout endet nach {duration}, vor Schritt {n}, daher hat Schritt {n} eventuell nichts aufzuheben.",
        "note_replaced": "ℹ️ Schritt {n} ersetzt das Timeout aus Schritt {m}.",
        "working": "⏳ Wird ausgeführt...",
        "cancelled": "✖️ Abgebrochen. Es wurde nichts getan.",
        "expired": "⌛ Nach {sec} s abgelaufen. Es wurde nichts getan.",
        "only_asker": "Nur der Mod, der gefragt hat, kann diese Knöpfe benutzen.",
        "step_done": "Schritt {n}: ✅ {result}",
        "step_failed": "Schritt {n}: ❌ {result}",
        "step_skipped": "Schritt {n}: ⏭️ übersprungen, weil Schritt {m} nicht geklappt hat.",
        "step_scheduled": "Schritt {n}: ⏰ geplant für {when}.",
        "scheduled_report": "⏰ Geplanter Schritt aus dem Plan von {mod}:",
        "member_left": "{member} ist nicht mehr auf dem Server.",
        "mod_left": "Der Mod, der diesen Plan erstellt hat, ist nicht mehr auf dem Server.",
        "not_timed_out_now": "{member} hat kein Timeout mehr, es gab also nichts aufzuheben.",
        "chat_only_mods": "Sorry, das können nur Mods. 🙂 Deine Fragen zu KI, Web3, Krypto oder N7X beantworte ich aber gern!",
        "chat_hello": "👋 Hi! Frag mich alles über KI-Agenten, Web3, Krypto oder N7X. Zum Beispiel: `Was ist ein KI-Agent?` oder `Was baut N7X?`",
        "chat_rephrase": "🤔 Ich weiß nicht genau, wie ich das beantworten soll. Kannst du es anders formulieren?",
    },
}

SUPPORTED = set(TEXTS)

# Other languages (e.g. "yo", "ha", "ig", "pcm", "ja") are translated by the AI the
# first time they're needed, then kept here in memory until the bot restarts.
TRANSLATED = {}        # language -> {key: translated sentence}
_failed_at = {}        # language -> time of the last failed translation (to avoid retrying constantly)
_locks = {}            # language -> asyncio.Lock (so two requests don't translate at once)
RETRY_FAILED_AFTER = 600  # seconds

TRANSLATE_PROMPT = """You translate the user-interface sentences of a Discord moderation bot.
You get a JSON object of English sentences. Reply with ONLY a JSON object with the SAME keys,
each value translated into the language with code "{language}".
Rules: keep every {{placeholder}} in curly braces EXACTLY as it is (do not translate them);
keep emoji, **bold**, _italics_, `code`, numbers and line breaks; keep it short and natural."""


def normalize_language(code):
    """'pt-BR' -> 'pt', 'PCM' -> 'pcm'. Anything that isn't a 2-3 letter code -> 'en'."""
    code = str(code or "").strip().lower().replace("_", "-").split("-")[0]
    return code if code.isalpha() and 2 <= len(code) <= 3 else "en"


def _placeholders(text):
    return sorted(name for _, name, _, _ in string.Formatter().parse(text) if name)


async def ensure_language(language, ai):
    """
    Make sure sentences exist for `language`. Built-in languages need nothing.
    Other languages are translated ONCE through the AI chain and cached.
    If that fails, t() simply falls back to English.
    """
    if language in SUPPORTED or language in TRANSLATED:
        return
    if time.time() - _failed_at.get(language, 0) < RETRY_FAILED_AFTER:
        return
    lock = _locks.setdefault(language, asyncio.Lock())
    async with lock:
        if language in TRANSLATED:
            return
        english = TEXTS["en"]
        try:
            answer, source = await ai.ask(TRANSLATE_PROMPT.format(language=language),
                                          json.dumps(english, ensure_ascii=False), json_mode=True, max_tokens=4000)
            data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip()))
        except Exception as error:  # AI down or bad JSON -> English for now
            _failed_at[language] = time.time()
            log.warning("Couldn't translate bot sentences into %r (%s); using English", language, type(error).__name__)
            return
        good = {}
        for key, text in english.items():
            value = data.get(key) if isinstance(data, dict) else None
            # Only keep a translation if its {placeholders} match the English exactly.
            if isinstance(value, str) and value.strip() and _placeholders(value) == _placeholders(text):
                good[key] = value
        TRANSLATED[language] = good
        log.info("Translated %d/%d bot sentences into %r [%s]", len(good), len(english), language, source)


def t(language, key, **values):
    """Get a sentence in the mod's language, with {placeholders} filled in. Falls back to English."""
    text = (TEXTS.get(language) or TRANSLATED.get(language) or {}).get(key) or TEXTS["en"][key]
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return TEXTS["en"][key].format(**values)
