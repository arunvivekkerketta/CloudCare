#!/usr/bin/env python3
"""CloudCare contrast gate (gate S8).

Asserts the WCAG 2.1 contrast ratio of every colour pair this fix touched,
using the values ACTUALLY shipped in assets/css/style.css (and index.html
for the home-page stat numbers) — not a hard-coded copy of them.

Usage:  python scripts/check-contrast.py
Exit code 0 = every pair passes, 1 = at least one failure.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "cloudcare" / "assets" / "css" / "style.css"
INDEX_PATH = ROOT / "cloudcare" / "index.html"


# ---------------------------------------------------------------- colour maths
def _channel(value: float) -> float:
    value /= 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    hex_color = hex_color.strip().lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def ratio(fg: str, bg: str) -> float:
    l1, l2 = luminance(fg), luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# ---------------------------------------------------------------- css parsing
def block(css: str, opener: str) -> str:
    """Return the body of the first `{ opener { ... } }` block."""
    start = css.index(opener)
    brace = css.index("{", start)
    depth = 0
    for i in range(brace, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[brace + 1:i]
    raise ValueError(f"unterminated block for {opener!r}")


def declarations(body: str) -> dict[str, str]:
    out = {}
    for raw in body.split(";"):
        if ":" not in raw:
            continue
        name, _, value = raw.partition(":")
        out[name.strip().lower()] = value.strip()
    return out


def rule(css: str, selector: str) -> dict[str, str]:
    selector = selector.rstrip("{").rstrip()
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    if not match:
        raise KeyError(f"selector not found in style.css: {selector}")
    return declarations(match.group(1))


def resolve(value: str, *scopes: dict[str, str]) -> str:
    """Resolve var(--x) / var(--x, fallback) against the given scopes."""
    value = value.strip()
    m = re.fullmatch(r"var\(\s*(--[a-z0-9-]+)\s*(?:,\s*([^)]+))?\)", value)
    if not m:
        return value
    name, fallback = m.group(1), m.group(2)
    for scope in scopes:
        if name in scope:
            return resolve(scope[name], *scopes)
    if fallback:
        return resolve(fallback.strip(), *scopes)
    raise KeyError(f"unresolvable {name}")


def main() -> int:
    css = CSS_PATH.read_text(encoding="utf-8")
    index_html = INDEX_PATH.read_text(encoding="utf-8")
    # comments may contain ':' and ';' — strip them before parsing declarations
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)

    light = declarations(block(css, ":root{"))
    dark = declarations(block(css, 'html[data-theme="dark"]{'))

    def lit(hex_value: str) -> str:
        return hex_value

    # (description, foreground, background, minimum ratio)
    checks: list[tuple[str, str, str, float]] = []

    # --- S8: accent button text on coral -----------------------------------
    accent_fg = resolve(rule(css, ".btn-accent{")["color"], light, dark)
    checks.append((".btn-accent text on coral (light)", accent_fg,
                   resolve(light["--coral-500"], light), 4.5))
    checks.append((".btn-accent text on coral (dark)",
                   resolve(dark["--ink-on-accent"], dark),
                   resolve(dark["--coral-500"], dark), 4.5))

    # --- S8: home-page mono stat numbers -----------------------------------
    if "home-stat-value" not in index_html:
        print("FAIL  index.html home stats must use .home-stat-value")
        return 1
    checks.append(("home .mono stat number on card (light)",
                   resolve(light["--teal-800"], light), "#ffffff", 4.5))
    checks.append(("home .mono stat number on card (dark)",
                   resolve(dark["--mint-300"], dark),
                   resolve(dark["--card-bg-solid"], dark), 4.5))

    # --- S8: slate-400 helper text -----------------------------------------
    checks.append(("--slate-400 on page canvas (light)",
                   resolve(light["--slate-400"], light),
                   resolve(light["--bg-canvas"], light), 4.5))
    checks.append(("--slate-400 on page canvas (dark)",
                   resolve(dark["--slate-400"], dark),
                   resolve(dark["--bg-canvas"], dark), 4.5))

    # --- C20: auth links ("Forgot password?" / "Create an account") -------
    checks.append(("--coral-link on card (light)",
                   resolve(light["--coral-link"], light), "#ffffff", 4.5))
    checks.append(("--coral-link on card (dark)",
                   resolve(dark["--coral-link"], dark),
                   resolve(dark["--card-bg-solid"], dark), 4.5))

    # --- S8: badge text on its tinted pill ---------------------------------
    badge_coral_fg = resolve(rule(css, ".badge-coral{")["color"], light, dark)
    checks.append((".badge-coral text on badge-coral bg (light)", badge_coral_fg,
                   resolve(light["--coral-100"], light), 4.5))
    badge_gold_fg = resolve(rule(css, ".badge-gold{")["color"], light, dark)
    checks.append((".badge-gold text on badge-gold bg (light)", badge_gold_fg,
                   resolve(light["--gold-100"], light), 4.5))
    checks.append((".badge-coral text on badge-coral bg (dark)",
                   resolve(dark["--coral-400"], dark),
                   resolve(dark["--coral-100"], dark), 4.5))
    checks.append((".badge-gold text on badge-gold bg (dark)",
                   resolve(dark["--gold-500"], dark),
                   resolve(dark["--gold-100"], dark), 4.5))

    # --- S8: stars (icons need >= 3:1) -------------------------------------
    checks.append(("filled star on white card", resolve(light["--gold-star"], light),
                   "#ffffff", 3.0))
    checks.append(("unfilled star on white card", resolve(light["--slate-400"], light),
                   "#ffffff", 3.0))
    checks.append(("filled star on dark card (dark)",
                   resolve(dark["--gold-star"], dark),
                   resolve(dark["--card-bg-solid"], dark), 3.0))

    # --- C15: footer brand --------------------------------------------------
    if "footer .brand{ color:#fff; }" not in css:
        print("FAIL  style.css must contain `footer .brand{ color:#fff; }`")
        return 1
    checks.append(("footer .brand on footer surface", lit("#fff"),
                   resolve(light["--teal-950"], light), 4.5))

    failures = 0
    width = max(len(desc) for desc, *_ in checks)
    for desc, fg, bg, minimum in checks:
        value = ratio(fg, bg)
        ok = value >= minimum
        if not ok:
            failures += 1
        print(f"{'PASS' if ok else 'FAIL'}  {desc:<{width}}  "
              f"{fg} on {bg} = {value:.2f}:1 (min {minimum}:1)")

    print()
    if failures:
        print(f"{failures} contrast pair(s) below the required ratio.")
        return 1
    print(f"All {len(checks)} contrast pairs pass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
