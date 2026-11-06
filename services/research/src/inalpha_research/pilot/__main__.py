"""Usage: python -m inalpha_research.pilot {inventory,freeze,validate} FILE..."""

import argparse
import json
from pathlib import Path

from .artifacts import freeze, inventory, validate_bundle, validate_materials, validate_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "freeze", "validate", "validate-run"))
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "inventory":
        output = inventory(args.files)
    else:
        if len(args.files) != 1:
            parser.error("freeze/validate accepts exactly one file")
        if args.command == "validate-run":
            errors = validate_run(args.files[0])
            print(json.dumps({"ready": not errors, "blockers": errors}, indent=2))
            raise SystemExit(1 if errors else 0)
        source = json.loads(args.files[0].read_text())
        if args.command == "freeze":
            output = freeze(source["case_id"], source["decision_at"], source["evidence"])
        else:
            errors = validate_bundle(source) if "evidence" in source else validate_materials(source)
            output = {"ready": not errors, "blockers": errors}
            print(json.dumps(output, ensure_ascii=False, indent=2))
            raise SystemExit(1 if errors else 0)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
