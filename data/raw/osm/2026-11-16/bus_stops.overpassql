
[out:json][timeout:180];
(
  node["highway"="bus_stop"](23.60,90.20,24.05,90.60);
  node["public_transport"="platform"]["bus"="yes"](23.60,90.20,24.05,90.60);
  node["public_transport"="stop_position"]["bus"="yes"](23.60,90.20,24.05,90.60);
  node["amenity"="bus_station"](23.60,90.20,24.05,90.60);
);
out body;
way["amenity"="bus_station"](23.60,90.20,24.05,90.60);
out tags center;
