# the observation record lives in the headless package so the GUI and the
# headless publisher share one definition; re-exported here for the GUI
from headless.storm_headless.observation import Observation

__all__ = ["Observation"]
