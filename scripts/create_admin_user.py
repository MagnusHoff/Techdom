from __future__ import annotations

import argparse
import asyncio
import sys

from techdom.domain.auth.models import UserRole
from techdom.infrastructure.db import session_scope
from techdom.services.auth import DuplicateEmailError, create_user


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an admin user account")
    parser.add_argument("--email", required=True, help="Admin email address")
    parser.add_argument("--username", required=True, help="Admin username")
    parser.add_argument("--password", required=True, help="Temporary password to set")
    return parser.parse_args()


async def _create_admin(email: str, username: str, password: str) -> None:
    async with session_scope() as session:
        try:
            user = await create_user(
                session, email=email, username=username, password=password, role=UserRole.ADMIN
            )
        except DuplicateEmailError:
            print(f"Admin user with email/username {email}/{username} already exists.")
            return
    print(f"Created admin user {user.email} ({user.username}) with role {user.role.value}.")


def main() -> int:
    args = _parse_args()
    asyncio.run(_create_admin(args.email, args.username, args.password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
