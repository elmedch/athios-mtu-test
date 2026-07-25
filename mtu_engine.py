"""
mtu_engine.py
Core MTU discovery engine for ATHIOS MTU Test.

Pure logic, no GUI/tkinter code here on purpose - this module can be
imported and exercised on its own (see the smoke test at the bottom).

Algorithm
---------
1. Binary-search the payload size in [PAYLOAD_MIN, PAYLOAD_MAX] using
   `ping -f -l <size> -n <count> www.google.com`. "-f" sets the Don't
   Fragment bit, so a payload that is too large for the path MTU comes
   back with "Packet needs to be fragmented but DF set" instead of a
   normal reply.
2. Among the successful sizes found during the search, take the ones
   closest to the discovered maximum and compare their average latency.
   The lowest-latency one becomes the preliminary "Best MTU" candidate
   (not necessarily the single largest size).
3. Re-run those candidate(s) a few more times to confirm the result is
   stable before reporting it.
4. Real MTU = payload size + 28 (20-byte IP header + 8-byte ICMP header).
"""

from __future__ import annotations

import math
import os
import re
import statistics
import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
HOST = "www.google.com"
PAYLOAD_MIN = 1300
PAYLOAD_MAX = 1500
IP_ICMP_OVERHEAD = 28           # 20-byte IP header + 8-byte ICMP header
ECHO_COUNT = 4                  # pings per test  (-n 4)
CONFIRMATION_ROUNDS = 3         # extra re-test rounds run on the final candidate(s)
CANDIDATE_COUNT = 3             # how many of the largest successful sizes to compare on latency
TIMEOUT_RETRIES = 2             # retries for an ambiguous "Request timed out" before giving up
PING_SUBPROCESS_TIMEOUT = 20    # hard ceiling (seconds) for a single ping call to return

ESTIMATED_SEARCH_STEPS = math.ceil(math.log2(PAYLOAD_MAX - PAYLOAD_MIN + 1))


class PingStatus(Enum):
    SUCCESS = auto()               # 0% loss, DF not triggered
    PARTIAL = auto()               # some loss but at least one reply, DF not triggered
    FRAGMENTATION_NEEDED = auto()  # DF bit forced a rejection - payload too large
    TIMEOUT = auto()               # 100% loss, no fragmentation message (ambiguous)
    NETWORK_ERROR = auto()         # DNS failure / no route - not an MTU problem at all


@dataclass
class PingResult:
    status: PingStatus
    avg_rtt_ms: Optional[float] = None
    loss_pct: Optional[int] = None
    message: str = ""
    raw_output: str = ""

    @property
    def is_pass(self) -> bool:
        """Whether this payload size 'gets through' for binary-search purposes."""
        return self.status in (PingStatus.SUCCESS, PingStatus.PARTIAL)


@dataclass
class TestRecord:
    payload: int
    phase: str          # "search" | "confirm"
    result: PingResult


@dataclass
class MtuResult:
    payload: int
    mtu: int
    avg_latency_ms: float
    largest_success: int


class NetworkError(Exception):
    """Raised when the problem is connectivity/DNS, not a fragmentation limit."""


