"""Seed the database with the same demo data shown on the static dashboards.

Usage:
    python -m app.scripts.seed_demo            # idempotent: skips existing emails
    python -m app.scripts.seed_demo --fresh    # wipe users/appointments/bills first

Accounts created (password for all: "Demo!Pass123"):
    simran.kaur@cloudcare.demo     patient
    vikram.rao@cloudcare.demo      patient
    farhan.ali@cloudcare.demo      patient
    neha.joshi@cloudcare.demo      patient
    aisha.khan@cloudcare.demo      patient
    deepak.chawla@cloudcare.demo   patient
    anita.sharma@cloudcare.demo    patient
    rohan.das@cloudcare.demo       patient
    aakash.verma@cloudcare.demo    doctor  (Cardiology)
    ibrahim.sheikh@cloudcare.demo  doctor  (Nephrology)
    sana.qureshi@cloudcare.demo    doctor  (Obstetrics)
    meera.iyer@cloudcare.demo      doctor  (Orthopedics)
    priya.nair@cloudcare.demo      nurse   (Ward B)
    karan.mehta@cloudcare.demo     pharmacist (Counter 2)
    farah.sheikh@cloudcare.demo    lab     (Central Laboratory)
    ritu.malhotra@cloudcare.demo   admin
"""
import sys
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal, verify_database_connection
from app.models import Appointment, Bill, Patient, Specialty, User, UserRole
from app.security import hash_password

DEMO_PASSWORD = "Demo!Pass123"

DOCTORS = [
    ("Dr. Aakash Verma", "aakash.verma@cloudcare.demo", "Cardiology"),
    ("Dr. Ibrahim Sheikh", "ibrahim.sheikh@cloudcare.demo", "Nephrology"),
    ("Dr. Sana Qureshi", "sana.qureshi@cloudcare.demo", "Obstetrics"),
    ("Dr. Meera Iyer", "meera.iyer@cloudcare.demo", "Orthopedics"),
]

STAFF = [
    ("Priya Nair", "priya.nair@cloudcare.demo", UserRole.nurse, "Ward B"),
    ("Karan Mehta", "karan.mehta@cloudcare.demo", UserRole.pharmacist, "Counter 2"),
    ("Farah Sheikh", "farah.sheikh@cloudcare.demo", UserRole.lab, "Central Laboratory"),
    ("Ritu Malhotra", "ritu.malhotra@cloudcare.demo", UserRole.admin, None),
]

# (name, email, age, condition, doctor_email, risk, status) — matches the admin
# dashboard patient directory.
PATIENTS = [
    ("Simran Kaur", "simran.kaur@cloudcare.demo", 34, "Type 2 Diabetes", "aakash.verma@cloudcare.demo", 82, "Active"),
    ("Vikram Rao", "vikram.rao@cloudcare.demo", 52, "Cardiac follow-up", "aakash.verma@cloudcare.demo", 76, "Admitted"),
    ("Farhan Ali", "farhan.ali@cloudcare.demo", 47, "CKD stage 2", "ibrahim.sheikh@cloudcare.demo", 64, "Admitted"),
    ("Neha Joshi", "neha.joshi@cloudcare.demo", 29, "Chronic migraine", "aakash.verma@cloudcare.demo", 24, "Active"),
    ("Aisha Khan", "aisha.khan@cloudcare.demo", 31, "Prenatal care", "sana.qureshi@cloudcare.demo", 38, "Active"),
    ("Deepak Chawla", "deepak.chawla@cloudcare.demo", 58, "Fracture recovery", "meera.iyer@cloudcare.demo", 31, "Admitted"),
    ("Anita Sharma", "anita.sharma@cloudcare.demo", 41, "Post-op recovery", "meera.iyer@cloudcare.demo", 22, "Admitted"),
    ("Rohan Das", "rohan.das@cloudcare.demo", 45, "Hypertension", "aakash.verma@cloudcare.demo", 48, "Active"),
]

APPOINTMENTS = [
    # (patient_email, doctor_email, offset_hours, department, mode, status, reason)
    # Status vocabulary: Requested / Confirmed / Declined / Cancelled (seeded
    # rows stay in the future so they occupy slots they claim).
    ("simran.kaur@cloudcare.demo", "aakash.verma@cloudcare.demo", 26, "Cardiology", "In-person", "Requested", "Diabetes follow-up"),
    ("vikram.rao@cloudcare.demo", "aakash.verma@cloudcare.demo", 27, "Cardiology", "In-person", "Requested", "Cardiac review"),
    ("neha.joshi@cloudcare.demo", "aakash.verma@cloudcare.demo", 29, "Neurology", "Telemedicine", "Confirmed", "Migraine consult"),
    ("rohan.das@cloudcare.demo", "aakash.verma@cloudcare.demo", 31, "Cardiology", "In-person", "Requested", "BP check"),
    ("aisha.khan@cloudcare.demo", "sana.qureshi@cloudcare.demo", 33, "Obstetrics", "In-person", "Confirmed", "Prenatal check-in"),
    ("farhan.ali@cloudcare.demo", "ibrahim.sheikh@cloudcare.demo", 35, "Nephrology", "In-person", "Requested", "Renal review"),
    ("deepak.chawla@cloudcare.demo", "meera.iyer@cloudcare.demo", 38, "Orthopedics", "In-person", "Requested", "Fracture review"),
]

