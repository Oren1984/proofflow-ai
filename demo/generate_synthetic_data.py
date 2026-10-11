"""Regenerates the static synthetic evidence files in demo/synthetic_data/.

Development-time helper (requires Pillow, which ships with Streamlit). The generated
files are committed, so running this is only needed to change the demo artifacts.
Everything produced here is fictional.

    python demo/generate_synthetic_data.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "synthetic_data"
INK, MUTED, ACCENT, PANEL = "#1f2933", "#5b6875", "#2b6cb0", "#f1f4f7"


def font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def dashboard(filename: str, title: str, tiles: list[tuple[str, str]], bars: list[int]) -> None:
    image = Image.new("RGB", (960, 540), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 960, 56], fill=ACCENT)
    draw.text((24, 16), title, fill="white", font=font(22))
    draw.text((700, 22), "SYNTHETIC DEMO DATA", fill="white", font=font(14))
    for index, (label, value) in enumerate(tiles):
        left = 24 + index * 310
        draw.rounded_rectangle([left, 84, left + 290, 184], radius=10, fill=PANEL)
        draw.text((left + 16, 98), label, fill=MUTED, font=font(15))
        draw.text((left + 16, 126), value, fill=INK, font=font(32))
    draw.text((24, 214), "Monthly revenue (fictional)", fill=MUTED, font=font(15))
    for index, height in enumerate(bars):
        left = 40 + index * 74
        draw.rectangle([left, 500 - height, left + 48, 500], fill=ACCENT)
    draw.line([24, 500, 936, 500], fill=MUTED, width=2)
    image.save(OUT / filename)


def pdf(filename: str, heading: str, lines: list[str]) -> None:
    page = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(page)
    draw.text((90, 90), heading, fill=INK, font=font(44))
    draw.text((90, 150), "Synthetic demonstration document - fictional content", fill=MUTED, font=font(22))
    for index, line in enumerate(lines):
        draw.text((90, 230 + index * 44), line, fill=INK, font=font(26))
    page.save(OUT / filename, "PDF", resolution=150.0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dashboard(
        "dashboard_home.png", "Northwind Analytics - Home",
        [("Status", "Online"), ("Environment", "Staging"), ("Last refresh", "06:00 UTC")],
        [60, 85, 70, 110, 95, 130, 120, 150, 140, 170, 160, 190],
    )
    dashboard(
        "dashboard_kpis.png", "Northwind Analytics - KPIs",
        [("Revenue", "$1.24M"), ("Active customers", "8,412"), ("Churn", "2.1%")],
        [90, 100, 96, 120, 135, 128, 150, 162, 158, 180, 196, 210],
    )
    pdf("user_guide.pdf", "Analytics Dashboard - User Guide v1.0", [
        "1. Signing in and opening the dashboard",
        "2. Reading the KPI tiles: revenue, active customers, churn",
        "3. Filtering by period and region",
        "4. Exporting data to CSV",
        "5. Getting support",
    ])
    pdf("stakeholder_review_minutes.pdf", "Final Stakeholder Review - Minutes", [
        "Attendees: delivery lead, product owner, customer sponsor (fictional)",
        "Agenda: walkthrough of dashboard, KPIs, CSV export, documentation",
        "Outcome: review completed; no blocking issues raised",
        "Follow-up: customer acceptance to be confirmed separately in writing",
    ])
    (OUT / "user_guide_draft.md").write_text(
        "# Analytics Dashboard - User Guide (DRAFT)\n\n"
        "Synthetic demonstration document.\n\n"
        "## 1. Signing in\n\nTODO\n\n## 2. KPI tiles\n\nTODO\n\n## 3. CSV export\n\nTODO\n",
        encoding="utf-8", newline="\n",
    )
    (OUT / "release_notes.md").write_text(
        "# Release Notes - Analytics Dashboard 1.0\n\n"
        "Synthetic demonstration document.\n\n"
        "- Dashboard home with status and refresh information\n"
        "- KPI tiles: revenue, active customers, churn\n"
        "- CSV export of the KPI table\n"
        "- Known limitation: data refreshes once per day\n",
        encoding="utf-8", newline="\n",
    )
    (OUT / "export_tests_passed.txt").write_text(
        "CSV export tests passed on my machine - all good.\n"
        "(Synthetic demo note: an unstructured claim, not a test-result record.)\n",
        encoding="utf-8", newline="\n",
    )
    print(f"Wrote {len(list(OUT.iterdir()))} files to {OUT}")


if __name__ == "__main__":
    main()
