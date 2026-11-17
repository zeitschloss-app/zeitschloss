"""
ZEITSCHLOSS – lock away your own Screen Time passcode behind real math and real time.

How it works
  1. The app generates a random 4-digit passcode and shows it for 30 seconds.
     You type it into your phone's Screen Time settings. Then it is gone.
  2. The code is sealed in a time-lock puzzle (Rivest–Shamir–Wagner, 1996):
     getting it back needs billions of squarings that cannot be parallelised
     or skipped. The shortcut (the prime factors) is destroyed right away.
  3. The app solves the puzzle in front of you. When the time is over, you see the code.
     No save points: close the app and the countdown starts over.

Requires: Python 3.9+ with tkinter, and gmpy2 (pip install gmpy2).
"""
import hashlib
import json
import multiprocessing as mp
import random
import secrets
import socket
import struct
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

try:
    import gmpy2
except ImportError:  # the lock is calibrated with the fastest known method, so gmpy2 is required
    gmpy2 = None

APP = "ZEITSCHLOSS"
LOCK_FILE = Path.home() / ".zeitschloss" / "lock.json"
SHOW_SECONDS = 15
CHUNK = 40_000  # squarings per step, about 0.1 s
ATOMIC_CLOCKS = ["ptbtime1.ptb.de", "ptbtime2.ptb.de", "time.google.com", "pool.ntp.org"]

INSTRUCTIONS = (
    "On your iPhone, open Settings → Screen Time → Lock Screen Time Settings "
    "and get ready to type a new passcode, then press the button below.\n\n"
    "When your iPhone asks for an Apple Account for passcode recovery, tap Cancel – "
    "otherwise you could simply reset the code and the lock would be useless."
)

ROASTS = [
    # Drafted together with the community – edit freely.
    "Put the phone down, you pathetic little dopamine junkie.",
    "Still here? Your willpower died years ago and nobody went to the funeral.",
    "TikTok does not miss you. Nobody there knows your name. Nobody ever will.",
    "Your battery has more self-control than you. And it's at 12%.",
    "You are a lab rat pressing a button for sugar. Congrats.",
    "Go outside. Or are you scared the sun will expose you as a failure?",
    "The algorithm owns you. You're not a user, you're the product.",
    "Your future self hates you. Honestly? Fair.",
    "Read a book, you useless clown.",
    "Homework done? Of course not. Loser behaviour.",
    "Other people build companies. You build excuses.",
    "You have the attention span of a goldfish with a concussion.",
    "Scrolling is not a personality. You don't have one anyway.",
    "Even your phone is embarrassed to be held by you.",
    "You traded your dreams for 15-second videos. Great deal, genius.",
    "Every minute you wait here is a minute you'd have wasted anyway.",
    "Your brain is melting and you're watching it happen in 9:16.",
    "Discipline is free. You still can't afford it.",
    "Somewhere, someone with half your talent is outworking you right now.",
    "You locked yourself out because you can't be trusted. Let that sink in.",
]

BG, FG, DIM, ACCENT, RED = "#0d0d0f", "#f2f2f2", "#8a8a8a", "#ffcc00", "#e5383b"


# ---------------------------------------------------------------- time-lock math
def random_code():
    """4 random digits without easy patterns (no repeats, no neighbours like 3-4, no years)."""
    while True:
        d = [secrets.randbelow(10) for _ in range(4)]
        s = "".join(map(str, d))
        if len(set(d)) < 4 or s.startswith(("19", "20")):
            continue
        if any(abs(d[i + 1] - d[i]) == 1 for i in range(3)):
            continue
        return s


def decoys(code, count=8):
    """Codes that look like the real one – used to overwrite your memory."""
    out, digits = set(), list(code)
    while len(out) < count:
        d = digits[:]
        if random.random() < 0.5:
            i, j = random.sample(range(4), 2)
            d[i], d[j] = d[j], d[i]
        else:
            d[random.randrange(4)] = str(random.randrange(10))
        s = "".join(d)
        if s != code:
            out.add(s)
    return list(out)


def benchmark(seconds=3.0):
    """Squarings per second with the fastest method we know (GMP, Montgomery inside powmod)."""
    n = gmpy2.mpz(secrets.randbits(2048) | 1)
    x = gmpy2.mpz(secrets.randbelow(int(n)))
    steps, start = 0, time.perf_counter()
    while time.perf_counter() - start < seconds:
        x = gmpy2.powmod(x, gmpy2.mpz(1) << CHUNK, n)
        steps += CHUNK
    return steps / (time.perf_counter() - start)


