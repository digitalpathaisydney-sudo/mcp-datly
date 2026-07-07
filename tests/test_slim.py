"""Unit tests for datly_mcp.tools._slim (the read-tool compactors).

Self-contained: loads _slim by file path so it runs under a plain `python3`
(no httpx/mcp needed — importing the package would pull the server).

    python3 -m unittest discover -s main/mcp/tests
    # or:  python3 main/mcp/tests/test_slim.py
"""
import importlib.util
import json
import unittest
from pathlib import Path

_SLIM_PATH = Path(__file__).resolve().parents[1] / "datly_mcp" / "tools" / "_slim.py"
_spec = importlib.util.spec_from_file_location("_slim", _SLIM_PATH)
slim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(slim)


def drf_field(**over):
    """A DBField dict shaped exactly like DBFieldSerializer emits (17 keys)."""
    base = {
        "id": "f-uuid", "name": "col", "type_id": "varchar", "type_name": "varchar",
        "primary_key": False, "unique": False, "nullable": True, "increment": False,
        "is_array": False, "character_maximum_length": None, "precision": None,
        "scale": None, "default": None, "collation": None, "comments": "",
        "order": 0, "created_at": "2026-06-01T00:00:00Z",
    }
    base.update(over)
    return base


class SlimFieldTests(unittest.TestCase):
    def test_plain_nullable_field_keeps_only_core(self):
        # A nullable column with all defaults → id + name + type, nothing else.
        out = slim.slim_field(drf_field(name="bio", type_name="text"))
        self.assertEqual(out, {"id": "f-uuid", "name": "bio", "type": "text"})

    def test_type_id_is_dropped_type_name_wins(self):
        out = slim.slim_field(drf_field(type_id="int4", type_name="integer"))
        self.assertNotIn("type_id", out)
        self.assertEqual(out["type"], "integer")

    def test_nullable_inversion_only_marks_not_null(self):
        self.assertNotIn("not_null", slim.slim_field(drf_field(nullable=True)))
        self.assertTrue(slim.slim_field(drf_field(nullable=False))["not_null"])

    def test_non_default_flags_surface(self):
        out = slim.slim_field(drf_field(
            name="id", type_name="integer", primary_key=True,
            increment=True, nullable=False))
        self.assertEqual(out, {
            "id": "f-uuid", "name": "id", "type": "integer",
            "pk": True, "not_null": True, "increment": True,
        })

    def test_unique_array_len_precision_scale_default_note(self):
        out = slim.slim_field(drf_field(
            unique=True, is_array=True, character_maximum_length=255,
            precision=10, scale=2, default="now()", collation="C",
            comments="hi"))
        self.assertTrue(out["unique"])
        self.assertTrue(out["array"])
        self.assertEqual(out["len"], 255)
        self.assertEqual(out["precision"], 10)
        self.assertEqual(out["scale"], 2)
        self.assertEqual(out["default"], "now()")
        self.assertEqual(out["collation"], "C")
        self.assertEqual(out["note"], "hi")

    def test_zero_default_is_kept_empty_string_is_not(self):
        # default=0 is meaningful; default="" is not.
        self.assertEqual(slim.slim_field(drf_field(default=0))["default"], 0)
        self.assertNotIn("default", slim.slim_field(drf_field(default="")))

    def test_slim_is_smaller(self):
        f = drf_field(unique=True, nullable=False, character_maximum_length=255)
        self.assertLess(
            len(json.dumps(slim.slim_field(f))), len(json.dumps(f)) // 2)


class SlimTableTests(unittest.TestCase):
    def make_table(self, **over):
        base = {
            "id": "t1", "name": "users", "schema": "public", "x": 10, "y": 20,
            "width": 200, "color": "#fff", "comments": "", "is_view": False,
            "is_materialized_view": False, "order": 1, "expanded": True,
            "created_at": "z",
            "fields": [drf_field(name="id", primary_key=True, nullable=False)],
            "indexes": [{"id": "i1", "name": "ix", "unique": True,
                         "field_ids": ["f-uuid"], "created_at": "z"}],
        }
        base.update(over)
        return base

    def test_drops_geometry_and_timestamps(self):
        out = slim.slim_table(self.make_table())
        for k in ("x", "y", "width", "color", "order", "expanded", "created_at"):
            self.assertNotIn(k, out)
        self.assertEqual(out["id"], "t1")
        self.assertEqual(out["name"], "users")

    def test_public_schema_omitted_custom_kept(self):
        self.assertNotIn("schema", slim.slim_table(self.make_table()))
        self.assertEqual(
            slim.slim_table(self.make_table(schema="auth"))["schema"], "auth")

    def test_indexes_compacted(self):
        idx = slim.slim_table(self.make_table())["indexes"][0]
        self.assertEqual(idx, {"id": "i1", "columns": ["f-uuid"],
                               "name": "ix", "unique": True})

    def test_view_flags(self):
        out = slim.slim_table(self.make_table(is_view=True,
                                              is_materialized_view=True))
        self.assertTrue(out["view"])
        self.assertTrue(out["materialized"])


class SlimRelationshipTests(unittest.TestCase):
    def test_card_and_endpoints(self):
        out = slim.slim_relationship({
            "id": "r1", "name": "", "source_table": "t2", "target_table": "t1",
            "source_field_id": "f2", "target_field_id": "f1",
            "source_cardinality": "many", "target_cardinality": "one",
            "source_schema": "public", "target_schema": "public",
            "created_at": "z"})
        self.assertEqual(out, {
            "id": "r1", "from": ["t2", "f2"], "to": ["t1", "f1"],
            "card": "many-one"})


class OutlineTests(unittest.TestCase):
    def make_diagram(self):
        users = {"id": "t1", "name": "users", "fields": [
            drf_field(name="id", primary_key=True)]}
        posts = {"id": "t2", "name": "posts", "fields": [
            drf_field(name="id", primary_key=True),
            drf_field(name="author_id")]}
        return {
            "id": "d1", "name": "Blog", "database_type": "postgresql",
            "tables": [users, posts],
            "relationships": [{
                "id": "r1", "source_table": "t2", "target_table": "t1",
                "source_field_id": "fa", "target_field_id": "fi",
                "source_cardinality": "many", "target_cardinality": "one"}],
            "areas": [{"id": "a1", "name": "Core", "x": 0, "y": 0, "width": 9,
                       "height": 9, "color": "#abc", "order": 0,
                       "hidden": False, "created_at": "z"}],
            "notes": [],
        }

    def test_outline_shape(self):
        out = slim.outline_diagram(self.make_diagram())
        self.assertEqual(out["diagram"]["name"], "Blog")
        self.assertEqual(out["counts"],
                         {"tables": 2, "fields": 3, "relationships": 1,
                          "areas": 1, "notes": 0})
        self.assertEqual(out["areas"], [{"id": "a1", "name": "Core"}])

    def test_outline_pk_and_refs(self):
        rows = {t["name"]: t for t in slim.outline_diagram(self.make_diagram())["tables"]}
        self.assertEqual(rows["users"]["pk"], ["id"])
        self.assertNotIn("refs", rows["users"])         # users references nobody
        self.assertEqual(rows["posts"]["refs"], ["users"])  # posts → users
        self.assertEqual(rows["posts"]["fields"], 2)

    def test_outline_is_tiny(self):
        d = self.make_diagram()
        self.assertLess(
            len(json.dumps(slim.outline_diagram(d))), len(json.dumps(d)))


class FindTableTests(unittest.TestCase):
    def setUp(self):
        self.d = {"tables": [{"id": "t1", "name": "Users"},
                             {"id": "t2", "name": "Posts"}]}

    def test_by_name_case_insensitive(self):
        self.assertEqual(slim.find_table(self.d, "users")["id"], "t1")
        self.assertEqual(slim.find_table(self.d, "POSTS")["id"], "t2")

    def test_by_id(self):
        self.assertEqual(slim.find_table(self.d, "t2")["name"], "Posts")

    def test_miss_returns_none(self):
        self.assertIsNone(slim.find_table(self.d, "ghost"))
        self.assertIsNone(slim.find_table(self.d, ""))


class SlimAreaViewTests(unittest.TestCase):
    def test_scoped_view_compacted(self):
        view = {
            "focus_area": {"id": "a1", "name": "Auth", "x": 0, "color": "#abc"},
            "in_scope": [{"id": "t1", "name": "users",
                          "fields": [drf_field(name="id", primary_key=True)]}],
            "out_of_scope_shadows": [{"id": "t9", "name": "logs", "fields": []}],
            "relationships": [],
            "counts": {"in_scope": 1, "shadows": 1},
        }
        out = slim.slim_area_view(view)
        self.assertEqual(out["focus_area"], {"id": "a1", "name": "Auth"})
        self.assertEqual(out["in_scope"][0]["name"], "users")
        self.assertEqual(out["shadows"][0]["name"], "logs")
        self.assertEqual(out["counts"], {"in_scope": 1, "shadows": 1})


if __name__ == "__main__":
    unittest.main()
