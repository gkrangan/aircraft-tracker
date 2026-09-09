import argparse
import csv
import json
import math
import os
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import pyModeS as pms
from pyModeS.extra.rtlreader import RtlReader

STALE_SECONDS = 60.0
EARTH_RADIUS_NM = 3440.065


def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))


@dataclass
class Aircraft:
    icao: str
    callsign: str = ""
    category: "int | None" = None
    altitude: "int | None" = None
    ground_speed: "float | None" = None
    track: "float | None" = None
    vertical_rate: "int | None" = None
    lat: "float | None" = None
    lon: "float | None" = None
    distance_nm: "float | None" = None
    message_count: int = 0
    last_seen: float = field(default_factory=time.monotonic)
    _even_msg: "str | None" = None
    _even_t: "float | None" = None
    _odd_msg: "str | None" = None
    _odd_t: "float | None" = None

    def to_dict(self):
        return {
            "icao": self.icao,
            "callsign": self.callsign,
            "altitude": self.altitude,
            "ground_speed": round(self.ground_speed) if self.ground_speed is not None else None,
            "track": round(self.track) if self.track is not None else None,
            "vertical_rate": self.vertical_rate,
            "lat": self.lat,
            "lon": self.lon,
            "distance_nm": round(self.distance_nm, 1) if self.distance_nm is not None else None,
            "messages": self.message_count,
            "age": round(time.monotonic() - self.last_seen, 1),
        }


class AircraftTable:
    """Thread-safe registry of tracked aircraft, updated from decoded ADS-B messages."""

    def __init__(self, ref_lat=None, ref_lon=None):
        self.lock = threading.Lock()
        self.aircraft: dict[str, Aircraft] = {}
        self.ref_lat = ref_lat
        self.ref_lon = ref_lon

    def update(self, msg, t):
        if pms.df(msg) not in (17, 18):
            return
        icao = pms.icao(msg)
        if icao is None:
            return

        with self.lock:
            ac = self.aircraft.get(icao)
            if ac is None:
                ac = Aircraft(icao=icao)
                self.aircraft[icao] = ac
            ac.last_seen = time.monotonic()
            ac.message_count += 1

            tc = pms.adsb.typecode(msg)
            if tc is None:
                return

            if 1 <= tc <= 4:
                ac.callsign = pms.adsb.callsign(msg).replace("_", "").strip()
                ac.category = pms.adsb.category(msg)
            elif tc == 19:
                velocity = pms.adsb.velocity(msg)
                if velocity is not None:
                    ac.ground_speed, ac.track, ac.vertical_rate = velocity[0], velocity[1], velocity[2]
            elif (9 <= tc <= 18) or (20 <= tc <= 22):
                ac.altitude = pms.adsb.altitude(msg)
                self._update_position(ac, msg, t)

    def _update_position(self, ac, msg, t):
        if self.ref_lat is not None and self.ref_lon is not None:
            try:
                latlon = pms.adsb.position_with_ref(msg, self.ref_lat, self.ref_lon)
            except Exception:
                latlon = None
            if latlon is not None:
                ac.lat, ac.lon = latlon
            return

        if pms.adsb.oe_flag(msg) == 0:
            ac._even_msg, ac._even_t = msg, t
        else:
            ac._odd_msg, ac._odd_t = msg, t

        if ac._even_msg and ac._odd_msg and abs(ac._even_t - ac._odd_t) < 10:
            try:
                latlon = pms.adsb.airborne_position(ac._even_msg, ac._odd_msg, ac._even_t, ac._odd_t)
            except Exception:
                latlon = None
            if latlon is not None:
                ac.lat, ac.lon = latlon

    def snapshot(self):
        now = time.monotonic()
        with self.lock:
            stale = [icao for icao, ac in self.aircraft.items() if now - ac.last_seen > STALE_SECONDS]
            for icao in stale:
                del self.aircraft[icao]
            live = list(self.aircraft.values())

        if self.ref_lat is not None and self.ref_lon is not None:
            for ac in live:
                if ac.lat is not None and ac.lon is not None:
                    ac.distance_nm = haversine_nm(self.ref_lat, self.ref_lon, ac.lat, ac.lon)

        live.sort(key=lambda a: a.distance_nm if a.distance_nm is not None else float("inf"))
        return live


class TrackerReceiver(RtlReader):
    def __init__(self, table, gain="auto", **kwargs):
        super().__init__(**kwargs)
        self.table = table
        self.sdr.gain = gain

    def handle_messages(self, messages):
        for msg, t in messages:
            self.table.update(msg, t)


