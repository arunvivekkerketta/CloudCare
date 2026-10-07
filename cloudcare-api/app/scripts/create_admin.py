"""Bootstrap the first admin account.

Usage:
    python -m app.scripts.create_admin                    # interactive prompts
    python -m app.scripts.create_admin --name "Ritu Malhotra" \
        --email admin@cloudcare.demo --password "S3cure!Pass" [--department IT]
"""
import argparse
import getpass
import sys

from app.database import SessionLocal, verify_database_connection
from app.models import User, UserRole
from app.security import hash_password


def create_admin(name: str, email: str, password: str, department: str | None = None) -> User:
    verify_database_connection()
    db = SessionLocal()
    try:
        email = email.strip().lower()
        if db.query(User).filter(User.email == email).first():
            print(f"A user with email {email} already exists.", file=sys.stderr)
            sys.exit(1)

        user = User(
            name=name.strip(),
            email=email,
            hashed_password=hash_password(password),
            role=UserRole.admin,
            department=department,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Admin created: {user.email}")
        return user
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a CloudCare admin account.")
    parser.add_argument("--name", help="Full name")
    parser.add_argument("--email", help="Email address")
    parser.add_argument("--password", help="Password (omit to be prompted safely)")
    parser.add_argument("--department", default=None, help="Department (optional)")
    args = parser.parse_args()

    name = args.name or input("Full name: ").strip()
    email = args.email or input("Email: ").strip()
    password = args.password or getpass.getpass("Password (min 8 chars): ")

    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    if not name or not email:
        print("Name and email are required.", file=sys.stderr)
        sys.exit(1)

    create_admin(name, email, password, args.department)


if __name__ == "__main__":
    main()