BILLS = [
    # (patient_email, invoice_no, amount, status, description)
    ("simran.kaur@cloudcare.demo", "INV-2293", "6750.00", "Pending", "Lab panel — HbA1c"),
    ("deepak.chawla@cloudcare.demo", "INV-2288", "18400.00", "Paid", "Orthopedic surgery & care"),
    ("aisha.khan@cloudcare.demo", "INV-2276", "3200.00", "Paid", "Prenatal consultation"),
    ("farhan.ali@cloudcare.demo", "INV-2265", "9100.00", "Overdue", "Nephrology panel & dialysis"),
    ("simran.kaur@cloudcare.demo", "INV-2187", "1500.00", "Paid", "Consultation — Cardiology"),
    ("vikram.rao@cloudcare.demo", "INV-2251", "2750.00", "Pending", "Cardiac enzymes & ECG"),
]


def seed(fresh: bool = False) -> None:
    verify_database_connection()
    db = SessionLocal()
    try:
        if fresh:
            print("Dropping existing demo rows…")
            db.query(Appointment).delete()
            db.query(Bill).delete()
            db.query(Patient).delete(synchronize_session=False)
            db.query(User).delete(synchronize_session=False)
            db.commit()

        def get_or_create(name, email, role, department=None):
            user = db.query(User).filter(User.email == email).first()
            if user:
                return user, False
            user = User(
                name=name,
                email=email,
                hashed_password=hash_password(DEMO_PASSWORD),
                role=role,
                department=department,
            )
            db.add(user)
            db.flush()
            return user, True

        doctors: dict[str, User] = {}
        for name, email, dept in DOCTORS:
            doctor, created = get_or_create(name, email, UserRole.doctor, dept)
            doctors[email] = doctor
            if created:
                print(f"+ doctor  {email}")

        for name, email, role, dept in STAFF:
            _, created = get_or_create(name, email, role, dept)
            if created:
                print(f"+ {role.value:<10} {email}")

        # Specialties mirror the seeded doctors' departments so the booking
        # dropdown and the admin Specialties panel have content out of the
        # box (idempotent: match by exact name).
        for dept in sorted({d[2] for d in DOCTORS}):
            if db.query(Specialty).filter(Specialty.name == dept).first():
                continue
            db.add(Specialty(name=dept))
            print(f"+ specialty {dept}")
        db.commit()

        patients: dict[str, Patient] = {}
        for name, email, age, condition, doctor_email, risk, status in PATIENTS:
            user, created = get_or_create(name, email, UserRole.patient)
            # SessionLocal uses autoflush=False: a just-added Patient is NOT
            # visible to queries until flushed, so get-or-create explicitly.
            patient = db.query(Patient).filter(Patient.user_id == user.id).first()
            if patient is None:
                patient = Patient(
                    user_id=user.id,
                    age=age,
                    condition=condition,
                    assigned_doctor_id=doctors[doctor_email].id,
                    risk_score=risk,
                    status=status,
                )
                db.add(patient)
                db.flush()
                print(f"+ patient {email}")
            patients[email] = patient

        base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        existing_appts = db.query(Appointment).count()
        if existing_appts == 0:
            for patient_email, doctor_email, hours, dept, mode, status, reason in APPOINTMENTS:
                db.add(
                    Appointment(
                        patient_id=patients[patient_email].id,
                        doctor_id=doctors[doctor_email].id,
                        scheduled_at=base + timedelta(hours=hours),
                        duration_minutes=30,
                        department=dept,
                        mode=mode,
                        status=status,
                        reason=reason,
                    )
                )
            print(f"+ {len(APPOINTMENTS)} appointments")

        existing_bills = db.query(Bill).count()
        if existing_bills == 0:
            for patient_email, invoice_no, amount, status, description in BILLS:
                db.add(
                    Bill(
                        patient_id=patients[patient_email].id,
                        invoice_no=invoice_no,
                        amount=amount,
                        status=status,
                        description=description,
                    )
                )
            print(f"+ {len(BILLS)} bills")

        db.commit()
        print("\nSeed complete. All demo accounts use password: Demo!Pass123")
        print("Admin login: ritu.malhotra@cloudcare.demo (role: admin)")
    finally:
        db.close()


if __name__ == "__main__":
    if "--fresh" in sys.argv:
        seed(fresh=True)
    else:
        seed()
