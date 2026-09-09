# ADS-B Tracker

A portable, pure-Python ADS-B aircraft tracker for a FlightAware ProStick (or any
RTL2832U/R820T RTL-SDR dongle). It reads raw 1090 MHz IQ samples straight from the
dongle and demodulates ADS-B messages in Python (via [pyModeS](https://github.com/junzis/pyModeS)'s
`RtlReader` + [pyrtlsdr](https://github.com/roger-/pyrtlsdr)) — no dump1090/readsb
binary required.

## Requirements

- Python 3.9+
- A FlightAware ProStick (or other RTL2832U-based RTL-SDR dongle) plugged into a USB port
- `librtlsdr` native driver: `brew install librtlsdr`

## Installation

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/patch_pyrtlsdr.py
```

The patch step is required: `pyrtlsdr` is unmaintained and unconditionally binds a
few GPIO/dithering functions that Homebrew's mainline `librtlsdr` build doesn't
export, which makes `import rtlsdr` (and opening the device) fail. The script
patches the installed package in your venv to skip those bindings when unavailable.
It's idempotent — safe to run again after reinstalling dependencies.

## Usage

Console table, using your receiver's coordinates for position decoding and distance:

```
python adsb_tracker.py --lat 39.9612 --lon -82.9988
```

Without `--lat`/`--lon`, positions still decode, but only once both an even and odd
CPR frame have arrived for an aircraft (slower to populate, no distance column).

Live map in your browser:

```
python adsb_tracker.py --lat 39.9612 --lon -82.9988 --gui
```

Log snapshots to CSV every 5 seconds:

```
python adsb_tracker.py --lat 39.9612 --lon -82.9988 --interval 5 --output flights.csv
```

### Options

- `--lat`, `--lon` — receiver location (decimal degrees). Enables accurate single-message
  position decoding and distance-to-aircraft.
- `--interval SECONDS` — how often the console table/CSV log refreshes (default: 2.0).
- `--count N` — number of refresh cycles before exiting; 0 (default) runs forever.
- `--output FILE` — CSV file to log aircraft snapshots to.
- `--gui` — open a live Leaflet map in your browser instead of the console table.
- `--gain VALUE` — tuner gain in dB, or `auto` (default) for AGC.

Aircraft are dropped from the table/map after 60 seconds without a new message.

## How it works

`pyModeS.extra.rtlreader.RtlReader` streams raw samples from the dongle at 2 Msps,
detects Mode S preambles, and demodulates 112-bit ADS-B frames in Python. Each
decoded message is fed into an in-memory aircraft table keyed by ICAO24 address,
which extracts callsign, altitude, ground speed/track/vertical rate, and position
(via `pyModeS`'s local-reference CPR decode when a receiver location is given, or
even/odd frame pairing otherwise).

## Limitations

- Surface position messages (ground traffic at airports) aren't decoded — only
  airborne position, identification, and velocity.
- Range and message rate depend heavily on antenna placement; a stock ProStick
  antenna indoors will see far fewer aircraft than one with clear sky view.

## Troubleshooting

- **"Could not open the RTL-SDR device"**: check the dongle is plugged in, run
  `python scripts/patch_pyrtlsdr.py` if you haven't, and confirm `librtlsdr` is
  installed (`brew list librtlsdr`).
- **No aircraft appear**: ADS-B needs line-of-sight-ish reception at 1090 MHz;
  try moving the antenna near a window or outdoors, and give it a minute — the
  first identification/position messages can take a few broadcast cycles to arrive.
