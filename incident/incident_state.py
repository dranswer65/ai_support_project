# incident/incident_state.py

INCIDENT_MODE = False


def is_incident_mode():
    return INCIDENT_MODE


def enable_incident_mode():
    global INCIDENT_MODE
    INCIDENT_MODE = True


def disable_incident_mode():
    global INCIDENT_MODE
    INCIDENT_MODE = False
