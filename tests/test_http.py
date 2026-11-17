import json
import os
import sys
import threading
import unittest
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from core import Store
from server import Handler


class HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.store = Store()
        cls.http = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = "http://127.0.0.1:" + str(cls.http.server_port)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        Handler.store.db.close()

    def request(self, path, body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.base + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def login(self, subject):
        return self.request("/v1/auth/fixture-login", {"subject": subject})[1]["token"]

    def test_auth_and_draft_visibility(self):
        learner = self.login("fixture:http-learner")
        editor = self.login("fixture:editor")
        self.assertEqual(self.request("/v1/me")[0], 401)
        self.assertEqual(self.request("/v1/course", token=learner)[0], 403)
        self.assertEqual(self.request("/v1/onboarding", {"adult": True, "consentVersion": "sandbox-v1", "track": "development", "goalMinutes": 5}, learner)[0], 200)
        draft = {"scenarioId": "HTTP-C01", "version": "0.1-draft", "track": "common", "publicBrief": "Ask", "privateFacts": [{"value": "hidden"}], "releaseAllowed": False}
        self.assertEqual(self.request("/v1/admin/import/scenario", draft, learner)[0], 403)
        self.assertEqual(self.request("/v1/admin/import/scenario", draft, editor)[0], 200)
        self.assertEqual(self.request("/v1/content/scenario/HTTP-C01/0.1-draft", token=learner)[0], 404)
        self.assertEqual(self.request("/v1/admin/review/scenario/HTTP-C01/0.1-draft", {}, self.login("fixture:reviewer"))[0], 409)


if __name__ == "__main__":
    unittest.main()
