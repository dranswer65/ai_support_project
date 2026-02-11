from datetime import datetime, time

# GCC Business Hours (Sun–Thu, 9AM–6PM KSA)
BUSINESS_DAYS = {6, 0, 1, 2, 3}  # Sunday = 6
START_HOUR = time(9, 0)
END_HOUR = time(18, 0)


def is_gcc_business_hours(now=None):
    now = now or datetime.utcnow()
    local_hour = now.hour  # assume UTC≈KSA for demo

    if now.weekday() not in BUSINESS_DAYS:
        return False

    return START_HOUR.hour <= local_hour < END_HOUR.hour
