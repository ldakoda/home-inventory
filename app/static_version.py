import os

# Appended to /static/css/styles.css as ?v=... so a deploy that changes the
# stylesheet always gets fetched, even by browsers (phones especially) still
# holding a long-lived cached copy from before the no-cache header existed.
_CSS = os.path.join(os.path.dirname(__file__), "static", "css", "styles.css")
try:
    STATIC_VERSION = str(int(os.path.getmtime(_CSS)))
except OSError:
    STATIC_VERSION = "0"
