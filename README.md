# 🔒 Zeitschloss

**Lock away your own Screen Time passcode – behind real math and real time.**

You know the problem: you set an app limit, and two seconds later you tap *Ignore Limit*.
Zeitschloss ("time lock" in German) generates a passcode you never get to keep. To see it again, your
computer has to grind through billions of calculations that **can't be skipped, sped up much, or done in parallel**.
You choose how long: 6 hours, a day, a week.

## How it works

1. **Get ready.** On your iPhone open *Settings → Screen Time → Lock Screen Time Settings*.
2. **3 · 2 · 1 – your code appears for 15 seconds.** Type it in twice. When your iPhone asks for an
   Apple Account for passcode recovery, tap **Cancel** – otherwise you could just reset the code.
3. **Wipe it from your head.** The app makes you type 8 look-alike codes. This uses
   *retroactive interference*: similar memories overwrite each other, and the real one fades.
4. **Choose the duration** and press **Lock it**. The code is sealed and the app starts unlocking it.
5. **Wait.** A countdown, a progress bar and (optional) roasts that pop up all over your screen.
   **No save points** – close the app or shut down the PC and the countdown starts over.

## Install

```bash
pip install gmpy2
python zeitschloss.py
```

Needs Python 3.9+ with tkinter (included on Windows and macOS).

## Why it's hard to break

The code is sealed with a **time-lock puzzle** (Rivest, Shamir & Wagner, 1996):

- The app picks two random 1024-bit primes `p, q`, sets `n = p·q`, and computes `a^(2^T) mod n`.
  With `p` and `q` this takes milliseconds. **Then `p` and `q` are thrown away.**
- Without them, the only known way is to square `a` one step after another, `T` times.
  Each step needs the previous result – **more CPU cores don't help.**
- `T` is calibrated with [GMP](https://gmplib.org/) (via `gmpy2`), the fastest practical big-number library,
  using its fastest method. So a smarter script won't save you hours.
- The countdown also follows an **atomic clock** (PTB Braunschweig via NTP), so changing your system clock does nothing.

### Honest limits

- A **faster computer** solves it faster. The lock is calibrated on *your* machine – a gaming PC
  twice as fast halves the time.
- The puzzle is only as strong as your phone setup: if you kept passcode recovery via Apple Account,
  you can reset the code without this app.
- If you write the code down, no math in the world can help you.

## Files

- `zeitschloss.py` – the whole app
- `~/.zeitschloss/lock.json` – your sealed lock (no secret inside that helps you)

## License

MIT
