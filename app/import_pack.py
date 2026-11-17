"""Atomic, server-only import of a checked DRAFT content ZIP into sandbox SQLite."""

import argparse
import csv
import hashlib
import io
import json
import os
import zipfile
from collections import Counter

from core import DomainError, Store, digest


OBJECT_FILES = {
    "scenarios.server.json": ("scenario", "scenarioId"),
    "exams.server.json": ("exam", "examFormId"),
    "days.json": ("day", "dayId"),
    "units.json": ("unit", "unitId"),
    "sentences.json": ("sentence", "phraseId"),
    "terms.json": ("term", "termId"),
    "rubrics.json": ("rubric", "rubricId"),
}
COUNT_FIELDS = {"scenario": "scenarioFamilies", "exam": "examForms", "day": "dayTaskRecords", "unit": "commonUnits", "sentence": "sentences", "term": "terms"}


def import_pack(store, zip_path, actor, expected_sha256=None):
    with open(zip_path, "rb") as source:
        package_hash = hashlib.file_digest(source, "sha256").hexdigest()
    if expected_sha256 and package_hash != expected_sha256:
        raise DomainError("package_hash_mismatch", 409)
    with zipfile.ZipFile(zip_path) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        names = [item.filename for item in entries]
        if len(names) != len(set(names)) or any("/" in name or "\\" in name or name.startswith(".") for name in names):
            raise DomainError("unsafe_archive")
        if any(item.file_size > 5_000_000 for item in entries) or sum(item.file_size for item in entries) > 20_000_000:
            raise DomainError("archive_too_large", 413)
        if "manifest.json" not in names or "object-hashes.csv" not in names:
            raise DomainError("manifest_missing")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("releaseAllowed") is not False or manifest.get("status") != "DRAFT_PENDING_REVIEW" or manifest.get("importGate") != "DO_NOT_IMPORT_TO_PRODUCTION_OR_EXPOSE_SERVER_FILES_TO_CLIENT":
            raise DomainError("draft_gate_required", 409)
        expected_files = manifest.get("files", {})
        if set(names) != set(expected_files) | {"manifest.json"}:
            raise DomainError("manifest_file_set_mismatch", 409)
        for name, expected in expected_files.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise DomainError("manifest_file_hash_mismatch", 409)
        rows = list(csv.DictReader(io.StringIO(archive.read("object-hashes.csv").decode("utf-8-sig"))))
        expected_rows = {}
        for row in rows:
            key = (row["objectType"], row["objectId"], row["version"])
            if key in expected_rows or row["canonicalization"] != "sorted-keys-json-utf8-v1":
                raise DomainError("object_hash_index_invalid", 409)
            expected_rows[key] = row
        if manifest.get("qaSupplement", {}).get("objectHashRows") != len(rows):
            raise DomainError("object_hash_count_mismatch", 409)
        objects = []
        for filename, (kind, id_key) in OBJECT_FILES.items():
            if filename not in names:
                continue
            records = json.loads(archive.read(filename))
            if not isinstance(records, list):
                raise DomainError("object_file_invalid")
            for obj in records:
                if not isinstance(obj, dict) or obj.get("releaseAllowed", False) is not False or obj.get("status", obj.get("contentStatus", "DRAFT")) != "DRAFT":
                    raise DomainError("draft_gate_required", 409)
                version = obj.get("version") or obj.get("contentVersion")
                key = (kind, obj.get(id_key), version)
                row = expected_rows.get(key)
                if not row or row["sourceFile"] != filename or digest(obj) != row["sha256"]:
                    raise DomainError("object_hash_mismatch", 409)
                objects.append((kind, obj))
        if len(objects) != len(rows):
            raise DomainError("object_hash_index_incomplete", 409)
        counts = Counter(kind for kind, _ in objects)
        declared = manifest.get("counts", {})
        for kind, field in COUNT_FIELDS.items():
            if field in declared and kind != "unit" and counts[kind] != declared[field]:
                raise DomainError("manifest_count_mismatch", 409)
        if "commonUnits" in declared and "itUnits" in declared and counts["unit"] != declared["commonUnits"] + declared["itUnits"]:
            raise DomainError("manifest_count_mismatch", 409)
    store.db.execute("BEGIN IMMEDIATE")
    try:
        idempotent = 0
        for kind, obj in objects:
            idempotent += int(store.import_content(actor, kind, obj)["idempotent"])
        store.audit(actor, "IMPORT_PACKAGE", f"{manifest['packageId']}:{package_hash}")
        store.db.execute("COMMIT")
    except Exception:
        store.db.execute("ROLLBACK")
        raise
    return {"packageId": manifest["packageId"], "sha256": package_hash, "status": "DRAFT", "counts": dict(counts), "idempotent": idempotent}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path")
    parser.add_argument("--db", required=True)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    if os.environ.get("DV_SANDBOX") != "1":
        raise SystemExit("DV_SANDBOX=1 required")
    result = import_pack(Store(args.db), args.zip_path, "fixture:editor", args.expected_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
