#!/usr/bin/env python3
"""Generates openwrt/lua/oui_data.lua from the IEEE MA-L registry.

The topology collector labels client nodes with a vendor name and picks a
device icon from it, which is what turns `unknown_88a29e6eaf43` into
`Raspberry Pi`. Those mappings are *not* written by hand: guessing an OUI
assignment produces a confidently wrong label, which is exactly the
"plausible wrong value" this repository's standing rules forbid. Everything
below is derived from the authoritative registry.

Usage:

    python3 -m scripts.build_oui_table                     # fetch the registry
    python3 -m scripts.build_oui_table --csv path/oui.csv  # use a local copy

Only the MA-L registry (24-bit prefixes) is used. MA-M/MA-S are 28- and
36-bit assignments that share a 24-bit prefix with their block owner, so
matching them would need variable-length lookup for a handful of niche
vendors; a 24-bit-only table never reports the wrong vendor, it only
reports none.

Output encoding is deliberately compact rather than a 12k-entry Lua table
constructor: this file is loaded by a metrics exporter on a router. Records
are fixed width in one string literal (~100 KB, one interned string) instead
of ~12k hash nodes (~850 KB resident).

The generated file is committed, like the generated dashboard JSON.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import urllib.request
from pathlib import Path

OUI_URL = "https://standards-oui.ieee.org/oui/oui.csv"
# Not openwrt/collectors/: the exporter turns every .lua in
# /usr/lib/lua/prometheus-collectors/ into a collector named after the file,
# and this one has no scrape(). Shared modules install to /usr/lib/lua/.
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "openwrt/lua/oui_data.lua"

# Fixed-width record: 6 hex prefix chars + 2 base-36 vendor index chars.
PREFIX_LEN = 6
INDEX_LEN = 2
RECORD_LEN = PREFIX_LEN + INDEX_LEN
MAX_VENDORS = 36**INDEX_LEN

# (registry-name substring, display name, icon). First match wins, so more
# specific patterns must come first. Every icon is verified to exist in the
# bundled Grafana's public/img/icons/unicons set -- an unknown IconName
# renders as nothing at all, which looks worse than the generic default.
RULES: list[tuple[str, str, str]] = [
    # -- virtualisation and single-board computers ------------------------
    ("raspberry pi", "Raspberry Pi", "processor"),
    ("proxmox", "Proxmox", "server"),
    ("vmware", "VMware", "server"),
    ("parallels", "Parallels", "server"),
    ("oracle", "Oracle", "server"),
    ("pcs systemtechnik", "VirtualBox", "server"),
    ("synology", "Synology", "database"),
    ("qnap", "QNAP", "database"),
    ("western digital", "Western Digital", "hdd"),
    ("seagate", "Seagate", "hdd"),
    ("sandisk", "SanDisk", "hdd"),
    ("kingston", "Kingston", "hdd"),
    # -- phones, tablets, computers ---------------------------------------
    ("apple", "Apple", "apple"),
    ("samsung", "Samsung", "mobile-android"),
    ("guangdong oppo", "OPPO", "mobile-android"),
    ("oneplus", "OnePlus", "mobile-android"),
    ("vivo mobile", "vivo", "mobile-android"),
    ("xiaomi", "Xiaomi", "mobile-android"),
    ("honor device", "HONOR", "mobile-android"),
    ("huawei device", "Huawei", "mobile-android"),
    ("huawei", "Huawei", "mobile-android"),
    ("motorola", "Motorola", "mobile-android"),
    ("htc", "HTC", "mobile-android"),
    ("google", "Google", "android"),
    ("microsoft", "Microsoft", "desktop"),
    ("dell", "Dell", "desktop"),
    ("hewlett packard enterprise", "HPE", "server"),
    ("hewlett packard", "HP", "desktop"),
    ("lenovo", "Lenovo", "laptop"),
    ("hp inc", "HP", "desktop"),
    ("asustek", "ASUS", "desktop"),
    ("asrock", "ASRock", "desktop"),
    ("giga-byte", "GIGABYTE", "desktop"),
    ("gigabyte", "GIGABYTE", "desktop"),
    ("micro-star", "MSI", "desktop"),
    ("intel", "Intel", "processor"),
    ("nvidia", "NVIDIA", "processor"),
    ("bitland", "Bitland", "desktop"),
    ("wistron", "Wistron", "laptop"),
    ("quanta", "Quanta", "laptop"),
    ("compal", "Compal", "laptop"),
    ("pegatron", "Pegatron", "desktop"),
    ("hon hai", "Foxconn", "desktop"),
    ("foxconn", "Foxconn", "desktop"),
    ("azurewave", "AzureWave", "wifi"),
    ("liteon", "Lite-On", "laptop"),
    ("universal global", "Universal Global", "wifi"),
    # -- networking gear ---------------------------------------------------
    ("tp-link", "TP-Link", "sitemap"),
    ("tplink", "TP-Link", "sitemap"),
    ("ubiquiti", "Ubiquiti", "sitemap"),
    ("ubnt", "Ubiquiti", "sitemap"),
    ("mikrotik", "MikroTik", "sitemap"),
    ("netgear", "NETGEAR", "sitemap"),
    ("d-link", "D-Link", "sitemap"),
    ("linksys", "Linksys", "sitemap"),
    ("zyxel", "Zyxel", "sitemap"),
    ("draytek", "DrayTek", "sitemap"),
    ("avm gmbh", "AVM FRITZ!", "sitemap"),
    ("sagemcom", "Sagemcom", "cloud"),
    ("technicolor", "Technicolor", "cloud"),
    ("arris", "ARRIS", "cloud"),
    ("netcomm", "NetComm", "cloud"),
    ("vodafone", "Vodafone", "cloud"),
    ("juniper", "Juniper", "sitemap"),
    ("aruba", "Aruba", "sitemap"),
    ("meraki", "Meraki", "sitemap"),
    ("aerohive", "Aerohive", "sitemap"),
    ("extreme networks", "Extreme", "sitemap"),
    ("fortinet", "Fortinet", "shield"),
    ("sophos", "Sophos", "shield"),
    ("cambium", "Cambium", "sitemap"),
    ("engenius", "EnGenius", "sitemap"),
    ("edimax", "Edimax", "sitemap"),
    ("buffalo", "Buffalo", "sitemap"),
    ("belkin", "Belkin", "sitemap"),
    ("eero", "eero", "sitemap"),
    ("ruckus", "Ruckus", "sitemap"),
    ("cisco", "Cisco", "sitemap"),
    ("new h3c", "H3C", "sitemap"),
    ("arista", "Arista", "sitemap"),
    ("palo alto", "Palo Alto", "shield"),
    ("avaya", "Avaya", "microphone"),
    ("nortel", "Nortel", "sitemap"),
    ("mellanox", "Mellanox", "server"),
    ("commscope", "CommScope", "cloud"),
    ("vantiva", "Vantiva", "cloud"),
    ("arcadyan", "Arcadyan", "cloud"),
    ("askey", "Askey", "cloud"),
    ("fiberhome", "FiberHome", "cloud"),
    ("sky uk", "Sky", "cloud"),
    ("nokia", "Nokia", "cloud"),
    ("zte", "ZTE", "cloud"),
    ("tct mobile", "Alcatel", "mobile-android"),
    ("tcl", "TCL", "monitor"),
    # -- smart home, IoT, sensors -----------------------------------------
    ("espressif", "Espressif", "lamp"),
    ("tuya", "Tuya", "lamp"),
    ("itead", "Sonoff", "lamp"),
    ("shelly", "Shelly", "lamp"),
    ("allterco", "Shelly", "lamp"),
    ("lumi", "Aqara", "lamp"),
    ("aqara", "Aqara", "lamp"),
    ("signify", "Philips Hue", "lamp"),
    ("philips", "Philips", "lamp"),
    ("ikea", "IKEA", "lamp"),
    ("nest labs", "Nest", "home"),
    ("ring llc", "Ring", "camera"),
    ("wyze", "Wyze", "camera"),
    ("reolink", "Reolink", "camera"),
    ("hikvision", "Hikvision", "camera"),
    ("dahua", "Dahua", "camera"),
    ("roborock", "Roborock", "home"),
    ("ecovacs", "Ecovacs", "home"),
    ("dyson", "Dyson", "home"),
    ("miele", "Miele", "home"),
    ("electrolux", "Electrolux", "home"),
    ("bosch", "Bosch", "home"),
    ("siemens", "Siemens", "home"),
    ("tesla", "Tesla", "bolt"),
    ("nordic semiconductor", "Nordic", "lamp"),
    ("silicon lab", "Silicon Labs", "lamp"),
    ("texas instrument", "Texas Instruments", "processor"),
    ("murata", "Murata", "wifi"),
    ("realtek", "Realtek", "processor"),
    ("mediatek", "MediaTek", "processor"),
    ("qualcomm", "Qualcomm", "processor"),
    ("broadcom", "Broadcom", "processor"),
    # -- media, audio, wearables, printers ---------------------------------
    ("amazon", "Amazon", "volume"),
    ("sonos", "Sonos", "volume"),
    ("bose", "Bose", "volume"),
    ("harman", "Harman", "volume"),
    ("denon", "Denon", "volume"),
    ("marantz", "Marantz", "volume"),
    ("yamaha", "Yamaha", "volume"),
    ("pioneer", "Pioneer", "volume"),
    ("onkyo", "Onkyo", "volume"),
    ("roku", "Roku", "monitor"),
    ("nintendo", "Nintendo", "game-structure"),
    ("sony", "Sony", "game-structure"),
    ("lg innotek", "LG", "monitor"),
    ("lg electron", "LG", "monitor"),
    ("skyworth", "Skyworth", "monitor"),
    ("realme", "realme", "mobile-android"),
    ("tecno mobile", "TECNO", "mobile-android"),
    ("itel mobile", "itel", "mobile-android"),
    ("infinix", "Infinix", "mobile-android"),
    ("quectel", "Quectel", "sim-card"),
    ("midea", "Midea", "home"),
    ("cloud network technology", "Foxconn", "desktop"),
    ("panasonic", "Panasonic", "monitor"),
    ("sharp", "Sharp", "monitor"),
    ("toshiba", "Toshiba", "monitor"),
    ("vestel", "Vestel", "monitor"),
    ("tp-vision", "TP Vision", "monitor"),
    ("garmin", "Garmin", "clock"),
    ("fitbit", "Fitbit", "clock"),
    ("withings", "Withings", "clock"),
    ("logitech", "Logitech", "headphones"),
    ("razer", "Razer", "headphones"),
    ("steelseries", "SteelSeries", "headphones"),
    ("corsair", "Corsair", "headphones"),
    ("brother", "Brother", "print"),
    ("canon", "Canon", "print"),
    ("epson", "Epson", "print"),
    ("ricoh", "Ricoh", "print"),
    ("xerox", "Xerox", "print"),
    ("yealink", "Yealink", "microphone"),
    ("grandstream", "Grandstream", "microphone"),
    ("qingdao intelligent", "Qingdao Intelligent", "cube"),
]

# Well-established locally-administered conventions. The locally-administered
# bit means the address is not in any registry, so these are the only ones
# that can be named at all; everything else is reported as a private address
# rather than guessed at.
LOCAL_RULES: list[tuple[str, str, str]] = [
    ("5254", "QEMU/KVM", "server"),
    ("0242", "Docker", "server"),
    ("0a0027", "VirtualBox", "server"),
]


def base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    for _ in range(INDEX_LEN):
        value, rem = divmod(value, 36)
        out = digits[rem] + out
    if value:
        raise ValueError("vendor index overflows the fixed-width encoding")
    return out


def classify(org: str) -> tuple[str, str] | None:
    lowered = org.lower()
    for pattern, display, icon in RULES:
        if pattern in lowered:
            return display, icon
    return None


def load_rows(csv_path: Path | None) -> list[tuple[str, str]]:
    if csv_path:
        raw = csv_path.read_bytes()
    else:
        with urllib.request.urlopen(OUI_URL, timeout=120) as response:
            raw = response.read()
    text = raw.decode("utf-8", errors="replace")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("Registry") != "MA-L":
            continue
        assignment = (row.get("Assignment") or "").strip().lower()
        organization = (row.get("Organization Name") or "").strip()
        if len(assignment) != PREFIX_LEN or not re.fullmatch(r"[0-9a-f]{6}", assignment):
            continue
        if organization:
            rows.append((assignment, organization))
    return rows


def build(rows: list[tuple[str, str]]) -> tuple[str, list[str], list[str], int]:
    matched: dict[str, tuple[str, str]] = {}
    for prefix, organization in rows:
        hit = classify(organization)
        if hit:
            matched[prefix] = hit

    vendors: list[str] = []
    icons: list[str] = []
    seen: dict[str, int] = {}
    for display, icon in sorted(set(matched.values())):
        if display in seen:
            continue
        seen[display] = len(vendors) + 1
        vendors.append(display)
        icons.append(icon)

    if len(vendors) >= MAX_VENDORS:
        raise SystemExit(f"too many vendors for a {INDEX_LEN}-char index: {len(vendors)}")

    records = [prefix + base36(seen[display]) for prefix, (display, _) in sorted(matched.items())]
    return "".join(records), vendors, icons, len(matched)


def lua_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(index: str, vendors: list[str], icons: list[str], local_rules) -> str:
    # 100 KB on one physical line is unreadable in a diff; chunk it and
    # concatenate at load time into a single interned string.
    chunk = 240
    pieces = [index[i:i + chunk] for i in range(0, len(index), chunk)]
    body = "\n".join(f"  {lua_string(p)}," for p in pieces)

    vendor_lines = "\n".join(
        f"  {lua_string(v)}, {lua_string(i)}," for v, i in zip(vendors, icons)
    )
    local_lines = "\n".join(
        f"  {lua_string(p)}, {lua_string(d)}, {lua_string(i)},"
        for p, d, i in local_rules
    )

    return f"""-- GENERATED by scripts/build_oui_table.py from the IEEE MA-L registry. DO NOT EDIT.
