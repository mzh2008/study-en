import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from core import DomainError, Store


class FoundationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.learner = "fixture:learner-1"
        self.store.login_sandbox(self.learner)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def assert_error(self, code, callback):
        with self.assertRaises(DomainError) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def publish(self, kind, obj):
        key = {"scenario": "scenarioId", "day": "dayId"}[kind]
        self.store.import_content("fixture:editor", kind, obj)
        self.store.review_content("fixture:reviewer", kind, obj[key], obj.get("version") or obj["contentVersion"])

    def onboard(self):
        return self.store.onboard(self.learner, True, "sandbox-v1", "development", 5)

    def test_identity_consent_and_token_isolation(self):
        self.assert_error("fixture_identity_required", lambda: self.store.login_sandbox("real-openid"))
        self.assert_error("adult_required", lambda: self.store.onboard(self.learner, False, "sandbox-v1", "development", 5))
        self.assert_error("training_not_allowed", lambda: self.store.course(self.learner))
        token = self.store.login_sandbox(self.learner)["token"]
        self.assertEqual(self.store.user_for_token(token)["id"], self.learner)
        self.assert_error("unauthorized", lambda: self.store.user_for_token("bad-token"))
        self.onboard()
        self.assertEqual(self.store.course(self.learner)["nextDay"], 1)
        self.store.withdraw(self.learner)
        self.assert_error("training_not_allowed", lambda: self.store.course(self.learner))

    def test_immutable_private_content_and_review_gate(self):
        self.onboard()
        scene = {"scenarioId": "C01", "version": "0.1.0-draft", "track": "common", "title": "Brief", "publicBrief": "Ask for scope", "privateFacts": [{"factId": "F1", "answer": "secret"}], "variants": [{"variantId": "E", "level": "easy", "hiddenAnswer": "secret"}], "releaseAllowed": False}
        first = self.store.import_content("fixture:editor", "scenario", scene)
        self.assertTrue(self.store.import_content("fixture:editor", "scenario", scene)["idempotent"])
        self.assert_error("immutable_version_conflict", lambda: self.store.import_content("fixture:editor", "scenario", {**scene, "publicBrief": "changed"}))
        self.assert_error("draft_not_release_allowed", lambda: self.store.review_content("fixture:reviewer", "scenario", "C01", "0.1.0-draft"))
        self.assert_error("content_not_available", lambda: self.store.public_content(self.learner, "scenario", "C01", "0.1.0-draft"))
        approved = {**scene, "version": "1.0.0-sandbox", "releaseAllowed": True}
        self.store.import_content("fixture:editor", "scenario", approved)
        self.assert_error("self_review_forbidden", lambda: self.store.review_content("fixture:editor", "scenario", "C01", "1.0.0-sandbox"))
        self.store.review_content("fixture:reviewer", "scenario", "C01", "1.0.0-sandbox")
        public = self.store.public_content(self.learner, "scenario", "C01", "1.0.0-sandbox")
        self.assertNotIn("privateFacts", public)
        self.assertNotIn("releaseAllowed", public)
        self.assertNotIn("hiddenAnswer", public["variants"][0])
        self.assertEqual(public["publicBrief"], "Ask for scope")
        self.assertEqual(len(first["sha256"]), 64)

    def test_course_requires_evidence_and_is_idempotent(self):
        self.onboard()
        day = {"dayId": "DEV-DAY-01", "contentVersion": "1.0-sandbox", "track": "development", "courseDay": 1, "microSpeakingTask": "Ask scope", "scenarioRef": {"scenarioId": "C01", "version": "1.0-sandbox"}, "releaseAllowed": True}
        self.publish("day", day)
        self.assertEqual(self.store.course(self.learner)["day"]["dayId"], "DEV-DAY-01")
        self.assert_error("effective_evidence_required", lambda: self.store.complete_day(self.learner, "button-click"))
        self.assertEqual(self.store.complete_day(self.learner, "fixture-evidence:turn-1")["completedDay"], 1)
        self.assertTrue(self.store.complete_day(self.learner, "fixture-evidence:turn-1")["idempotent"])
        self.assertEqual(self.store.course(self.learner)["nextDay"], 2)
        self.assert_error("published_day_missing_or_ambiguous", lambda: self.store.complete_day(self.learner, "fixture-evidence:turn-2"))


if __name__ == "__main__":
    unittest.main()
