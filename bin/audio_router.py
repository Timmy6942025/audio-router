#!/usr/bin/env python3
"""Audio Router: splits system audio into full-range + bass-only outputs."""

import numpy as np
import sounddevice as sd
from scipy.signal import butter, lfilter
import subprocess
import argparse
import sys
import os
import json
import time
import fcntl
import threading
import queue


DEFAULT_BASS_CUTOFF = 80
DEFAULT_DELAY_MS = 150
DEFAULT_SAMPLE_RATE = 48000
BLOCK_SIZE = 1024
AUDIOTEE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiotee")
CONFIG_DIR = os.path.expanduser("~/.audio-router")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _read_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


class RingBuffer:
    def __init__(self, capacity, channels=2):
        self.buffer = np.zeros((capacity, channels), dtype=np.float32)
        self.capacity = capacity
        self.write_pos = 0
        self.read_pos = 0
        self._lock = threading.Lock()

    def write(self, data):
        n = data.shape[0]
        with self._lock:
            for i in range(n):
                self.buffer[(self.write_pos + i) % self.capacity] = data[i]
            self.write_pos = (self.write_pos + n) % self.capacity

    def read(self, count):
        out = np.zeros((count, 2), dtype=np.float32)
        with self._lock:
            for i in range(count):
                out[i] = self.buffer[(self.read_pos + i) % self.capacity]
            self.read_pos = (self.read_pos + count) % self.capacity
        return out

    def read_delayed(self, count, delay_samples):
        out = np.zeros((count, 2), dtype=np.float32)
        with self._lock:
            for i in range(count):
                pos = (self.read_pos - delay_samples + i) % self.capacity
                out[i] = self.buffer[pos]
            self.read_pos = (self.read_pos + count) % self.capacity
        return out


