# core/vehicle.py
# current state of a tracked vehicle, including its latest observation.


# vehicle state container
class Vehicle:
    # create a new vehicle object
    def __init__(self, id, lat, lon, latest_obs=None, icon_type="car"):
        # assign id
        self.id = id
        # assign latitude
        self.lat = lat
        # assign longitude
        self.lon = lon
        # assign latest observation
        self.latest_obs = latest_obs
        # assign icon type (car, drone, mesonet, lidar, radar)
        self.icon_type = icon_type
