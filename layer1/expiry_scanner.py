"""CLI: scan for expired documents and list those expiring within 90 days.

  python -m layer1.expiry_scanner
"""
from . import expiry_manager


def main() -> None:
    soon = expiry_manager.expiring_within(90)
    print(f"ACTIVE documents expiring within 90 days (or already expired): {len(soon)}")
    for r in soon:
        print(f"  {r['document_uri']}  {r['expiry_date']}  ({r['days_left']:+d} days)  "
              f"{r['study_type']}  {r['therapeutic_area']}  {r['document_name']}")
    moved = expiry_manager.scan_expired()
    print(f"Moved to UNDER_REVIEW (EXPIRY_TRIGGERED): {moved}")


if __name__ == "__main__":
    main()
