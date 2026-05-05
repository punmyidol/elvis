"""
chatbot/scripts/embed_cadquery_docs.py

Embed CadQuery API documentation into the cadquery_docs vector collection in elvis.db.
Re-running is safe — upsert skips unchanged content.

Run from the chatbot/ directory (so DB_PATH resolves correctly):
    cd chatbot && python scripts/embed_cadquery_docs.py

Or pass the DB path explicitly via env:
    ELVIS_DB_PATH=/abs/path/to/chatbot/elvis.db python chatbot/scripts/embed_cadquery_docs.py

Sources (196 items):
  - Workplane class + 107 public methods
  - Assembly class + 9 public methods
  - Sketch class + 47 public methods
  - 33 selector classes
  - Hand-written: string selector syntax, export formats, chaining guide
"""

import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.vector_store import upsert_vector
from core.config import DB_PATH


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_method(cls_name: str, name: str, method) -> str:
    try:
        sig = str(inspect.signature(method))
    except (ValueError, TypeError):
        sig = "(...)"
    doc = inspect.getdoc(method) or ""
    return f"== {cls_name}.{name}{sig} ==\n{doc}"


def _fmt_class(name: str, cls) -> str:
    doc = inspect.getdoc(cls) or ""
    return f"== {name} ==\n{doc}"


def _public_methods(cls):
    return [
        (name, m)
        for name, m in inspect.getmembers(cls, inspect.isfunction)
        if inspect.getdoc(m) and not name.startswith("_")
    ]


# ---------------------------------------------------------------------------
# Hand-written reference sections
# ---------------------------------------------------------------------------

_STRING_SELECTORS = """\
== String Selector Syntax ==
CadQuery accepts string selectors in .faces(), .edges(), .wires(), .vertices(), etc.

Axis selectors (pick the face/edge furthest in a direction):
  ">X"  largest X (rightmost face)       "<X"  smallest X (leftmost face)
  ">Y"  largest Y                         "<Y"  smallest Y
  ">Z"  largest Z (top face)              "<Z"  smallest Z (bottom face)

Orientation selectors (pick faces/edges parallel to a plane or axis):
  "|X"  faces/edges perpendicular to X axis (parallel to YZ plane)
  "|Y"  faces/edges perpendicular to Y axis
  "|Z"  faces/edges perpendicular to Z axis (horizontal faces)
  "#X"  edges parallel to X axis
  "#Y"  edges parallel to Y axis
  "#Z"  edges parallel to Z axis (vertical edges)

Combining selectors:
  & (and)  — both conditions must match
  | (or)   — either condition matches
  - (not)  — inverts a selector
  Example:  .edges("|Z and >X")

Tag selectors:
  .tag("myface")        — tag current selection state
  .faces(tag="myface")  — recall tagged selection

Examples:
  # Hole in the top face
  result = cq.Workplane("XY").box(20, 20, 10).faces(">Z").workplane().hole(5)

  # Fillet only vertical edges
  result = cq.Workplane("XY").box(10, 10, 10).edges("#Z").fillet(1)

  # Chamfer the bottom edge of a cylinder
  result = cq.Workplane("XY").cylinder(20, 5).edges("<Z").chamfer(0.5)
"""

_EXPORT_FORMATS = """\
== CadQuery Export Formats ==
STEP (.step / .stp) — preferred for solid geometry (use this in Elvis):
  result.val().exportStep("output.step")
  Compatible with FreeCAD, SolidWorks, Fusion 360, CATIA.

STL (.stl) — mesh format for 3D printing:
  cq.exporters.export(result, "output.stl")

SVG (.svg) — 2D projection:
  cq.exporters.export(result, "output.svg")

GLTF / GLB — web 3D viewer format:
  cq.exporters.export(result, "output.gltf")
  cq.exporters.export(result, "output.glb")

DXF (.dxf) — 2D cross-section for laser cutting:
  cq.exporters.export(result, "output.dxf")

AMF (.amf):
  cq.exporters.export(result, "output.amf")

In the Elvis CAD tool the output path is injected at runtime — always write:
  result.val().exportStep(OUTPUT_PATH)
"""