def start_receiver(table, gain):
    try:
        receiver = TrackerReceiver(table, gain=gain)
    except Exception as exc:
        raise RuntimeError(
            "Could not open the RTL-SDR device. Make sure the FlightAware ProStick is "
            f"plugged in and librtlsdr is installed (brew install librtlsdr). Details: {exc}"
        ) from exc

    thread = threading.Thread(target=_run_receiver, args=(receiver,), daemon=True)
    thread.start()
    return receiver, thread


def _run_receiver(receiver):
    try:
        receiver.run()
    except Exception:
        pass


def ensure_csv_header(path):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["timestamp_utc", "icao", "callsign", "altitude_ft", "ground_speed_kt",
                 "track_deg", "vertical_rate_fpm", "lat", "lon", "distance_nm", "messages"]
            )


def append_csv_rows(path, aircraft_list):
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for ac in aircraft_list:
            writer.writerow(
                [ts, ac.icao, ac.callsign, ac.altitude,
                 round(ac.ground_speed) if ac.ground_speed is not None else None,
                 round(ac.track) if ac.track is not None else None,
                 ac.vertical_rate, ac.lat, ac.lon,
                 round(ac.distance_nm, 1) if ac.distance_nm is not None else None,
                 ac.message_count]
            )


def format_table(aircraft_list, ref_set):
    header = f"{'ICAO':<7}{'CALLSIGN':<10}{'ALT(ft)':>9}{'GS(kt)':>8}{'TRK':>6}{'VS(fpm)':>9}"
    header += f"{'LAT':>10}{'LON':>11}"
    if ref_set:
        header += f"{'DIST(nm)':>10}"
    header += f"{'MSGS':>7}{'AGE(s)':>8}"
    lines = [header, "-" * len(header)]

    def fmt_spec(value, spec):
        return spec.format(value) if value is not None else "-"

    for ac in aircraft_list:
        row = f"{ac.icao:<7}{ac.callsign:<10}"
        row += f"{fmt_spec(ac.altitude, '{:.0f}'):>9}"
        row += f"{fmt_spec(ac.ground_speed, '{:.0f}'):>8}"
        row += f"{fmt_spec(ac.track, '{:.0f}'):>6}"
        row += f"{fmt_spec(ac.vertical_rate, '{:.0f}'):>9}"
        row += f"{fmt_spec(ac.lat, '{:.3f}'):>10}"
        row += f"{fmt_spec(ac.lon, '{:.3f}'):>11}"
        if ref_set:
            row += f"{fmt_spec(ac.distance_nm, '{:.1f}'):>10}"
        row += f"{ac.message_count:>7}"
        row += f"{ac.to_dict()['age']:>8}"
        lines.append(row)

    return "\n".join(lines)


def run_console(table, interval, count, output):
    if output:
        ensure_csv_header(output)
        print(f"Logging aircraft snapshots to {output}")

    print("Tracking ADS-B traffic. Press Ctrl+C to stop.\n")
    cycles = 0
    try:
        while count == 0 or cycles < count:
            aircraft_list = table.snapshot()
            os.system("clear" if os.name != "nt" else "cls")
            print(f"Tracked aircraft: {len(aircraft_list)}  (updated every {interval}s)\n")
            print(format_table(aircraft_list, table.ref_lat is not None))
            if output:
                append_csv_rows(output, aircraft_list)
            cycles += 1
            if count and cycles >= count:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped tracking.")


