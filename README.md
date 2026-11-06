# Chrono

A desktop clock app with an analog + digital clock, alarms, and a world clock.
Built with Python and Tkinter — no third-party UI dependencies.

## Run

```powershell
python -m pip install -r requirements.txt
python clock_app.py
```

Requires Python 3.10+ (developed on 3.13) with Tkinter.

`tzdata` is only needed on systems without an IANA timezone database
(typical on Windows). On Linux/macOS the system database is used.

## Features

- **Clock** — live analog dial, large digital time, date, UTC offset,
  12/24-hour toggle, and a countdown to the next alarm.
- **Alarms** — add any number of alarms, optional weekday repeat,
  one-time alarms disable themselves after ringing, enable/disable and
  delete from the list, 5-minute snooze, repeating tone with a full-screen
  style "ringing" window. A **Test sound** button checks audio.
- **World Clock** — add cities by name or IANA zone (`Tokyo`, `Asia/Tokyo`),
  live times with local date and UTC offset, remove as needed.

Everything is saved in `clock_data.json` next to `clock_app.py`.

## Tests

```powershell
python -m unittest test_clock_app -v
```

Covers alarm matching (minute, weekday, duplicate suppression, one-shot),
next-occurrence calculation, 12/24-hour formatting, UTC offsets,
timezone resolution, and JSON persistence/validation.

## Notes

- Alarms only ring while the app is running; this is not an OS-level
  scheduler.
- Sound uses `winsound.Beep` on Windows and the Tk bell elsewhere.
- If the IANA database is missing, the world clock falls back to fixed
  standard-time offsets for major cities (DST will not be reflected).
