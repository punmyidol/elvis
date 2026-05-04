"""
step_to_stl.py — Convert STEP files to STL using CadQuery

Usage:
    python step_to_stl.py input.step                  # outputs input.stl
    python step_to_stl.py input.step -o output.stl    # custom output path
    python step_to_stl.py parts/                      # batch convert directory
    python step_to_stl.py input.step --tolerance 0.01 # finer mesh

Requirements:
    pip install cadquery
"""

import argparse
import sys
from pathlib import Path


def convert(
    input_path: Path,
    output_path: Path,
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
    verbose: bool = True,
) -> dict:
    """
    Convert a single STEP file to STL.

    Args:
        input_path:        Path to .step/.stp file
        output_path:       Path to write .stl file
        tolerance:         Linear deflection — smaller = finer mesh, slower (default 0.1mm)
        angular_tolerance: Angular deflection in radians (default 0.1)
        verbose:           Print progress

    Returns:
        dict with keys: success, input, output, error
    """
    try:
        import cadquery as cq
    except ImportError:
        return {
            "success": False,
            "input": str(input_path),
            "output": None,
            "error": "cadquery not installed — run: pip install cadquery",
        }

    if verbose:
        print(f"  Loading  {input_path.name} ...", end="", flush=True)

    try:
        shape = cq.importers.importStep(str(input_path))
    except Exception as e:
        if verbose:
            print(" FAILED")
        return {
            "success": False,
            "input": str(input_path),
            "output": None,
            "error": f"Failed to import STEP: {e}",
        }

    # Validate — reject empty/degenerate geometry
    try:
        vol = shape.val().Volume()
        if vol <= 0:
            if verbose:
                print(" FAILED")
            return {
                "success": False,
                "input": str(input_path),
                "output": None,
                "error": f"Degenerate geometry: volume={vol}",
            }
    except Exception as e:
        if verbose:
            print(f" WARNING (volume check failed: {e})")

    if verbose:
        print(f" Exporting → {output_path.name} ...", end="", flush=True)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cq.exporters.export(
            shape,
            str(output_path),
            exportType="STL",
            tolerance=tolerance,
            angularTolerance=angular_tolerance,
        )
    except Exception as e:
        if verbose:
            print(" FAILED")
        return {
            "success": False,
            "input": str(input_path),
            "output": None,
            "error": f"Failed to export STL: {e}",
        }

    size_kb = output_path.stat().st_size / 1024
    if verbose:
        print(f" ✅ ({size_kb:.1f} KB)")

    return {
        "success": True,
        "input": str(input_path),
        "output": str(output_path),
        "error": None,
    }


def batch_convert(
    directory: Path,
    output_dir: Path | None = None,
    tolerance: float = 0.1,
    angular_tolerance: float = 0.1,
) -> list[dict]:
    """Convert all STEP files in a directory."""
    step_files = list(directory.glob("*.step")) + list(directory.glob("*.stp"))

    if not step_files:
        print(f"No .step/.stp files found in {directory}")
        return []

    results = []
    print(f"Found {len(step_files)} STEP file(s) in {directory}\n")

    for step_path in sorted(step_files):
        out_dir = output_dir or step_path.parent
        out_path = out_dir / step_path.with_suffix(".stl").name

        result = convert(step_path, out_path, tolerance, angular_tolerance)
        results.append(result)

    return results


def print_summary(results: list[dict]) -> None:
    passed = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n{'─' * 50}")
    print(f"  Converted: {len(passed)}/{len(results)}")

    if failed:
        print(f"\n  Failed:")
        for r in failed:
            print(f"    ✗ {Path(r['input']).name}: {r['error']}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert STEP files to STL using CadQuery"
    )
    parser.add_argument("input", help="STEP file or directory containing STEP files")
    parser.add_argument("-o", "--output", help="Output STL path (single file only)")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.1,
        help="Linear mesh tolerance in mm (default: 0.1, finer = smaller value)",
    )
    parser.add_argument(
        "--angular-tolerance",
        type=float,
        default=0.1,
        help="Angular mesh tolerance in radians (default: 0.1)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for batch conversion (default: same as input)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: {input_path} does not exist")
        sys.exit(1)

    # --- Batch mode ---
    if input_path.is_dir():
        if args.output:
            print("Error: --output cannot be used with directory input, use --output-dir")
            sys.exit(1)

        out_dir = Path(args.output_dir) if args.output_dir else None
        results = batch_convert(input_path, out_dir, args.tolerance, args.angular_tolerance)

        if results:
            print_summary(results)
            failed = [r for r in results if not r["success"]]
            sys.exit(1 if failed else 0)

    # --- Single file mode ---
    else:
        if input_path.suffix.lower() not in (".step", ".stp"):
            print(f"Error: expected .step or .stp file, got {input_path.suffix}")
            sys.exit(1)

        if args.output:
            output_path = Path(args.output)
        else:
            output_path = input_path.with_suffix(".stl")

        print(f"Converting STEP → STL")
        print(f"  tolerance: {args.tolerance}mm linear, {args.angular_tolerance}rad angular\n")

        result = convert(input_path, output_path, args.tolerance, args.angular_tolerance)

        if not result["success"]:
            print(f"\nError: {result['error']}")
            sys.exit(1)


if __name__ == "__main__":
    main()