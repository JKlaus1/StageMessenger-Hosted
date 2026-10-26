"""Server-side admin commands. Run inside the container:

  docker compose exec stage-messenger python manage.py set-admin-password
  docker compose exec stage-messenger python manage.py list-leaders
  docker compose exec stage-messenger python manage.py reset-leader-password <username>

Passwords are read from the terminal (never echoed, never passed as arguments).
"""
import getpass
import sys

import store

MIN_PW = 8


def ask_password(prompt='New password: '):
    if not sys.stdin.isatty():
        sys.exit('Needs an interactive terminal — use: ssh -t ... docker compose exec ...')
    for _ in range(3):
        a = getpass.getpass(prompt)
        if len(a) < MIN_PW:
            print(f'At least {MIN_PW} characters.')
            continue
        if a != getpass.getpass('Again: '):
            print("Didn't match.")
            continue
        return a
    sys.exit('Gave up.')


def main(argv):
    store.init()
    cmd = argv[1] if len(argv) > 1 else ''
    if cmd == 'set-admin-password':
        store.set_admin_password(ask_password('New admin password: '))
        print('PASS admin password set (any open admin sessions are now signed out).')
    elif cmd == 'list-leaders':
        rows = store.list_leaders()
        if not rows:
            print('(no leaders yet)')
        for r in rows:
            print(f"{r['id']:>3}  {r['username']:<20} {'active' if r['active'] else 'DISABLED':<9} "
                  f"gigs {r['active_gigs']}/{r['max_gigs']}  {r['display_name']}")
    elif cmd == 'reset-leader-password' and len(argv) > 2:
        match = [r for r in store.list_leaders() if r['username'].lower() == argv[2].lower()]
        if not match:
            sys.exit(f'No leader named {argv[2]}')
        store.update_leader(match[0]['id'], password=ask_password())
        print(f"PASS password reset for {match[0]['username']} (their sessions are signed out).")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv)
