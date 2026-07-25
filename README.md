<p align="center">
  <img src="main-logo.png" alt="ATHIOS MTU Test logo" width="120"/>
</p>

<h1 align="center">ATHIOS MTU Test</h1>

<p align="center">
  A lightweight Windows desktop app that automatically finds the optimal MTU value for your internet connection.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-blue" alt="Platform: Windows">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
</p>

---

## 🚀 About

**ATHIOS MTU Test** finds the largest MTU your connection can handle without packet fragmentation — and, among the values close to that maximum, the one with the **lowest average latency**.

It works by sending `ping` requests with the **Don't Fragment (DF)** flag set, using a binary search to quickly converge on the best packet size, instead of testing every value one by one.

```

ping www.google.com -f -l <payload_size>

````

- `-f` → Don't Fragment (Windows only)
- If ping replies with *"Packet needs to be fragmented but DF set"*, the payload is too large.
- The real MTU is calculated as:

```

MTU = payload size + 28   (20 bytes IP header + 8 bytes ICMP header)

````

## ✨ Features

- 🔎 **Binary search algorithm** — tests only ~10 values instead of 200+, from a 1300–1500 payload range.
- 📉 **Latency-aware selection** — picks the fastest MTU among the valid candidates, not just the largest.
- 🔁 **Re-test / confirmation pass** — re-runs the top candidates a few times to confirm accuracy before showing the final result.
- 🖥️ **Responsive UI** — testing runs on a background thread, so the interface never freezes.
- 📋 **Copy result** — one click to copy your MTU result.
- 📊 **Test log** — see every tested value, whether it succeeded, and its latency.
- ☕ **Ko-fi support button** built into the UI.


## 📦 Installation

### Option 1 — Download the executable
Grab the latest `.exe` from the [Releases](../../releases) page and run it directly. No installation required.

### Option 2 — Run from source
```bash
git clone https://github.com/<elmedch>/athios-mtu-test.git
cd athios-mtu-test
pip install -r requirements.txt
python main.py
````

## 🛠️ How It Works

1. Binary search runs between payload sizes **1300 and 1500**.
2. Each candidate is pinged 4 times (`-n 4`) with `-f` set.
3. Successful (non-fragmented) values and their average latency are recorded.
4. The search narrows toward the largest working payload size.
5. Once converged, nearby candidates are re-tested a few extra times for accuracy.
6. The app reports the payload size + latency combination with the best overall result, and the corresponding MTU.

## ⚠️ Requirements

* Windows OS (uses `ping -f`, a Windows-only flag)
* Active internet connection
* Python 3.9+ (only if running from source)

## ☕ Support

If this tool saved you some troubleshooting time, consider supporting development:

[![Support me on Ko-fi](https://storage.ko-fi.com/cdn/brandasset/kofi_button_red.png)](https://ko-fi.com/W7W116MME5)

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions, issues, and feature requests are welcome. Feel free to check the [issues page](../../issues).

---

<p align="center">Made with ⚡ by ATHIOS</p>
