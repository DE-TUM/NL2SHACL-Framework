"""
Tests of the logical evaluator (LER).

Run from the Shapes-Evaluation folder:
    python -m unittest discover -s tests -v

Tests that call SHACL2FOL and Vampire are skipped when the tools are not
installed (see tools/setup_logical_tools.sh); all other tests need no tools.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rdflib import Graph, URIRef, RDF
from rdflib.namespace import SH

import logical_equivalence as L
from logical_metrics import logical_metrics

PREFIXES = (
    "@prefix : <http://ex/> . "
    "@prefix sh: <http://www.w3.org/ns/shacl#> . "
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
)
EX = "http://ex/"


def node_shape(body: str, name: str = ":S", target: str = "sh:targetClass :C") -> str:
    return PREFIXES + f"{name} a sh:NodeShape ; {target} ; {body} ."


def prop(constraints: str, path: str = ":p") -> str:
    return f"sh:property [ sh:path {path} ; {constraints} ]"


TOOLS, TOOLS_PROBLEM = L.resolve_tools()
needs_tools = unittest.skipIf(TOOLS is None, f"SHACL2FOL/Vampire not available: {TOOLS_PROBLEM}")


# ---------------------------------------------------------------------------
# No tools required
# ---------------------------------------------------------------------------

class TestRenameShapes(unittest.TestCase):

    def rename(self, ttl):
        return L.rename_shapes(Graph().parse(data=ttl, format="turtle"))

    def test_shape_subjects_are_renamed(self):
        g = self.rename(node_shape(prop("sh:minCount 1")))
        self.assertIn(URIRef(EX + "S" + L.SHAPE_SUFFIX), set(g.subjects(RDF.type, SH.NodeShape)))
        self.assertNotIn(URIRef(EX + "S"), set(g.subjects()))

    def test_class_with_the_same_iri_as_the_shape_is_not_renamed(self):
        # The shape is named like the class it targets, as in many real shapes graphs.
        g = self.rename(node_shape(prop("sh:minCount 1"), name=":C"))
        self.assertIn(URIRef(EX + "C"), set(g.objects(None, SH.targetClass)))

    def test_references_to_shapes_are_renamed(self):
        ttl = (PREFIXES + ":S a sh:NodeShape ; sh:targetClass :C ; sh:node :A ; sh:and ( :A :B ) .\n"
               ":A a sh:NodeShape . :B a sh:NodeShape .")
        g = self.rename(ttl)
        renamed = {URIRef(EX + n + L.SHAPE_SUFFIX) for n in "AB"}
        referenced = set(g.objects(None, SH.node)) | set(
            member for head in g.objects(None, SH["and"]) for member in __import__("rdflib").collection.Collection(g, head))
        self.assertTrue(renamed & referenced)
        self.assertFalse({URIRef(EX + "A"), URIRef(EX + "B")} & referenced)

    def test_properties_and_other_iris_are_untouched(self):
        g = self.rename(node_shape(prop("sh:class :D")))
        self.assertIn(URIRef(EX + "p"), set(g.objects(None, SH.path)))
        self.assertIn(URIRef(EX + "D"), set(g.objects(None, SH["class"])))


class TestUnsupportedConstraints(unittest.TestCase):

    def found(self, ttl):
        return L.unsupported_constraints(Graph().parse(data=ttl, format="turtle"))

    def test_finds_ignored_components_with_their_path(self):
        found = self.found(node_shape(prop('sh:datatype xsd:string ; sh:pattern "^a"')))
        self.assertEqual({(c, v) for _, c, v in found},
                         {("datatype", "http://www.w3.org/2001/XMLSchema#string"), ("pattern", '"^a"')})
        self.assertTrue(all(path == EX + "p" for path, _, _ in found))

    def test_supported_components_are_not_reported(self):
        self.assertEqual(self.found(node_shape(prop("sh:minCount 1 ; sh:class :D"))), set())

    def test_same_constraints_give_equal_signatures(self):
        a = self.found(node_shape(prop('sh:languageIn ( "en" "de" )')))
        b = self.found(node_shape(prop('sh:languageIn ( "en" "de" )'), name=":T"))
        self.assertEqual(a, b)


class TestParseOutput(unittest.TestCase):

    def test_contained(self):
        self.assertEqual(L.parse_shacl2fol_output("Is the first shape graph contained in the second? true\n", ""), (True, None))

    def test_not_contained(self):
        self.assertEqual(L.parse_shacl2fol_output("Is the first shape graph contained in the second? false\n", ""), (False, None))

    def test_unsupported_construct(self):
        stderr = "Exception in thread \"main\" java.lang.reflect.InvocationTargetException\n" \
                 "Caused by: converter.WrongNumberOfPathAttributesException\n"
        self.assertEqual(L.parse_shacl2fol_output("", stderr), (None, "unsupported:WrongNumberOfPathAttributesException"))

    def test_prover_time_limit(self):
        stderr = "java.lang.RuntimeException: Failed to parse the output of the theorem prover.\n"
        self.assertEqual(L.parse_shacl2fol_output("", stderr, "% Time limit reached!\n"), (None, "timeout"))

    def test_prover_gave_up(self):
        stderr = "java.lang.RuntimeException: Failed to parse the output of the theorem prover.\n"
        self.assertEqual(L.parse_shacl2fol_output("", stderr, "% Refutation not found, incomplete strategy\n"), (None, "prover_gave_up"))

    def test_unknown_failure(self):
        self.assertEqual(L.parse_shacl2fol_output("", "")[0], None)


class TestClassify(unittest.TestCase):

    def test_all_cases(self):
        self.assertEqual(L.classify(True, True), (True, "equivalent"))
        self.assertEqual(L.classify(False, True), (False, "output_stricter"))
        self.assertEqual(L.classify(True, False), (False, "output_more_permissive"))
        self.assertEqual(L.classify(False, False), (False, "incomparable"))
        self.assertEqual(L.classify(False, None), (False, None))
        self.assertEqual(L.classify(None, False), (False, None))
        self.assertEqual(L.classify(True, None), (None, None))
        self.assertEqual(L.classify(None, None), (None, None))


class TestUnavailableTools(unittest.TestCase):

    def test_missing_jar_is_reported_not_raised(self):
        result = L.evaluate_logical_equivalence("", "", jar="/nonexistent/SHACL2FOL.jar")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["equivalent"])
        self.assertIn("SHACL2FOL jar not found", result["reason"])


class TestLogicalMetrics(unittest.TestCase):

    @staticmethod
    def record(status, equivalent=None, direction=None):
        return {"logical": {"status": status, "equivalent": equivalent, "direction": direction}}

    def test_rates_and_counts(self):
        records = [
            self.record("success", True, "equivalent"),
            self.record("success", True, "equivalent"),
            self.record("success", False, "output_stricter"),
            self.record("success", False, "output_more_permissive"),
            self.record("undetermined"),
            self.record("unavailable"),          # not attempted: excluded everywhere
            {"logical": None},                   # validity failed: not attempted
        ]
        m = logical_metrics(records)
        self.assertEqual(m["N_logical"], 5)
        self.assertEqual(m["N_logical_equivalent"], 2)
        self.assertEqual(m["N_logical_non_equivalent"], 2)
        self.assertEqual(m["N_logical_undetermined"], 1)
        self.assertAlmostEqual(m["LER"], 2 / 4)             # among determined records
        self.assertAlmostEqual(m["LER_coverage"], 4 / 5)    # determined among attempted
        self.assertEqual((m["N_output_stricter"], m["N_output_more_permissive"], m["N_incomparable"]), (1, 1, 0))

    def test_not_run_gives_none(self):
        m = logical_metrics([{"logical": None}, {}])
        self.assertTrue(all(value is None for value in m.values()))

    def test_nothing_determined(self):
        m = logical_metrics([self.record("undetermined")])
        self.assertIsNone(m["LER"])
        self.assertEqual(m["LER_coverage"], 0.0)


# ---------------------------------------------------------------------------
# Tools required: every expectation below was derived by hand and confirmed
# against SHACL2FOL 4.9 / Vampire 4.9.
# ---------------------------------------------------------------------------

@needs_tools
class TestEvaluateWithTools(unittest.TestCase):

    def check(self, ref, out, equivalent, direction=None, **kwargs):
        result = L.evaluate_logical_equivalence(ref, out, timeout=60, **kwargs)
        self.assertEqual(result["equivalent"], equivalent, result)
        if direction is not None:
            self.assertEqual(result["direction"], direction, result)
        return result

    def test_identical_shapes_are_equivalent(self):
        shape = node_shape(prop("sh:minCount 1"))
        result = self.check(shape, shape, True, "equivalent")
        self.assertEqual(result["status"], "success")

    def test_same_shape_name_does_not_hide_a_difference(self):
        # Regression test: without renaming, both graphs would be merged and
        # the result would wrongly be "equivalent".
        self.check(node_shape(prop("sh:minCount 1")), node_shape(prop("sh:minCount 2")),
                   False, "output_stricter")

    def test_output_more_permissive(self):
        self.check(node_shape(prop("sh:maxCount 1")), node_shape(prop("sh:maxCount 2")),
                   False, "output_more_permissive")

    def test_different_target_class_is_not_equivalent(self):
        self.check(node_shape(prop("sh:minCount 1")),
                   node_shape(prop("sh:minCount 1"), target="sh:targetClass :D"),
                   False, "incomparable")

    def test_different_structure_but_equivalent(self):
        flat = node_shape(prop("sh:minCount 1 ; sh:maxCount 1"))
        split = PREFIXES + (":S a sh:NodeShape ; sh:targetClass :C ; sh:and ( :A :B ) .\n"
                            ":A a sh:NodeShape ; " + prop("sh:minCount 1") + " .\n"
                            ":B a sh:NodeShape ; " + prop("sh:maxCount 1") + " .")
        self.check(flat, split, True, "equivalent")

    def test_annotations_do_not_matter(self):
        plain = node_shape(prop("sh:minCount 1"))
        annotated = node_shape('sh:name "x" ; sh:description "y" ; ' + prop('sh:minCount 1 ; sh:name "p"'))
        self.check(plain, annotated, True, "equivalent")

    def test_unsupported_construct_is_undetermined(self):
        result = L.evaluate_logical_equivalence(
            node_shape(prop("sh:minCount 1")),
            node_shape(prop("sh:minCount 1", path=":p, :q")), timeout=60)
        self.assertEqual(result["status"], "undetermined")
        self.assertIsNone(result["equivalent"])
        self.assertTrue(result["reason"].startswith("unsupported:"), result)

    def test_ignored_constraints_that_differ_are_not_reported_equivalent(self):
        # SHACL2FOL ignores sh:datatype, so it alone would call these equivalent.
        string = node_shape(prop("sh:minCount 1 ; sh:datatype xsd:string"))
        integer = node_shape(prop("sh:minCount 1 ; sh:datatype xsd:integer"))
        result = L.evaluate_logical_equivalence(string, integer, timeout=60)
        self.assertEqual(result["status"], "undetermined")
        self.assertEqual(result["reason"], "unsupported_components_differ:datatype")
        self.assertEqual(result["unsupported_components"], ["datatype"])

    def test_component_check_can_be_switched_off(self):
        string = node_shape(prop("sh:minCount 1 ; sh:datatype xsd:string"))
        integer = node_shape(prop("sh:minCount 1 ; sh:datatype xsd:integer"))
        self.check(string, integer, True, check_unsupported_components=False)

    def test_identical_ignored_constraints_stay_equivalent(self):
        shape = node_shape(prop("sh:minCount 1 ; sh:datatype xsd:string"))
        self.check(shape, shape, True, "equivalent")

    def test_real_difference_is_found_even_when_ignored_constraints_differ(self):
        self.check(node_shape(prop("sh:minCount 1 ; sh:datatype xsd:string")),
                   node_shape(prop("sh:minCount 2 ; sh:datatype xsd:integer")), False)


if __name__ == "__main__":
    unittest.main()