--
-- Regenerate with:  python3 -m scripts.build_oui_table
--
-- `index` is a sorted, fixed-width record table: {PREFIX_LEN} hex characters of
-- 24-bit OUI prefix followed by a {INDEX_LEN}-character base-36 index into
-- `vendors`. oui.lua binary-searches it. See scripts/build_oui_table.py for why this
-- shape rather than a hash table.
--
-- records: {len(index) // RECORD_LEN}
-- vendors: {len(vendors)}

return {{
  record_len = {RECORD_LEN},
  prefix_len = {PREFIX_LEN},

  -- flat {{display, icon}} pairs, 1-based vendor index = (n + 1) / 2
  vendors = {{
{vendor_lines}
  }},

  -- flat {{prefix, display, icon}} triples for locally-administered ranges
  local_ranges = {{
{local_lines}
  }},

  index = table.concat({{
{body}
  }}),
}}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, help="local copy of the IEEE oui.csv")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    rows = load_rows(args.csv)
    if not rows:
        raise SystemExit("no MA-L rows parsed from the registry")

    index, vendors, icons, matched = build(rows)
    text = render(index, vendors, icons, LOCAL_RULES)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    print(f"registry MA-L rows : {len(rows)}")
    print(f"matched prefixes   : {matched}")
    print(f"vendors            : {len(vendors)}")
    print(f"wrote {args.out} ({len(text)} bytes, sha256 {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
