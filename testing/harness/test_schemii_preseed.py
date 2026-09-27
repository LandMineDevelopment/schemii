import hashlib
import json
import re
import unittest

from testing.harness.schemii_preseed import canonical, design_content


class SchemiiPreseedTests(unittest.TestCase):
    def test_exact_two_table_oracle_and_references(self):
        content = design_content("sep26d")
        projects, tasks = content["tables"]
        self.assertEqual([table["name"] for table in content["tables"]], ["qa_projects", "qa_tasks"])
        self.assertEqual(
            [(column["name"], column["dataType"]) for column in projects["columns"]],
            [("project_id", "bigint"), ("code", "varchar(24)"), ("title", "text"),
             ("budget", "numeric(12,2)"), ("due_on", "date")],
        )
        self.assertEqual(
            [(column["name"], column["dataType"]) for column in tasks["columns"]],
            [("task_id", "bigint"), ("project_id", "bigint"), ("state", "varchar(16)"),
             ("effort_hours", "numeric(6,2)"), ("notes", "text")],
        )
        self.assertEqual(projects["columns"][0]["identity"], "always")
        self.assertEqual(tasks["columns"][0]["identity"], "always")
        self.assertEqual(projects["columns"][3]["defaultExpression"], "0")
        self.assertEqual(tasks["columns"][2]["defaultExpression"], "'todo'")
        self.assertEqual(projects["checks"][0]["expression"], "budget >= 0")
        self.assertEqual(tasks["checks"][0]["expression"], "state IN ('todo','doing','done')")
        relationship = content["relationships"][0]
        self.assertEqual(relationship["sourceColumnIds"], [tasks["columns"][1]["id"]])
        self.assertEqual(relationship["targetColumnIds"], [projects["columns"][0]["id"]])
        self.assertEqual(tasks["indexes"][0]["columnIds"],
                         [tasks["columns"][1]["id"], tasks["columns"][2]["id"]])
        ids = []
        for table in content["tables"]:
            ids.append(table["id"])
            for collection in ("columns", "keys", "checks", "indexes"):
                ids.extend(item["id"] for item in table.get(collection, []))
        ids.extend(item["id"] for item in content["relationships"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(re.fullmatch(r"[a-z]+_[0-9a-f]{32}", item) for item in ids))

    def test_content_is_tag_deterministic(self):
        first = design_content("sep26d")
        self.assertEqual(first, design_content("sep26d"))
        self.assertNotEqual(first, design_content("sep26e"))
        self.assertEqual(hashlib.sha256(canonical(first)).hexdigest(),
                         hashlib.sha256(json.dumps(first, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