class AudioRouter:
    def __init__(
        self,
        full_output_device,
        bass_output_device,
        sample_rate=DEFAULT_SAMPLE_RATE,
        bass_cutoff=DEFAULT_BASS_CUTOFF,
        delay_ms=DEFAULT_DELAY_MS,
        mute=True,
    ):
        self.sample_rate = sample_rate
        self.bass_cutoff = bass_cutoff
        self.mute = mute
        self.running = False
        self.audiotee_proc = None

        b, a = butter(2, bass_cutoff / (sample_rate / 2), btype="low")
        self.bass_b = b
        self.bass_a = a
        self.bass_zi = np.zeros((2, 2), dtype=np.float32)

        self.delay_samples = max(0, int(delay_ms * sample_rate / 1000))
        self.full_buffer = RingBuffer(sample_rate * 2)
        self.bass_buffer = RingBuffer(sample_rate * 2)

        self.full_output_device = full_output_device
        self.bass_output_device = bass_output_device

        print(f"Full Output:    {self._dname(full_output_device)}")
        print(f"Bass Output:    {self._dname(bass_output_device)}")
        print(f"Bass Cutoff:    {bass_cutoff} Hz")
        print(f"Sync Delay:     {delay_ms} ms")
        print(f"Sample Rate:    {sample_rate} Hz")
        print(f"Mute Tapped:    {mute}")
        print()

    def _dname(self, did):
        try:
            return sd.query_devices(did)["name"]
        except Exception:
            return f"Device {did}"

    def start_audiotee(self):
        cmd = [AUDIOTEE_PATH, "--stereo"]
        if self.mute:
            cmd.append("--mute")
        if self.sample_rate != 48000:
            cmd.extend(["--sample-rate", str(self.sample_rate)])

        self.audiotee_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )

        fd = self.audiotee_proc.stderr.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        for _ in range(20):
            try:
                line = self.audiotee_proc.stderr.readline()
                if line:
                    msg = json.loads(line.decode())
                    if msg.get("message_type") == "metadata":
                        meta = msg.get("data", {})
                        print(
                            f"Capture: {meta.get('sample_rate')}Hz, {meta.get('channels_per_frame')}ch"
                        )
                        return True
                    if msg.get("message_type") == "stream_start":
                        return True
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            time.sleep(0.2)
        return False

    def capture_thread(self):
        while self.running:
            raw = self.audiotee_proc.stdout.read(BLOCK_SIZE * 2 * 4)
            if not raw:
                time.sleep(0.001)
                continue
            audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)

            full, self.bass_zi = lfilter(
                self.bass_b, self.bass_a, audio, axis=0, zi=self.bass_zi
            )
            full_out = full.astype(np.float32)

            self.full_buffer.write(full_out)
            self.bass_buffer.write(audio - full_out)

    def full_callback(self, outdata, frames, time_info, status):
        if status:
            pass
        delay_ms = 150
        try:
            cfg = _read_config()
            delay_ms = cfg.get(
                "delay_ms", self.delay_samples * 1000 // self.sample_rate
            )
        except Exception:
            pass
        delay_samples = max(0, int(delay_ms * self.sample_rate / 1000))
        outdata[:] = self.full_buffer.read_delayed(frames, delay_samples)

    def bass_callback(self, outdata, frames, time_info, status):
        if status:
            pass
        outdata[:] = self.bass_buffer.read(frames)

    def run(self):
        self.running = True

        print("Starting audiotee...")
        if not self.start_audiotee():
            print("Failed to start audiotee")
            return
        print("Running. Ctrl+C to stop.\n")

        capture_t = threading.Thread(target=self.capture_thread, daemon=True)
        capture_t.start()

        full_stream = sd.OutputStream(
            device=self.full_output_device,
            samplerate=self.sample_rate,
            channels=2,
            blocksize=BLOCK_SIZE,
            callback=self.full_callback,
        )
        bass_stream = sd.OutputStream(
            device=self.bass_output_device,
            samplerate=self.sample_rate,
            channels=2,
            blocksize=BLOCK_SIZE,
            callback=self.bass_callback,
        )

        full_stream.start()
        bass_stream.start()

        try:
            while True:
                time.sleep(0.5)
                cfg = _read_config()
                new_delay = max(
                    0, int(cfg.get("delay_ms", 150) * self.sample_rate / 1000)
                )
                if new_delay != self.delay_samples:
                    self.delay_samples = new_delay
                    print(f"Delay updated: {cfg.get('delay_ms', 150)}ms")
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            self.running = False
            full_stream.stop()
            full_stream.close()
            bass_stream.stop()
            bass_stream.close()
            if self.audiotee_proc:
                self.audiotee_proc.terminate()
                try:
                    self.audiotee_proc.wait(timeout=3)
                except Exception:
                    self.audiotee_proc.kill()


def list_devices():
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            rate = int(d["default_samplerate"]) if d["default_samplerate"] else "N/A"
            print(
                f"{i:<4} {d['name'][:30]:<30} out={d['max_output_channels']}  rate={rate}"
            )


def main():
    p = argparse.ArgumentParser(description="Audio Router")
    p.add_argument("--list", action="store_true")
    p.add_argument("--full", type=int)
    p.add_argument("--bass", type=int)
    p.add_argument("--cutoff", type=int, default=DEFAULT_BASS_CUTOFF)
    p.add_argument("--delay", type=int, default=DEFAULT_DELAY_MS)
    p.add_argument("--rate", type=int, default=DEFAULT_SAMPLE_RATE)
    p.add_argument("--no-mute", action="store_true")
    args = p.parse_args()

    if args.list:
        list_devices()
        return

    if args.full is None or args.bass is None:
        p.print_help()
        print("\nError: --full and --bass required.")
        sys.exit(1)

    _ensure_config_dir()
    cfg = _read_config()
    cfg["delay_ms"] = args.delay
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f)

    AudioRouter(
        full_output_device=args.full,
        bass_output_device=args.bass,
        sample_rate=args.rate,
        bass_cutoff=args.cutoff,
        delay_ms=args.delay,
        mute=not args.no_mute,
    ).run()


if __name__ == "__main__":
    main()
