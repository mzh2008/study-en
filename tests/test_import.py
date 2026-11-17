import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from core import DomainError, Store, digest
from import_pack import import_pack


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "db.sqlite3"))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def archive(self, changed=False, corrupt=False):
        scenario = {"scenarioId": "C01", "version": "0.1-draft", "track": "common", "publicBrief": "Ask", "privateFacts": [{"value": "hidden"}], "releaseAllowed": False}
        if changed:
            scenario["publicBrief"] = "Changed"
        source = json.dumps([scenario]).encode()
        rows = io.StringIO()
        writer = csv.DictWriter(rows, fieldnames=["objectType", "objectId", "version", "sha256", "sourceFile", "canonicalization"])
        writer.writeheader()
        writer.writerow({"objectType": "scenario", "objectId": "C01", "version": "0.1-draft", "sha256": digest(scenario), "sourceFile": "scenarios.server.json", "canonicalization": "sorted-keys-json-utf8-v1"})
        hashes = rows.getvalue().encode()
        if corrupt:
            hashes += b"bad"
        manifest = {"packageId": "TEST-DRAFT", "status": "DRAFT_PENDING_REVIEW", "releaseAllowed": False, "importGate": "DO_NOT_IMPORT_TO_PRODUCTION_OR_EXPOSE_SERVER_FILES_TO_CLIENT", "files": {"scenarios.server.json": hashlib.sha256(source).hexdigest(), "object-hashes.csv": hashlib.sha256(hashes).hexdigest()}, "counts": {"scenarioFamilies": 1}, "qaSupplement": {"objectHashRows": 1}}
        path = os.path.join(self.tmp.name, "pack.zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr("scenarios.server.json", source)
            archive.writestr("object-hashes.csv", hashes)
        return path

    def test_draft_import_is_atomic_and_idempotent(self):
        path = self.archive()
        first = import_pack(self.store, path, "fixture:editor")
        self.assertEqual(first["counts"]["scenario"], 1)
        self.assertEqual(first["status"], "DRAFT")
        second = import_pack(self.store, path, "fixture:editor")
        self.assertEqual(second["idempotent"], 1)
        with self.assertRaises(DomainError) as raised:
            import_pack(self.store, self.archive(changed=True), "fixture:editor")
        self.assertEqual(raised.exception.code, "immutable_version_conflict")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0], 1)

    def test_manifest_hash_failure_rolls_back(self):
        with self.assertRaises(DomainError):
            import_pack(self.store, self.archive(corrupt=True), "fixture:editor")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