_CHAINING_GUIDE = """\
== Workplane Chaining Rules ==
CadQuery uses a fluent (method-chaining) API. Each method returns a Workplane.

Basic pattern:
  result = (
      cq.Workplane("XY")   # start on XY plane
        .box(20, 20, 10)   # 20×20×10 mm solid box
        .faces(">Z")       # select top face
        .workplane()       # re-anchor 2D plane there
        .hole(5)           # drill 5 mm hole
  )

Key rules:
1. Start with cq.Workplane("XY"), "YZ", or "XZ".
2. Primitives (box, cylinder, sphere, cone, wedge) create solids.
3. .faces(), .edges(), .vertices() select sub-shapes.
4. .workplane() re-establishes the 2D drawing plane at the selected face.
5. 2D shapes (.circle, .rect, .polygon, .slot2D) draw on the current plane.
6. .extrude(), .revolve(), .loft() convert 2D wire to 3D.
7. .cut(), .union(), .intersect() do boolean ops with another shape.
8. .shell(), .fillet(), .chamfer() post-process the solid.
9. Always assign the final solid to `result`.

Common patterns:
  # Box with through-hole
  result = cq.Workplane("XY").box(30, 20, 10).faces(">Z").workplane().hole(8)

  # Cylinder with chamfered top edge
  result = cq.Workplane("XY").cylinder(20, 5).edges(">Z").chamfer(1)

  # Extruded profile
  result = (
      cq.Workplane("XY")
        .rect(30, 20)
        .extrude(10)
  )

  # Hollow box (shell)
  result = cq.Workplane("XY").box(30, 30, 30).shell(-2)

  # Revolve a 2D profile around Z
  result = (
      cq.Workplane("XZ")
        .lineTo(5, 0).lineTo(5, 10).lineTo(0, 10)
        .close()
        .revolve()
  )
"""


# ---------------------------------------------------------------------------
# Doc collector
# ---------------------------------------------------------------------------

def collect_docs() -> list[tuple[str, str]]:
    import cadquery as cq
    import cadquery.selectors as sel

    items: list[tuple[str, str]] = []

    # Class overviews
    for cls_name, cls in (("Workplane", cq.Workplane), ("Assembly", cq.Assembly), ("Sketch", cq.Sketch)):
        doc = inspect.getdoc(cls) or ""
        if doc:
            items.append((f"cadquery::{cls_name}::overview", f"== {cls_name} class ==\n{doc}"))

    # Public methods
    for cls_name, cls in (("Workplane", cq.Workplane), ("Assembly", cq.Assembly), ("Sketch", cq.Sketch)):
        for name, method in _public_methods(cls):
            items.append((
                f"cadquery::{cls_name}::{name}",
                _fmt_method(cls_name, name, method),
            ))

    # Selector classes
    for name, cls in sorted(inspect.getmembers(sel, inspect.isclass)):
        if name.startswith("_"):
            continue
        doc = inspect.getdoc(cls) or ""
        if doc and name not in ("ABC",):  # skip stdlib base classes
            items.append((f"cadquery::selectors::{name}", _fmt_class(name, cls)))

    # Hand-written reference sections
    items.append(("cadquery::string_selectors",  _STRING_SELECTORS))
    items.append(("cadquery::export_formats",     _EXPORT_FORMATS))
    items.append(("cadquery::chaining_guide",     _CHAINING_GUIDE))

    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"DB: {os.path.abspath(DB_PATH)}")
    print("Collecting CadQuery doc items…")
    items = collect_docs()
    print(f"Found {len(items)} items to embed.\n")

    ok = failed = skipped = 0
    for i, (source_id, content) in enumerate(items, 1):
        label = source_id.split("::")[-1]
        try:
            n = upsert_vector(source_id, "cadquery_docs", content, db_path=DB_PATH)
            if n:
                ok += 1
                print(f"  [{i:>3}/{len(items)}] {label}")
            else:
                skipped += 1
        except Exception as e:
            print(f"  [{i:>3}/{len(items)}] FAIL {label}: {e}")
            failed += 1

    print(f"\nDone: {ok} embedded, {skipped} skipped (unchanged), {failed} failed.")


if __name__ == "__main__":
    main()
