import unittest

from scriptorium.components import (
    ComponentCatalogError,
    build_component_report,
    format_component_report,
    load_component_catalog,
)


class ComponentCatalogTests(unittest.TestCase):
    def test_catalog_keeps_capture_owned_by_provenance(self):
        catalog = load_component_catalog()
        capture = catalog.components["capture"]
        self.assertEqual(capture.owner, "provenance")
        self.assertEqual(capture.delivery, "release-asset")
        self.assertEqual(capture.artifact_status, "published")
        self.assertTrue(capture.artifact_url.endswith(capture.artifact_name))
        self.assertNotIn("capture", catalog.profiles["core"])

    def test_core_profile_selects_only_contract_and_memory_core(self):
        report = build_component_report("core")
        selected = {
            row["id"] for row in report["components"] if row["selected"]
        }
        self.assertEqual(selected, {"scriptorium-spec", "provenance"})

    def test_capture_profile_does_not_select_the_provenance_source_package(self):
        report = build_component_report("capture")
        selected = [row["id"] for row in report["components"] if row["selected"]]
        self.assertEqual(selected, ["capture"])

    def test_component_report_is_readable(self):
        output = format_component_report(build_component_report("research"))
        self.assertIn("Selected profile: research", output)
        self.assertIn("* steward 0.2.0", output)

    def test_unknown_profile_fails_closed(self):
        with self.assertRaisesRegex(ComponentCatalogError, "unknown"):
            build_component_report("unknown")


if __name__ == "__main__":
    unittest.main()
