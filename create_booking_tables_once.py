from core.booking_store_pg import ensure_booking_tables

if __name__ == "__main__":
    ensure_booking_tables()
    print("✅ Booking tables created/verified: clinics, departments, doctors, doctor_schedules, appointments")