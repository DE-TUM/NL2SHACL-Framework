"""
logical_equivalence.py
----------------------
Logical equivalence of two SHACL shapes graphs, used for the Logical
Equivalence Rate (LER).

Two shapes graphs S and S' are *equivalent* iff every data graph that is valid
for S is also valid for S' and vice versa. Each direction is a containment check
S <= S'. The check is delegated to SHACL2FOL (https://github.com/paolo7/SHACL2FOL),
which translates the two shapes graphs into first-order logic, and to the
Vampire theorem prover:

    reference <= output   false  -> the output is stricter than the reference
    output <= reference   false  -> the output is more permissive than the reference

Outcome of one record
    equivalent      both directions hold
    non-equivalent  at least one direction is refuted
    undetermined    no refutation, but at least one direction could not be
                    decided (timeout, construct not supported by SHACL2FOL, ...)

Limitation of SHACL2FOL
    SHACL2FOL does not implement the "filter" constraint components sh:datatype,
    sh:minInclusive, sh:maxInclusive, sh:minExclusive, sh:maxExclusive,
    sh:minLength, sh:maxLength, sh:pattern, sh:flags and sh:languageIn: it ignores
    them silently, so two shapes that differ only in these constraints are reported
    as equivalent. To avoid false "equivalent" results, a record is reported as
    undetermined (reason "unsupported_components_differ:...") when the two graphs
    do not carry the same such constraints on the same paths. The check can be
    switched off with check_unsupported_components=False.

Requirements (see Shapes-Evaluation/README.md, "Logical equivalence"):
    - Java 21 or newer
    - SHACL2FOL.jar and a Vampire executable. `tools/setup_logical_tools.sh`
      downloads both into `Shapes-Evaluation/tools/bin/`.

The tools are looked up, in this order, from: the function arguments, the
environment variables SHACL2FOL_JAR / VAMPIRE_PATH / JAVA_BIN, and finally the
default locations (tools/bin/, PATH, JAVA_HOME, CONDA_PREFIX).

Usage as a library:
    from logical_equivalence import evaluate_logical_equivalence
    result = evaluate_logical_equivalence(reference_ttl, output_ttl, timeout=60)
"""

import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

from rdflib import Graph, RDF, URIRef
from rdflib.collection import Collection
from rdflib.namespace import SH, RDFS

DEFAULT_TIMEOUT = 60          # seconds per containment check (one direction)
GRACE_SECONDS = 10            # extra wall-clock time for JVM start and TPTP generation
MIN_JAVA_MAJOR = 21
SHAPE_SUFFIX = "__llm"        # appended to the shape names of the output graph

TOOLS_BIN = Path(__file__).resolve().parent / "tools" / "bin"

# Predicates whose object is a shape (or a list of shapes).
_SHAPE_REF_PREDICATES = [SH.node, SH.property, SH["not"], SH.qualifiedValueShape]
_SHAPE_LIST_PREDICATES = [SH["and"], SH["or"], SH.xone]

# Constraint components that SHACL2FOL ignores (see the module docstring).
_UNSUPPORTED_COMPONENTS = [
    SH.datatype, SH.minInclusive, SH.maxInclusive, SH.minExclusive, SH.maxExclusive,
    SH.minLength, SH.maxLength, SH.pattern, SH.flags, SH.languageIn,
]

# Pure annotations that do not influence validation.
_ANNOTATION_PREDICATES = [
    RDFS.label, RDFS.comment, RDFS.isDefinedBy,
    SH.description, SH.name, SH.order, SH.message,
]


# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------

def _java_major(java: str) -> Optional[int]:
    """Major version of a java executable, or None if it cannot be determined."""
    try:
        out = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r'version "(\d+)(?:\.(\d+))?', out.stderr + out.stdout)
    if not match:
        return None
    major = int(match.group(1))
    return int(match.group(2)) if major == 1 and match.group(2) else major  # "1.8.0" -> 8


def _java_candidates(java: Optional[str]):
    if java:
        yield java
        return
    if os.environ.get("JAVA_BIN"):
        yield os.environ["JAVA_BIN"]
    if shutil.which("java"):
        yield shutil.which("java")
    for var in ("JAVA_HOME", "CONDA_PREFIX"):
        base = os.environ.get(var)
        if base and (Path(base) / "bin" / "java").exists():
            yield str(Path(base) / "bin" / "java")


def _lib_candidates(java: str):
    """Directories that may hold the shared libraries the Vampire binary needs."""
    if os.environ.get("VAMPIRE_LD_LIBRARY_PATH"):
        yield os.environ["VAMPIRE_LD_LIBRARY_PATH"]
    if os.environ.get("CONDA_PREFIX"):
        yield str(Path(os.environ["CONDA_PREFIX"]) / "lib")
    yield str((Path(java).resolve().parent.parent / "lib"))
    yield ""  # system libraries only