GUI_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ADS-B Tracker</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body { font-family: Arial, sans-serif; margin: 0; }
    #header { padding: 12px 20px; }
    h1 { margin: 0 0 4px 0; font-size: 20px; }
    .status { color: #555; }
    #map { height: calc(100vh - 64px); width: 100%; }
    .plane-icon { font-size: 18px; color: #1a73e8; }
    .home-icon { font-size: 18px; color: #d93025; }
  </style>
</head>
<body>
  <div id="header">
    <h1>ADS-B Tracker</h1>
    <div class="status" id="status">Starting...</div>
  </div>
  <div id="map"></div>
  <script>
    var refLat = REF_LAT;
    var refLon = REF_LON;
    var map = L.map('map').setView([refLat !== null ? refLat : 20, refLon !== null ? refLon : 0], refLat !== null ? 9 : 2);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    if (refLat !== null) {
      L.marker([refLat, refLon], {
        icon: L.divIcon({ className: '', html: '<div class="home-icon">&#8982;</div>', iconSize: [20, 20] })
      }).addTo(map).bindPopup('Receiver');
    }

    var markers = {};

    function planeIcon(track) {
      var angle = (track === null || track === undefined) ? 0 : track;
      return L.divIcon({
        className: '',
        html: '<div class="plane-icon" style="transform: rotate(' + angle + 'deg);">&#10148;</div>',
        iconSize: [20, 20],
        iconAnchor: [10, 10],
      });
    }

    async function refresh() {
      try {
        const response = await fetch('/data');
        const data = await response.json();
        document.getElementById('status').textContent =
          data.aircraft.length + ' aircraft tracked';

        var seen = {};
        data.aircraft.forEach(function (ac) {
          seen[ac.icao] = true;
          if (ac.lat === null || ac.lon === null) return;

          var popup = '<b>' + (ac.callsign || ac.icao) + '</b><br>' +
            'ICAO: ' + ac.icao + '<br>' +
            'Altitude: ' + (ac.altitude !== null ? ac.altitude + ' ft' : '-') + '<br>' +
            'Speed: ' + (ac.ground_speed !== null ? ac.ground_speed + ' kt' : '-') + '<br>' +
            'Track: ' + (ac.track !== null ? ac.track + '°' : '-') + '<br>' +
            'Vertical rate: ' + (ac.vertical_rate !== null ? ac.vertical_rate + ' fpm' : '-') + '<br>' +
            (ac.distance_nm !== null ? 'Distance: ' + ac.distance_nm + ' nm<br>' : '');

          if (markers[ac.icao]) {
            markers[ac.icao].setLatLng([ac.lat, ac.lon]);
            markers[ac.icao].setIcon(planeIcon(ac.track));
            markers[ac.icao].getPopup().setContent(popup);
          } else {
            markers[ac.icao] = L.marker([ac.lat, ac.lon], { icon: planeIcon(ac.track) })
              .addTo(map)
              .bindPopup(popup);
          }
        });

        Object.keys(markers).forEach(function (icao) {
          if (!seen[icao]) {
            map.removeLayer(markers[icao]);
            delete markers[icao];
          }
        });
      } catch (err) {
        document.getElementById('status').textContent = 'Error fetching data.';
      }
    }
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>
"""


def run_gui(table, interval, count, output):
    if output:
        ensure_csv_header(output)

    html_page = GUI_HTML.replace("REF_LAT", json.dumps(table.ref_lat)).replace(
        "REF_LON", json.dumps(table.ref_lon)
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(html_page.encode("utf-8"))
            elif self.path == "/data":
                aircraft_list = table.snapshot()
                if output:
                    append_csv_rows(output, aircraft_list)
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                payload = {"aircraft": [ac.to_dict() for ac in aircraft_list]}
                self.wfile.write(json.dumps(payload).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            return

    with HTTPServer(("localhost", 0), Handler) as server:
        port = server.server_port
        url = f"http://localhost:{port}"
        print(f"Opening browser GUI at {url}")
        print("Press Ctrl+C to stop.")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped tracking.")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Track ADS-B aircraft using a FlightAware ProStick (RTL-SDR) receiver."
    )
    parser.add_argument("--lat", type=float, help="Receiver latitude, for position decoding and distance.")
    parser.add_argument("--lon", type=float, help="Receiver longitude, for position decoding and distance.")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="Seconds between table/log refreshes (default: 2.0)."
    )
    parser.add_argument(
        "--count", type=int, default=0, help="Number of refresh cycles before exiting; 0 means run forever."
    )
    parser.add_argument("--output", type=str, help="Optional CSV file path to log aircraft snapshots.")
    parser.add_argument("--gui", action="store_true", help="Launch a browser-based live map instead of the console table.")
    parser.add_argument(
        "--gain", type=str, default="auto", help="Tuner gain in dB, or 'auto' for AGC (default: auto)."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if (args.lat is None) != (args.lon is None):
        raise ValueError("--lat and --lon must be provided together.")
    if args.interval <= 0:
        raise ValueError("Interval must be greater than zero.")

    gain = args.gain
    if gain != "auto":
        gain = float(gain)

    table = AircraftTable(ref_lat=args.lat, ref_lon=args.lon)
    if args.lat is None:
        print("No --lat/--lon given: positions will decode only when both an even and odd frame "
              "arrive for the same aircraft (slower to populate).")

    receiver, thread = start_receiver(table, gain)
    try:
        if args.gui:
            run_gui(table, args.interval, args.count, args.output)
        else:
            run_console(table, args.interval, args.count, args.output)
    finally:
        receiver.stop()


if __name__ == "__main__":
    main()
