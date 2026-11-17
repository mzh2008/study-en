"""Verify authorized DV-10 document exports without committing server-only content.

Input JSON shape: scenarios, exams, days, rubrics, units arrays.
The supplied CSV is the corresponding subset of the DV-10 object-hashes index.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
from core import DomainError, Store, digest, public_projection
from import_pack import import_pack


FILES = {"scenarios": "scenarios.server.json", "exams": "exams.server.json", "days": "days.json", "rubrics": "rubrics.json", "units": "units.json"}


def check(source_path, hash_path):
    with open(source_path, encoding="utf-8") as stream:
        source = json.load(stream)
    with open(hash_path, encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    index = {(row["objectType"], row["objectId"], row["version"]): row for row in rows}
    if len(index) != len(rows):
        raise ValueError("duplicate object hash key")
    file_bytes = {}
    for plural, filename in FILES.items():
        objects = source[plural]
        kind = plural[:-1] if plural != "rubrics" else "rubric"
        key_field = {"scenario": "scenarioId", "exam": "examFormId", "day": "dayId", "rubric": "rubricId", "unit": "unitId"}[kind]
        for obj in objects:
            version = obj.get("version") or obj.get("contentVersion")
            row = index[(kind, obj[key_field], version)]
            if digest(obj) != row["sha256"] or row["sourceFile"] != filename:
                raise ValueError("DV-10 object hash mismatch: " + kind + ":" + obj[key_field])
        file_bytes[filename] = json.dumps(objects, ensure_ascii=False, indent=2).encode("utf-8")
    if sum(len(source[name]) for name in FILES) != len(index):
        raise ValueError("incomplete hash index")
    with open(hash_path, "rb") as stream:
        file_bytes["object-hashes.csv"] = stream.read()
    manifest = {
        "packageId": "DV-10-V0.3-DOCUMENT-SUBSET-TEST",
        "status": "DRAFT_PENDING_REVIEW",
        "releaseAllowed": False,
        "importGate": "DO_NOT_IMPORT_TO_PRODUCTION_OR_EXPOSE_SERVER_FILES_TO_CLIENT",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in file_bytes.items()},
        "counts": {"scenarioFamilies": len(source["scenarios"]), "examForms": len(source["exams"]), "dayTaskRecords": len(source["days"]), "commonUnits": 10, "itUnits": 10},
        "qaSupplement": {"objectHashRows": len(rows)},
    }
    with tempfile.TemporaryDirectory() as directory:
        archive_path = os.path.join(directory, "subset.zip")
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, data in file_bytes.items():
                archive.writestr(name, data)
        store = Store(os.path.join(directory, "sandbox.sqlite3"))
        first = import_pack(store, archive_path, "fixture:editor")
        second = import_pack(store, archive_path, "fixture:editor")
        if second["idempotent"] != len(rows):
            raise AssertionError("reimport not idempotent")
        scene = source["scenarios"][0]
        changed = {**scene, "publicBrief": "mutated"}
        try:
            store.import_content("fixture:editor", "scenario", changed)
        except DomainError as error:
            if error.code != "immutable_version_conflict":
                raise
        else:
            raise AssertionError("same-version overwrite allowed")
        if "privateFacts" in public_projection("scenario", scene) or "revealOn" in str(public_projection("scenario", scene)):
            raise AssertionError("private fact leaked in projection")
        store.login_sandbox("fixture:learner")
        store.onboard("fixture:learner", True, "sandbox-v1", "development", 5)
        for operation in (
            lambda: store.public_content("fixture:learner", "scenario", scene["scenarioId"], scene["version"]),
            lambda: store.start_session("fixture:learner", scene["scenarioId"], scene["version"]),
            lambda: store.review_content("fixture:reviewer", "scenario", scene["scenarioId"], scene["version"]),
        ):
            try:
                operation()
            except DomainError as error:
                if error.code not in ("content_not_available", "draft_not_release_allowed"):
                    raise
            else:
                raise AssertionError("draft gate bypassed")
        stored = store.db.execute("SELECT COUNT(*) FROM content").fetchone()[0]
        sessions = store.db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        store.db.close()
    return {"objectHashChecks": len(rows), "imported": stored, "sessions": sessions, "counts": first["counts"], "idempotentReimport": second["idempotent"], "overwriteRejected": True, "privateProjection": "PASS", "draftGate": "PASS", "note": "document subset, not original ZIP"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source_json")
    parser.add_argument("hashes_csv")
    args = parser.parse_args()
    print(json.dumps(check(args.source_json, args.hashes_csv), sort_keys=True))