def _prover_runs(prover: str, lib: str) -> bool:
    env = dict(os.environ)
    if lib:
        env["LD_LIBRARY_PATH"] = lib + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    try:
        out = subprocess.run([prover, "--version"], capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "Vampire" in out.stdout


@lru_cache(maxsize=None)
def resolve_tools(jar: Optional[str] = None, prover: Optional[str] = None, java: Optional[str] = None):
    """
    Locate and check Java, SHACL2FOL.jar and Vampire.

    Returns (tools, problem). `tools` is a dict with the keys java, jar, prover
    and lib (library path needed by the prover, possibly ""), or None when
    something is missing; `problem` then explains what.
    """
    jar = jar or os.environ.get("SHACL2FOL_JAR") or str(TOOLS_BIN / "SHACL2FOL.jar")
    prover = prover or os.environ.get("VAMPIRE_PATH") or str(TOOLS_BIN / "vampire")
    setup_hint = "Run Shapes-Evaluation/tools/setup_logical_tools.sh or set SHACL2FOL_JAR / VAMPIRE_PATH / JAVA_BIN."

    if not Path(jar).is_file():
        return None, f"SHACL2FOL jar not found: {jar}. {setup_hint}"
    if not Path(prover).is_file() or not os.access(prover, os.X_OK):
        return None, f"Vampire executable not found or not executable: {prover}. {setup_hint}"

    seen = []
    chosen = None
    for candidate in _java_candidates(java):
        major = _java_major(candidate)
        seen.append(f"{candidate} (Java {major if major else '?'})")
        if major and major >= MIN_JAVA_MAJOR:
            chosen = candidate
            break
    if not chosen:
        found = "; ".join(seen) if seen else "no java executable found"
        return None, f"Java {MIN_JAVA_MAJOR} or newer is required by SHACL2FOL ({found}). Set JAVA_BIN."

    for lib in _lib_candidates(chosen):
        if _prover_runs(prover, lib):
            return {"java": chosen, "jar": jar, "prover": prover, "lib": lib}, None
    return None, (f"Vampire does not run: {prover}. It may need a newer libstdc++; "
                  "set VAMPIRE_LD_LIBRARY_PATH to a directory that contains it (for example $CONDA_PREFIX/lib).")


# ---------------------------------------------------------------------------
# Preparing the two shapes graphs
# ---------------------------------------------------------------------------

def _shape_iris(g: Graph) -> set:
    """IRIs that act as shapes in a graph (blank-node shapes are handled by SHACL2FOL itself)."""
    shapes = set()
    for node_type in (SH.NodeShape, SH.PropertyShape):
        shapes.update(s for s in g.subjects(RDF.type, node_type) if isinstance(s, URIRef))
    for s, p, o in g:
        if isinstance(s, URIRef) and str(p).startswith(str(SH)):
            shapes.add(s)  # anything that carries sh: predicates is a shape
        if p in _SHAPE_REF_PREDICATES and isinstance(o, URIRef):
            shapes.add(o)
    for pred in _SHAPE_LIST_PREDICATES:
        for head in g.objects(None, pred):
            try:
                shapes.update(m for m in Collection(g, head) if isinstance(m, URIRef))
            except Exception:
                pass
    return shapes


def rename_shapes(g: Graph, suffix: str = SHAPE_SUFFIX) -> Graph:
    """
    Return a copy of g in which every shape IRI gets `suffix` appended.

    SHACL2FOL encodes both shapes graphs of a containment check into one logical
    theory, so two shapes with the same IRI would be merged and the result would
    be wrong. Only the shapes themselves are renamed: classes, properties and
    other IRIs that happen to look like a shape name (for example the class in
    `sh:targetClass`) are left untouched.
    """
    shapes = _shape_iris(g)

    def rename(term):
        return URIRef(str(term) + suffix) if term in shapes else term

    renamed = Graph()
    for prefix, namespace in g.namespaces():
        renamed.bind(prefix, namespace)

    # RDF list nodes that hold shape references (sh:and / sh:or / sh:xone)
    list_nodes = set()
    for pred in _SHAPE_LIST_PREDICATES:
        for head in g.objects(None, pred):
            node = head
            while node is not None and node != RDF.nil and node not in list_nodes:
                list_nodes.add(node)
                node = g.value(node, RDF.rest)

    for s, p, o in g:
        subject = rename(s)
        if p in _SHAPE_REF_PREDICATES or (p == RDF.first and s in list_nodes):
            o = rename(o)
        renamed.add((subject, p, o))
    return renamed


def _term_repr(g: Graph, term) -> str:
    """Stable text form of a term; RDF lists are expanded, blank nodes are not named."""
    if isinstance(term, URIRef):
        return str(term)
    if term == RDF.nil:
        return "()"
    if (term, RDF.first, None) in g:
        try:
            return "(" + " ".join(_term_repr(g, m) for m in Collection(g, term)) + ")"
        except Exception:
            return "(?)"
    if hasattr(term, "datatype") or hasattr(term, "language"):
        return term.n3()
    return "_:bnode"


def _path_repr(g: Graph, node) -> str:
    """Stable text form of a property path (IRI, inverse, alternative, sequence, ...)."""
    if isinstance(node, URIRef):
        return str(node)
    if (node, RDF.first, None) in g:  # sequence path
        return "/".join(_path_repr(g, m) for m in Collection(g, node))
    for pred in (SH.inversePath, SH.alternativePath, SH.zeroOrMorePath,
                 SH.oneOrMorePath, SH.zeroOrOnePath):
        value = g.value(node, pred)
        if value is not None:
            return f"{pred.split('#')[-1]}({_path_repr(g, value)})"
    return "?"


def unsupported_constraints(g: Graph) -> set:
    """
    The constraints of g that SHACL2FOL ignores, as {(path, component, value)}.
    `path` is the path of the property shape that carries the constraint, or
    "node" for a constraint stated directly on a node shape.
    """
    found = set()
    for component in _UNSUPPORTED_COMPONENTS:
        for subject, _, value in g.triples((None, component, None)):
            path = g.value(subject, SH.path)
            context = _path_repr(g, path) if path is not None else "node"
            found.add((context, component.split("#")[-1], _term_repr(g, value)))
    return found


def strip_annotations(g: Graph) -> Graph:
    """Remove annotation-only triples (labels, descriptions, names, order, messages)."""
    for predicate in _ANNOTATION_PREDICATES:
        for triple in list(g.triples((None, predicate, None))):
            g.remove(triple)
    return g


# ---------------------------------------------------------------------------
# Running SHACL2FOL
# ---------------------------------------------------------------------------

_CONTAINED_RE = re.compile(r"contained in the second\? (true|false)")
_CAUSE_RE = re.compile(r"Caused by: ([\w.$]+)")
_EXCEPTION_RE = re.compile(r"Exception in thread \"main\" ([\w.$]+)")


def parse_shacl2fol_output(stdout: str, stderr: str, prover_output: str = ""):
    """
    Interpret one run of `SHACL2FOL.jar c <first> <second>`.

    Returns (contained, reason): contained is True/False, or None when the
    check could not be decided, in which case reason says why.
    """
    match = _CONTAINED_RE.search(stdout)
    if match:
        return match.group(1) == "true", None

    if "Failed to parse the output of the theorem prover" in stderr:
        text = prover_output + stdout
        if re.search(r"Time limit reached|SZS status Timeout", text):
            return None, "timeout"
        if re.search(r"Refutation not found|SZS status GaveUp|Memory limit", text):
            return None, "prover_gave_up"
        return None, "prover_error"

    cause = _CAUSE_RE.search(stderr)
    if cause:
        return None, "unsupported:" + cause.group(1).split(".")[-1]
    exception = _EXCEPTION_RE.search(stderr)
    if exception:
        return None, "error:" + exception.group(1).split(".")[-1]
    return None, "error:no_result"


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


def _containment(tools: dict, first: Path, second: Path, work: Path, timeout: int):
    """One containment check `first <= second`. Returns (contained, reason)."""
    prover_out = work / "prover_output.txt"
    wrapper = work / "prover.sh"
    lib = tools["lib"]
    wrapper.write_text(
        "#!/bin/bash\n"
        + (f'export LD_LIBRARY_PATH={shlex.quote(lib)}:"$LD_LIBRARY_PATH"\n' if lib else "")
        + f'{shlex.quote(tools["prover"])} --time_limit {int(timeout)} "$@" | tee {shlex.quote(str(prover_out))}\n'
        + 'exit "${PIPESTATUS[0]}"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    # SHACL2FOL reads this file from its working directory.
    (work / "config.properties").write_text(
        "encodeUNA=false\n"
        "tptpPrefix=fof\n"
        f"tptpOutputFile={work / 'theory.tptp'}\n"
        f"proverPath={wrapper}\n"
        "proverArguments=--saturation_algorithm fmb\n",
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [tools["java"], "-jar", tools["jar"], "c", str(first), str(second)],
        cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,  # so that the prover started by Java can be killed with it
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout + GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        proc.communicate()
        return None, "timeout"
    finally:
        if proc.poll() is None:
            _kill_group(proc)

    prover_text = prover_out.read_text(errors="replace") if prover_out.exists() else ""
    return parse_shacl2fol_output(stdout, stderr, prover_text)


def classify(ref_in_out: Optional[bool], out_in_ref: Optional[bool]):
    """Map the two containment results to (equivalent, direction)."""
    if ref_in_out is True and out_in_ref is True:
        return True, "equivalent"
    if ref_in_out is False and out_in_ref is True:
        return False, "output_stricter"
    if ref_in_out is True and out_in_ref is False:
        return False, "output_more_permissive"
    if ref_in_out is False and out_in_ref is False:
        return False, "incomparable"
    if ref_in_out is False or out_in_ref is False:
        return False, None            # refuted in one direction, the other undecided
    return None, None                 # nothing refuted, something undecided


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_logical_equivalence(
    reference_shacl: str,
    output_shacl: str,
    timeout: int = DEFAULT_TIMEOUT,
    jar: Optional[str] = None,
    prover: Optional[str] = None,
    java: Optional[str] = None,
    strip_annotation_triples: bool = False,
    check_unsupported_components: bool = True,
    keep_files: bool = False,
) -> dict:
    """
    Decide whether `output_shacl` is logically equivalent to `reference_shacl`.

    Both arguments are Turtle strings. `timeout` is the time budget in seconds
    of each of the two containment checks. The function never raises for
    problems of the tools or of the input: they are reported in the result.

    Returns
        {
          "status":               "success" | "undetermined" | "unavailable",
          "equivalent":           True | False | None,
          "direction":            "equivalent" | "output_stricter" |
                                  "output_more_permissive" | "incomparable" | None,
          "ref_contained_in_out": True | False | None,
          "out_contained_in_ref": True | False | None,
          "reason":               None, or why the outcome is not determined:
                                  "timeout", "unsupported:<exception>",
                                  "unsupported_components_differ:<components>",
                                  "error:<...>", "parse_error", or a message
                                  about missing tools when unavailable,
          "unsupported_components": names of the constraint components ignored
                                  by SHACL2FOL that occur in either graph,
          "time_s":               seconds spent,
        }
    """
    started = time.time()

    unsupported_present = []

    def result(status, equivalent=None, direction=None, a=None, b=None, reason=None):
        return {
            "status": status, "equivalent": equivalent, "direction": direction,
            "ref_contained_in_out": a, "out_contained_in_ref": b,
            "reason": reason, "unsupported_components": unsupported_present,
            "time_s": round(time.time() - started, 3),
        }

    tools, problem = resolve_tools(jar, prover, java)
    if tools is None:
        return result("unavailable", reason=problem)
    timeout = max(1, int(timeout))

    try:
        ref_graph = Graph().parse(data=reference_shacl, format="turtle")
        out_graph = Graph().parse(data=output_shacl, format="turtle")
    except Exception as e:
        return result("undetermined", reason=f"parse_error:{type(e).__name__}")

    ref_ignored = unsupported_constraints(ref_graph)
    out_ignored = unsupported_constraints(out_graph)
    unsupported_present.extend(sorted({c for _, c, _ in ref_ignored | out_ignored}))
    differing = sorted({c for _, c, _ in ref_ignored ^ out_ignored})

    out_graph = rename_shapes(out_graph)
    if strip_annotation_triples:
        strip_annotations(ref_graph)
        strip_annotations(out_graph)

    work = Path(tempfile.mkdtemp(prefix="ler_"))
    try:
        if " " in str(work):
            return result("undetermined", reason="error:work_directory_contains_spaces")
        ref_file, out_file = work / "reference.ttl", work / "output.ttl"
        ref_graph.serialize(destination=str(ref_file), format="turtle")
        out_graph.serialize(destination=str(out_file), format="turtle")

        ref_in_out, reason_a = _containment(tools, ref_file, out_file, work, timeout)
        if reason_a and reason_a.startswith(("unsupported", "error")):
            # The same graphs fail in the same way in the other direction.
            out_in_ref, reason_b = None, reason_a
        else:
            out_in_ref, reason_b = _containment(tools, out_file, ref_file, work, timeout)
    finally:
        if not keep_files:
            shutil.rmtree(work, ignore_errors=True)

    equivalent, direction = classify(ref_in_out, out_in_ref)
    # The reason names the direction that could not be decided, if any.
    reason = None if (ref_in_out is not None and out_in_ref is not None) else (reason_a or reason_b)

    if equivalent is True and check_unsupported_components and differing:
        # SHACL2FOL ignores these constraints, so "equivalent" cannot be trusted.
        return result("undetermined", None, None, ref_in_out, out_in_ref,
                      "unsupported_components_differ:" + ",".join(differing))
    status = "success" if equivalent is not None else "undetermined"
    return result(status, equivalent, direction, ref_in_out, out_in_ref, reason)
