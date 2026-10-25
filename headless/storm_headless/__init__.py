"""
STORM headless vehicle publisher.

Tails a vehicle's instrument-logger file and publishes the latest observation
to the STORM MQTT broker without running the STORM GUI.  The modules here are
also imported by the GUI so both share one row parser and one payload format;
nothing in this package may depend on Qt.
"""

__version__ = "0.1.0"