def create_lock(code, hours, rate, settings):
    p = gmpy2.next_prime(gmpy2.mpz(secrets.randbits(1024) | (1 << 1023)))
    q = gmpy2.next_prime(gmpy2.mpz(secrets.randbits(1024) | (1 << 1023)))
    n, phi = p * q, (p - 1) * (q - 1)
    T = int(hours * 3600 * rate)
    a = gmpy2.mpz(secrets.randbelow(int(n) - 3) + 2)
    b = gmpy2.powmod(a, gmpy2.powmod(2, T, phi), n)  # the shortcut – only possible with p and q
    key = hashlib.sha256(str(int(b)).encode()).digest()
    lock = {
        "version": 1,
        "n": str(int(n)), "a": str(int(a)), "T": T, "hours": hours,
        "secret": bytes(x ^ y for x, y in zip(code.encode(), key)).hex(),
        "check": hashlib.sha256(key + b"check").hexdigest()[:16],
        **settings,
    }
    del p, q, phi, b, key  # the shortcut is gone
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(json.dumps(lock, indent=1))
    return lock


def atomic_time():
    """Current time from an atomic clock via NTP. (None, None) when offline."""
    for server in ATOMIC_CLOCKS:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3)
                s.sendto(b"\x1b" + 47 * b"\0", (server, 123))
                data = s.recv(48)
            sec, frac = struct.unpack("!II", data[40:48])
            return sec - 2208988800 + frac / 2**32, server
        except OSError:
            continue
    return None, None


def _solve(n, a, T, progress, out):
    """Runs in its own process, so the math can never freeze the window."""
    n, x, i = gmpy2.mpz(int(n)), gmpy2.mpz(int(a)), 0  # no save points: always from zero
    while i < T:
        k = min(CHUNK, T - i)
        x = gmpy2.powmod(x, gmpy2.mpz(1) << k, n)
        i += k
        progress.value = i
    out.put(str(int(x)))


class Solver:
    def __init__(self, lock):
        self.lock, self.T, self.code = lock, lock["T"], None
        self.progress, self.out = mp.Value("Q", 0, lock=False), mp.Queue()
        self.proc = mp.Process(target=_solve, args=(lock["n"], lock["a"], self.T, self.progress, self.out), daemon=True)
        self.t0 = time.monotonic()

    def start(self):
        self.proc.start()

    def stop(self):
        if self.proc.is_alive():
            self.proc.terminate()

    @property
    def i(self):
        return self.progress.value

    def rate(self):
        dt = time.monotonic() - self.t0
        return self.i / dt if dt > 5 and self.i else None

    def poll(self):
        if self.code is None and not self.out.empty():
            key = hashlib.sha256(self.out.get().encode()).digest()
            if hashlib.sha256(key + b"check").hexdigest()[:16] == self.lock["check"]:
                self.code = bytes(a ^ b for a, b in zip(bytes.fromhex(self.lock["secret"]), key)).decode()
            else:
                self.code = "ERROR"
        return self.code


