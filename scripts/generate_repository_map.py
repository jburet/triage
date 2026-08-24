"""Regenerate config/repository-map.yaml from the architecture document.

    make repository-map

The document is someone's Markdown, dated 2026-04-20 and hand-written; a
repository added since is missing from it and a tenancy changed since is wrong
in it. Generating the seed rather than reading the document at run time is what
turns that drift into a diff a reviewer sees.
"""

from triage.mapping.seed import DOCUMENT_PATH, SEED_PATH, dump_seed, parse_file
from triage.schemas.system_map import Tenancy


def main() -> int:
    entries = parse_file(DOCUMENT_PATH)
    SEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEED_PATH.write_text(dump_seed(entries), encoding="utf-8")

    mono = [entry.repository for entry in entries if entry.tenancy is Tenancy.MONO_TENANT]
    print(f"{len(entries)} repositories written to {SEED_PATH.relative_to(SEED_PATH.parents[1])}")
    print(f"mono-tenant: {', '.join(mono) or 'none'}")
    for entry in entries:
        print(f"  {entry.repository:<28} {entry.tenancy.value:<24} {entry.iac_repo or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
