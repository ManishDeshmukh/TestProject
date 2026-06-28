"""Chatbot Agent: deterministic intent classification + action safety."""

from jobpilot.constants import INTENTS, CONFIRM_INTENTS


def test_all_22_intents_defined():
    # 22 named intents incl. license_status + license_wait (excluding 'unknown').
    named = [k for k in INTENTS if k != "unknown"]
    assert len(named) == 21  # 'unknown' is the 22nd
    assert "license_status" in INTENTS and "license_wait" in INTENTS


def test_intent_classification_regex_only(coordinator):
    bot = coordinator.chatbot
    assert bot.classify("which jobs running") == "running_jobs"
    assert bot.classify("why did job 12345 fail") == "why_failed"
    assert bot.classify("license status") == "license_status"
    assert bot.classify("best time to submit") == "license_wait"
    assert bot.classify("totally unrelated nonsense") == "unknown"


def test_entity_extraction(coordinator):
    ent = coordinator.chatbot.extract_entities("tell me about job 98213 in queue verify")
    assert ent["job_id"] == "98213"
    assert ent["queue"] == "verify"


def test_action_requires_confirmation(coordinator):
    bot = coordinator.chatbot
    r1 = bot.respond("cancel job 1234", session="t")
    assert "confirm" in r1["text"].lower()
    assert "cancel_job" in CONFIRM_INTENTS
    # A non-yes answer aborts the action.
    r2 = bot.respond("no", session="t")
    assert "cancelled" in r2["text"].lower() or "no action" in r2["text"].lower()


def test_chatbot_cannot_submit_jobs(coordinator):
    # There is no submit intent; an attempt routes to unknown/help, never submission.
    assert "submit_job" not in INTENTS