# ---------------------------------------------------------------- UI
class App:
    def __init__(self, root):
        self.root = root
        root.title(APP)
        root.configure(bg=BG)
        root.geometry("680x500")
        root.resizable(False, False)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Z.Horizontal.TProgressbar", troughcolor="#222", background=ACCENT,
                        bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)
        self.frame = None
        self.code = None
        if gmpy2 is None:
            self.page_error("gmpy2 is missing.\n\nInstall it with:\n\npip install gmpy2")
        elif LOCK_FILE.exists():
            self.page_lock(json.loads(LOCK_FILE.read_text()))
        else:
            self.page_intro()

    # helpers
    def new_page(self):
        if self.frame:
            self.frame.destroy()
        self.frame = tk.Frame(self.root, bg=BG)
        self.frame.pack(fill="both", expand=True, padx=36, pady=24)
        tk.Label(self.frame, text="🔒  " + APP, font=("Segoe UI", 22, "bold"), fg=ACCENT, bg=BG).pack(pady=(0, 14))
        return self.frame

    def text(self, parent, s, size=11, color=FG, **kw):
        lbl = tk.Label(parent, text=s, font=("Segoe UI", size), fg=color, bg=BG, wraplength=600, justify="center", **kw)
        lbl.pack(pady=6)
        return lbl

    def button(self, parent, s, cmd, primary=True):
        b = tk.Button(parent, text=s, command=cmd, font=("Segoe UI", 12, "bold"), relief="flat", cursor="hand2",
                      bg=ACCENT if primary else "#2a2a2e", fg="#111" if primary else FG,
                      activebackground="#ffe066", padx=22, pady=8, bd=0)
        b.pack(pady=8)
        return b

    def page_error(self, msg):
        f = self.new_page()
        self.text(f, msg, 13, RED)

    # 1 – instructions
    def page_intro(self):
        f = self.new_page()
        self.text(f, INSTRUCTIONS, 12)
        self.text(f, f"You will see your new code for {SHOW_SECONDS} seconds. Type it in twice. Don't write it down.", 10, DIM)
        self.button(f, "Generate code", self.page_reveal)

    # 2 – 3·2·1, code for 30 s, gone
    def page_reveal(self):
        self.code = random_code()
        f = self.new_page()
        big = tk.Label(f, text="3", font=("Consolas", 96, "bold"), fg=FG, bg=BG)
        big.pack(pady=20)
        hint = self.text(f, "Get ready…", 12, DIM)
        bar = ttk.Progressbar(f, length=480, maximum=SHOW_SECONDS * 10, style="Z.Horizontal.TProgressbar")

        def count(n):
            if n > 0:
                big.config(text=str(n))
                self.root.after(1000, count, n - 1)
            else:
                big.config(text=self.code, fg=ACCENT)
                hint.config(text="Type it into your iPhone now – twice.")
                bar.pack(pady=10)
                show(SHOW_SECONDS * 10)

        def show(left):
            if not big.winfo_exists():
                return
            bar["value"] = left
            if left > 0:
                self.root.after(100, show, left - 1)
            else:
                big.config(text="••••", fg=DIM)
                hint.config(text="Gone. Did you set it on your iPhone?")
                bar.pack_forget()
                self.button(f, "Yes, it's set", self.page_scramble)
                self.button(f, "New code (this one is lost forever)", self.page_reveal, primary=False)

        count(3)

    # 3 – overwrite memory with look-alike codes (retroactive interference)
    def page_scramble(self):
        f = self.new_page()
        self.text(f, "Now let's wipe it from your head.", 14)
        self.text(f, "Type each of these codes. They look like yours on purpose – "
                     "your brain mixes them up and the real one fades.", 11, DIM)
        queue = decoys(self.code, 8)
        shown = tk.Label(f, text="", font=("Consolas", 64, "bold"), fg=FG, bg=BG)
        shown.pack(pady=8)
        entry = tk.Entry(f, font=("Consolas", 28), width=6, justify="center", bg="#1d1d21", fg=FG,
                         insertbackground=FG, relief="flat")
        entry.pack(pady=6)
        status = self.text(f, "", 10, DIM)

        def next_code():
            if not queue:
                return self.page_settings()
            shown.config(text=queue[-1])
            status.config(text=f"{len(queue)} left")
            entry.delete(0, "end")
            entry.focus_set()

        def check(_=None):
            if entry.get().strip() == queue[-1]:
                queue.pop()
                next_code()
            else:
                entry.delete(0, "end")

        entry.bind("<Return>", check)
        entry.bind("<KeyRelease>", lambda e: len(entry.get().strip()) == 4 and check())
        next_code()

    # 4 – settings
    def page_settings(self):
        f = self.new_page()
        self.text(f, "How long should the lock last?", 14)
        hours = tk.IntVar(value=24)
        row = tk.Frame(f, bg=BG)
        row.pack(pady=6)
        for h, label in [(6, "6 h"), (12, "12 h"), (24, "24 h"), (48, "2 days"), (72, "3 days"), (168, "1 week")]:
            tk.Radiobutton(row, text=label, variable=hours, value=h, indicatoron=False, font=("Segoe UI", 11),
                           bg="#2a2a2e", fg=FG, selectcolor="#5a4a00", activebackground="#3a3a3e",
                           relief="flat", padx=12, pady=6, bd=0).pack(side="left", padx=4)
        roasts = tk.BooleanVar(value=True)
        popups = tk.BooleanVar(value=True)
        for var, label in [(roasts, "Roast me while I wait"), (popups, "Pop-up roasts all over the screen")]:
            tk.Checkbutton(f, text=label, variable=var, font=("Segoe UI", 11), bg=BG, fg=FG,
                           selectcolor="#1d1d21", activebackground=BG, activeforeground=FG).pack(anchor="center", pady=2)
        self.text(f, "No save points: if you close the app or the PC turns off, the countdown starts over.", 10, DIM)
        status = self.text(f, "", 10, ACCENT)

        def lock_it():
            btn.config(state="disabled")
            status.config(text="Measuring your computer's speed and sealing the code…")
            settings = {"roasts": roasts.get(), "popups": popups.get()}
            h = hours.get()
            n = gmpy2.mpz(secrets.randbits(2048) | 1)
            st = {"x": gmpy2.mpz(secrets.randbelow(int(n))), "steps": 0, "busy": 0.0}

            def measure():  # small steps in the UI thread, so the window never freezes
                t = time.perf_counter()
                st["x"] = gmpy2.powmod(st["x"], gmpy2.mpz(1) << CHUNK, n)
                st["busy"] += time.perf_counter() - t  # pure math time = the fastest possible rate
                st["steps"] += CHUNK
                if st["busy"] < 3:
                    return self.root.after(1, measure)
                status.config(text="Sealing…")
                self.root.after(50, lambda: seal(st["steps"] / st["busy"]))

            def seal(rate):
                lock = create_lock(self.code, h, rate, settings)
                self.code = None  # forget it
                self.page_lock(lock)

            self.root.after(50, measure)

        btn = self.button(f, "Lock it", lock_it)

    # 5 – the lock
    def page_lock(self, lock):
        f = self.new_page()
        self.lock, self.done = lock, False
        self.text(f, "Your code is being unlocked. This takes time. That's the point.", 11, DIM)
        self.clock = tk.Label(f, text="--:--:--", font=("Consolas", 64, "bold"), fg=FG, bg=BG)
        self.clock.pack(pady=(10, 6))
        self.bar = ttk.Progressbar(f, length=560, maximum=1000, style="Z.Horizontal.TProgressbar")
        self.bar.pack()
        self.pct = self.text(f, "", 10, DIM)
        self.roast = self.text(f, random.choice(ROASTS) if lock.get("roasts") else "", 13, RED)
        self.roast.config(font=("Segoe UI", 13, "italic"))
        self.source = tk.Label(f, text="", font=("Segoe UI", 8), fg="#5a5a5a", bg=BG)
        self.source.pack(side="bottom")

        self.duration = lock["hours"] * 3600
        self.start, self.correction, self.atom_start = time.monotonic(), 0.0, None
        self.clock_source = "time: internal clock"
        threading.Thread(target=self.sync_atomic, daemon=True).start()

        self.solver = Solver(lock)
        self.solver.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.tick()
        if lock.get("roasts"):
            self.root.after(12_000, self.next_roast)
        if lock.get("popups"):
            self.root.after(random.randint(15, 40) * 1000, self.popup)

    def sync_atomic(self):
        while not self.done:
            mono = time.monotonic()
            t, server = atomic_time()
            if t is not None:
                if self.atom_start is None:
                    self.atom_start = t - (mono - self.start)
                else:
                    self.correction = (t - self.atom_start) - (mono - self.start)
                self.clock_source = f"time: atomic clock {server}"
            time.sleep(300)

    def elapsed(self):
        return time.monotonic() - self.start + self.correction

    def tick(self):
        s = self.solver
        if s.poll() and self.elapsed() >= self.duration:
            return self.unlocked(s.code)
        left = self.duration - self.elapsed()
        rate = s.rate()
        if rate:
            left = max(left, (s.T - s.i) / rate)  # never show 0 before the math is done
        left = max(0, int(left))
        self.clock.config(text=f"{left // 3600:02d}:{left % 3600 // 60:02d}:{left % 60:02d}")
        self.bar["value"] = s.i / s.T * 1000
        self.pct.config(text=f"{s.i / s.T:.3%}   ·   {s.i:,} of {s.T:,} squarings")
        self.source.config(text=self.clock_source)
        self.root.after(200, self.tick)

    def next_roast(self):
        if not self.done:
            self.roast.config(text=random.choice(ROASTS))
            self.root.after(12_000, self.next_roast)

    def popup(self):
        if self.done:
            return
        p = tk.Toplevel(self.root)
        p.overrideredirect(True)
        p.attributes("-topmost", True)
        p.configure(bg=RED)
        tk.Label(p, text=random.choice(ROASTS), font=("Segoe UI", 14, "bold"), fg="white", bg=RED,
                 padx=22, pady=16, wraplength=380).pack()
        p.update_idletasks()
        sw, sh = p.winfo_screenwidth(), p.winfo_screenheight()
        p.geometry(f"+{random.randint(40, max(41, sw - p.winfo_width() - 40))}"
                   f"+{random.randint(40, max(41, sh - p.winfo_height() - 80))}")
        p.bind("<Button-1>", lambda e: p.destroy())
        self.root.bell()
        p.after(5000, lambda: p.winfo_exists() and p.destroy())
        self.root.after(random.randint(30, 90) * 1000, self.popup)

    def unlocked(self, code):
        self.done = True
        f = self.new_page()
        self.text(f, "Time's up. Here is your code:", 13)
        tk.Label(f, text=code, font=("Consolas", 96, "bold"), fg=ACCENT, bg=BG).pack(pady=16)
        self.text(f, "Hope it was worth the wait, champ.", 11, DIM)

        def reset():
            LOCK_FILE.unlink(missing_ok=True)
            self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
            self.page_intro()

        self.button(f, "Done – start a new lock", reset)

    def close(self):
        self.solver.stop()
        self.done = True
        self.root.destroy()


if __name__ == "__main__":
    mp.freeze_support()
    root = tk.Tk()
    App(root)
    root.attributes("-topmost", True)  # come to the front on start …
    root.after(800, lambda: root.attributes("-topmost", False))  # … but don't stay on top
    root.focus_force()
    root.mainloop()
