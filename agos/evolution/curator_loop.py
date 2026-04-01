"""Curator CLI — run from host machine or via Claude Code /loop.

Usage:
    python -m agos.evolution.curator_loop                           # report only
    python -m agos.evolution.curator_loop --release                  # report + release
    python -m agos.evolution.curator_loop --fleet-dir .opensculpt-fleet
    python -m agos.evolution.curator_loop --seed                     # apply latest release
    python -m agos.evolution.curator_loop --contribute               # export knowledge

Claude Code integration:
    /loop 10m python -m agos.evolution.curator_loop --release
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agos.evolution.curator import (
    apply_release,
    create_release,
    export_contribution,
    generate_fleet_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenSculpt Fleet Curator — aggregate, score, and release evolved knowledge",
    )
    parser.add_argument(
        "--fleet-dir", type=Path, default=Path(".opensculpt-fleet"),
        help="Directory containing per-node subdirectories (default: .opensculpt-fleet)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Release output directory (default: .opensculpt/releases)",
    )
    parser.add_argument(
        "--min-score", type=float, default=0.3,
        help="Minimum composite score for release inclusion (default: 0.3)",
    )
    parser.add_argument(
        "--release", action="store_true",
        help="Generate report AND create a release package",
    )
    parser.add_argument(
        "--seed", type=Path, default=None, nargs="?", const="auto",
        help="Apply latest release to local workspace. Optionally specify release dir.",
    )
    parser.add_argument(
        "--contribute", action="store_true",
        help="Export anonymized local knowledge for federation",
    )
    parser.add_argument(
        "--workspace", type=Path, default=Path(".opensculpt"),
        help="Local workspace directory (default: .opensculpt)",
    )

    args = parser.parse_args()

    # Seed mode: apply a release
    if args.seed is not None:
        if args.seed == "auto":
            # Find latest release
            releases_dir = args.workspace / "releases"
            if not releases_dir.exists():
                print("No releases found. Run --release first.")
                sys.exit(1)
            versions = sorted(releases_dir.glob("v*"))
            if not versions:
                print("No release versions found.")
                sys.exit(1)
            release_dir = versions[-1]
        else:
            release_dir = args.seed

        print(f"Seeding from {release_dir}...")
        result = apply_release(release_dir, args.workspace)
        print(f"Applied: {result['tools']} tools, {result['skills']} skills, "
              f"{result['constraints']} constraints, {result['resolutions']} resolutions")
        return

    # Contribute mode: export knowledge
    if args.contribute:
        contrib_dir = export_contribution(args.workspace)
        print(f"Contribution exported to: {contrib_dir}")
        print("Submit this directory to the OpenSculpt knowledge registry.")
        return

    # Report mode (always runs)
    print(f"Scanning fleet at {args.fleet_dir}...")
    report = generate_fleet_report(args.fleet_dir)

    # Write report to disk
    curator_dir = args.workspace / "curator"
    curator_dir.mkdir(parents=True, exist_ok=True)
    report_path = curator_dir / "fleet_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Fleet report written to: {report_path}")
    print()
    print(report)

    # Release mode
    if args.release:
        print("\nCreating release...")
        release_dir = create_release(
            args.fleet_dir,
            output_dir=args.output_dir,
            min_score=args.min_score,
        )
        print(f"Release created at: {release_dir}")

        # Show what's in it
        manifest_path = release_dir / "manifest.json"
        if manifest_path.exists():
            import json
            manifest = json.loads(manifest_path.read_text())
            print(f"  Tools: {len(manifest.get('tools_included', []))}")
            print(f"  Skills: {len(manifest.get('skills_included', []))}")
            print(f"  Constraints: {manifest.get('constraints_count', 0)}")
            print(f"  Resolutions: {manifest.get('resolutions_count', 0)}")


if __name__ == "__main__":
    main()
