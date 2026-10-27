"""Create one admin, one doctor and one patient to test CloudCare by hand.

Put this file in  cloudcare-api/app/scripts/  and run from  cloudcare-api/ :

    python -m app.scripts.create_test_accounts

Safe to run again: accounts that already exist are skipped (never overwritten).
Uses whatever database your .env points to (SQLite by default in development).
"""
from app.database import SessionLocal, verify_database_connection
from app.models import BillingDetail, Patient, Specialty, User, UserRole
from app.security import hash_password

ACCOUNTS = [
    # (name, email, password, role, department)
    ("Test Admin", "admin.test@cloudcare.demo", "Admin#Test123", UserRole.admin, None),
    ("Dr. Test Doctor", "doctor.test@cloudcare.demo", "Doctor#Test123", UserRole.doctor, "Cardiology"),
    ("Test Patient", "patient.test@cloudcare.demo", "Patient#Test123", UserRole.patient, None),
]


def main() -> None:
    verify_database_connection()
    db = SessionLocal()
    try:
        # The booking form lists specialties; make sure the doctor's exists.
        if db.query(Specialty).filter(Specialty.name == "Cardiology").first() is None:
            db.add(Specialty(name="Cardiology"))
            print("+ specialty Cardiology")

        for name, email, password, role, department in ACCOUNTS:
            if db.query(User).filter(User.email == email).first():
                print(f"= {role.value:8} {email} already exists - skipped")
                continue
            user = User(
                name=name,
                email=email,
                hashed_password=hash_password(password),
                role=role,
                department=department,
            )
            db.add(user)
            db.flush()
            if role == UserRole.patient:
                db.add(Patient(user_id=user.id, status="Active"))
                db.add(
                    BillingDetail(
                        user_id=user.id,
                        full_name=name,
                        email=email,
                        billing_address="",
                        payment_method="Not set",
                        currency="INR",
                        status="Default",
                    )
                )
            print(f"+ {role.value:8} {email}   password: {password}")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
