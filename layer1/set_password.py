"""Set the password of each role's account. Run it in YOUR terminal; the passwords are typed hidden and are never
written anywhere except as a salted hash in layer1/stores/credentials.json.

    .venv\\Scripts\\python -m layer1.set_password            # asks for all four accounts
    .venv\\Scripts\\python -m layer1.set_password admin      # asks for one account
"""
import getpass
import sys

from . import audit_log, auth


def main(argv: list) -> int:
    names = argv or sorted(auth.usernames())
    unknown = [n for n in names if n not in auth.usernames()]
    if unknown:
        print(f"Unknown username(s): {', '.join(unknown)}. Accounts: {', '.join(sorted(auth.usernames()))}")
        return 2
    for name in names:
        while True:
            first = getpass.getpass(f"New password for '{name}' (at least {auth.MIN_PASSWORD_LENGTH} characters): ")
            if getpass.getpass("Type it again: ") != first:
                print("The two entries differ. Try again.")
                continue
            try:
                auth.set_password(name, first)
            except ValueError as e:
                print(e)
                continue
            break
        audit_log.log_event("PASSWORD_CHANGED", None, {"user_id": auth.usernames()[name], "username": name, "by": "set_password script"})
        print(f"Password set for '{name}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
