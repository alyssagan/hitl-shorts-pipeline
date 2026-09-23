"""classify_rights_status() and its wiring into vet_all() (#8: identity, relevance, rights and selection
must stay independent axes -- rights_status is evidence-based, never a legal conclusion, and a human's own
sign-off (rights_reviewer set) always outranks a re-run of the heuristic)."""
import unittest

from pipeline.core.models import Asset
from pipeline.vetting.rules import classify_rights_status, vet_all


class ClassifyRightsStatusTests(unittest.TestCase):
    def test_cc0_is_distinct_from_public_domain(self):
        cc0, _ = classify_rights_status("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
        pd, _ = classify_rights_status("Public domain (per Library of Congress rights advisory)", "")
        self.assertEqual(cc0, "cc0")
        self.assertEqual(pd, "public_domain")
        self.assertNotEqual(cc0, pd)

    def test_open_license_vs_restrictive_vs_unknown(self):
        self.assertEqual(classify_rights_status("CC BY 4.0", "")[0], "open_license")
        self.assertEqual(classify_rights_status("Pexels License", "")[0], "open_license")
        self.assertEqual(classify_rights_status("CC BY-NC-SA 4.0", "")[0], "unresolved")
        self.assertEqual(classify_rights_status("", "")[0], "unresolved")
        self.assertEqual(classify_rights_status("Some unrecognized license text", "")[0], "unresolved")

    def test_no_known_restrictions_is_evidence_not_a_conclusion(self):
        # #8: "no known restrictions" maps to public_domain (a claim worth recording) but the evidence
        # string makes clear it's exactly that text, not an independent legal determination.
        status, evidence = classify_rights_status("No known restrictions on publication.", "")
        self.assertEqual(status, "public_domain")
        self.assertIn("No known restrictions", evidence)


class VetAllRightsWiringTests(unittest.TestCase):
    def test_rights_status_set_from_license_but_never_promoted_to_verified_case(self):
        a = Asset(source="x", path="/x", title="t", license="CC0", source_url="https://x/y", width=1000, height=800)
        vet_all([a], ["t"])
        self.assertEqual(a.rights_status, "cc0")
        self.assertIsNone(a.category)          # rights classification never touches category/identity

    def test_human_signoff_is_never_overwritten_by_a_later_vetting_round(self):
        a = Asset(source="x", path="/x", title="t", license="CC0", source_url="https://x/y", width=1000, height=800,
                  rights_reviewer="Aly", rights_status="paid_license", rights_evidence="verified by hand, actually a paid stock license")
        vet_all([a], ["t"])
        self.assertEqual(a.rights_status, "paid_license")
        self.assertIn("verified by hand", a.rights_evidence)

    def test_unresolved_when_no_license_at_all(self):
        a = Asset(source="x", path="/x", title="t", source_url="https://x/y", width=1000, height=800)
        vet_all([a], ["t"])
        self.assertEqual(a.rights_status, "unresolved")


if __name__ == "__main__":
    unittest.main()