class MtuEngine:
    """
    Runs the full binary-search + confirmation MTU discovery flow.

    `on_step` is invoked after every individual ping test with a TestRecord
    so a caller can show live progress. It is called from whichever thread
    `run()` executes on - a GUI caller is responsible for marshalling it
    back onto the UI thread (tkinter is not thread-safe).
    """

    FRAG_RE = re.compile(r"needs to be fragmented", re.IGNORECASE)
    UNREACHABLE_RE = re.compile(
        r"could not find host|transmit failed|general failure|"
        r"destination host unreachable|network is unreachable",
        re.IGNORECASE,
    )
    AVG_RE = re.compile(r"Average\s*=\s*(\d+)\s*ms", re.IGNORECASE)
    LOSS_RE = re.compile(r"\((\d{1,3})%\s*loss\)", re.IGNORECASE)

    def __init__(self, on_step: Optional[Callable[[TestRecord], None]] = None):
        self.on_step = on_step
        self.history: List[TestRecord] = []

    # ---- low level ---------------------------------------------------

    def _run_ping(self, payload: int) -> PingResult:
        cmd = ["ping", "-f", "-l", str(payload), "-n", str(ECHO_COUNT), HOST]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=PING_SUBPROCESS_TIMEOUT,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired:
            return PingResult(PingStatus.TIMEOUT, message="ping command timed out")
        except FileNotFoundError:
            raise NetworkError("The 'ping' command was not found on this system.")

        output = f"{completed.stdout}\n{completed.stderr}"
        return self._parse(output)

    def _parse(self, output: str) -> PingResult:
        if self.FRAG_RE.search(output):
            return PingResult(PingStatus.FRAGMENTATION_NEEDED, raw_output=output)

        if self.UNREACHABLE_RE.search(output):
            return PingResult(
                PingStatus.NETWORK_ERROR,
                message="Host unreachable or DNS lookup failed. Check your internet connection.",
                raw_output=output,
            )

        avg_match = self.AVG_RE.search(output)
        loss_match = self.LOSS_RE.search(output)
        loss_pct = int(loss_match.group(1)) if loss_match else 100

        if avg_match and loss_pct == 0:
            return PingResult(
                PingStatus.SUCCESS,
                avg_rtt_ms=float(avg_match.group(1)),
                loss_pct=0,
                raw_output=output,
            )
        if avg_match and loss_pct < 100:
            return PingResult(
                PingStatus.PARTIAL,
                avg_rtt_ms=float(avg_match.group(1)),
                loss_pct=loss_pct,
                raw_output=output,
            )
        return PingResult(PingStatus.TIMEOUT, loss_pct=loss_pct, raw_output=output)

    def _test_payload(self, payload: int, phase: str) -> PingResult:
        """Ping once, transparently retrying ambiguous timeouts, and emit progress."""
        attempts = 0
        result: Optional[PingResult] = None
        while attempts <= TIMEOUT_RETRIES:
            result = self._run_ping(payload)
            if result.status == PingStatus.NETWORK_ERROR:
                raise NetworkError(result.message)
            if result.status != PingStatus.TIMEOUT:
                break
            attempts += 1
        record = TestRecord(payload=payload, phase=phase, result=result)
        self.history.append(record)
        if self.on_step:
            self.on_step(record)
        return result

    # ---- phases --------------------------------------------------------

    def _binary_search(self) -> Dict[int, PingResult]:
        lo, hi = PAYLOAD_MIN, PAYLOAD_MAX
        tested: Dict[int, PingResult] = {}
        while lo <= hi:
            mid = (lo + hi) // 2
            result = self._test_payload(mid, phase="search")
            tested[mid] = result
            if result.is_pass:
                lo = mid + 1
            else:
                hi = mid - 1
        return tested

    def _pick_candidates(self, tested: Dict[int, PingResult]) -> List[int]:
        """Largest successful sizes found, closest to the boundary first."""
        successful = sorted((p for p, r in tested.items() if r.is_pass), reverse=True)
        return successful[:CANDIDATE_COUNT]

    def _confirm(self, candidates: List[int]) -> Dict[int, List[PingResult]]:
        samples: Dict[int, List[PingResult]] = {p: [] for p in candidates}
        for p in candidates:
            for _ in range(CONFIRMATION_ROUNDS):
                r = self._test_payload(p, phase="confirm")
                if r.is_pass:
                    samples[p].append(r)
        return samples

    def run(self) -> MtuResult:
        tested = self._binary_search()
        candidates = self._pick_candidates(tested)

        if not candidates:
            raise NetworkError(
                f"No payload size between {PAYLOAD_MIN} and {PAYLOAD_MAX} bytes got "
                "through without fragmentation. ICMP may be blocked by a firewall, "
                "or your path MTU is unusually small."
            )

        confirmed = self._confirm(candidates)

        # Score each candidate as the mean of every real reading we have for
        # it (the original search-phase reply plus every confirmation round
        # that succeeded), so a single flaky round can't skew the pick.
        scored: Dict[int, float] = {}
        for p in candidates:
            readings = [r.avg_rtt_ms for r in confirmed[p] if r.avg_rtt_ms is not None]
            if tested[p].avg_rtt_ms is not None:
                readings.append(tested[p].avg_rtt_ms)
            if readings:
                scored[p] = statistics.mean(readings)

        if scored:
            best_payload = min(scored, key=scored.get)
            best_latency = scored[best_payload]
        else:
            # every confirmation round flaked - fall back to the largest
            # candidate's original search-phase reading
            best_payload = candidates[0]
            best_latency = tested[best_payload].avg_rtt_ms or 0.0

        return MtuResult(
            payload=best_payload,
            mtu=best_payload + IP_ICMP_OVERHEAD,
            avg_latency_ms=best_latency,
            largest_success=candidates[0],
        )


# ---------------------------------------------------------------------------
# Offline smoke test for the parser + selection logic (no real network calls).
# Run with:  python mtu_engine.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    engine = MtuEngine()

    success_output = """
Pinging www.google.com [142.250.72.4] with 1472 bytes of data:
Reply from 142.250.72.4: bytes=1472 time=14ms TTL=115
Reply from 142.250.72.4: bytes=1472 time=13ms TTL=115
Reply from 142.250.72.4: bytes=1472 time=14ms TTL=115
Reply from 142.250.72.4: bytes=1472 time=13ms TTL=115

Ping statistics for 142.250.72.4:
    Packets: Sent = 4, Received = 4, Lost = 0 (0% loss),
Approximate round trip times in milli-seconds:
    Minimum = 13ms, Maximum = 14ms, Average = 13ms
"""
    frag_output = """
Pinging www.google.com [142.250.72.4] with 1500 bytes of data:
Packet needs to be fragmented but DF set.
Packet needs to be fragmented but DF set.
Packet needs to be fragmented but DF set.
Packet needs to be fragmented but DF set.

Ping statistics for 142.250.72.4:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""
    dns_fail_output = "Ping request could not find host www.google.com. Please check the name and try again.\n"

    timeout_output = """
Pinging www.google.com [142.250.72.4] with 1400 bytes of data:
Request timed out.
Request timed out.
Request timed out.
Request timed out.

Ping statistics for 142.250.72.4:
    Packets: Sent = 4, Received = 0, Lost = 4 (100% loss),
"""

    r1 = engine._parse(success_output)
    assert r1.status == PingStatus.SUCCESS and r1.avg_rtt_ms == 13.0, r1
    r2 = engine._parse(frag_output)
    assert r2.status == PingStatus.FRAGMENTATION_NEEDED, r2
    r3 = engine._parse(dns_fail_output)
    assert r3.status == PingStatus.NETWORK_ERROR, r3
    r4 = engine._parse(timeout_output)
    assert r4.status == PingStatus.TIMEOUT, r4

    print("All parser smoke tests passed.")
    print(f"Estimated binary-search steps for range [{PAYLOAD_MIN},{PAYLOAD_MAX}]: {ESTIMATED_SEARCH_STEPS}")
