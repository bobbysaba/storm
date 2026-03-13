# core/vehicle.py
# current state of a tracked vehicle, including its latest observation.


# vehicle state container
class Vehicle:
    # create a new vehicle object
    def __init__(self, id, lat, lon, color="#FF6B35", latest_obs=None):
        # assign id
        self.id = id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign color
        self.color = color
        # assign latest observation
        self.latest_obs = latest_obs
