"""Generate, validate, and cache a link-enabled PDF for one saved scrape."""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from pypdf import PdfReader


NAVY = colors.HexColor("#11253E")
BLUE = colors.HexColor("#176B87")
CYAN = colors.HexColor("#64CCC5")
ORANGE = colors.HexColor("#FF9B50")
INK = colors.HexColor("#1D2733")
MUTED = colors.HexColor("#667585")
PALE = colors.HexColor("#F2F6F8")
LINE = colors.HexColor("#D8E2E8")
WHITE = colors.white
CT = ZoneInfo("America/Chicago")
_REPORT_LOCKS: dict[tuple[int, str], threading.Lock] = {}
_REPORT_LOCKS_GUARD = threading.Lock()


def _register_fonts() -> tuple[str, str]:
    candidates = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("ReportSans", str(regular)))
            pdfmetrics.registerFont(TTFont("ReportSansBold", str(bold)))
            return "ReportSans", "ReportSansBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = _register_fonts()


def _clean(value: object) -> str:
    raw = "" if value is None else str(value)
    text = unicodedata.normalize("NFKC", raw)
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        text = text.replace(dash, "-")
    text = text.replace("\u00a0", " ")
    text = re.sub(
        "["
        "\U0001F1E6-\U0001F1FF"
        "\U0001F300-\U0001FAFF"
        "\U00002700-\U000027BF"
        "\U00002600-\U000026FF"
        "]+",
        "",
        text,
    )
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    return re.sub(r"[ \t]+", " ", text).strip()


def _paragraph_text(value: object) -> str:
    return html.escape(_clean(value)).replace("\n", "<br/>")


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_time(value: object, include_zone: bool = True) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Time unavailable"
    suffix = " CT" if include_zone else ""
    return parsed.astimezone(CT).strftime("%b %d, %Y at %I:%M %p") + suffix


def _load_report_data(db_path: Path, run_id: str, user_id: int) -> dict:
    connection = sqlite3.connect(db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        run = connection.execute(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?",
            (run_id, user_id),
        ).fetchone()
        if run is None:
            raise ValueError("Saved scrape not found for this user")
        story_rows = connection.execute(
            "SELECT * FROM stories WHERE run_id = ? AND display_ok = 1 "
            "ORDER BY rank, id",
            (run_id,),
        ).fetchall()
        post_rows = connection.execute(
            """
            SELECT * FROM tweets
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (run_id,),
        ).fetchall()
    finally:
        connection.close()

    posts = [dict(row) for row in post_rows]
    post_map = {str(post["tweet_id"]): post for post in posts}
    stories = []
    for row in story_rows:
        item = dict(row)
        try:
            tweet_ids = [str(value) for value in json.loads(item["tweet_ids"] or "[]")]
        except (TypeError, json.JSONDecodeError):
            tweet_ids = []
        item["tweet_ids"] = tweet_ids
        item["sources"] = [
            post_map[tweet_id] for tweet_id in tweet_ids if tweet_id in post_map
        ]
        stories.append(item)

    run_data = dict(run)
    try:
        run_data["errors"] = json.loads(run_data.get("errors") or "[]")
    except (TypeError, json.JSONDecodeError):
        run_data["errors"] = []
    return {"run": run_data, "stories": stories, "posts": posts}


def _report_lock(user_id: int, run_id: str) -> threading.Lock:
    key = (int(user_id), str(run_id))
    with _REPORT_LOCKS_GUARD:
        return _REPORT_LOCKS.setdefault(key, threading.Lock())


def _safe_run_id(run_id: str) -> str:
    value = str(run_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise ValueError("Saved scrape ID is invalid")
    return value


def report_filename(data: dict, run_id: str) -> str:
    started = _parse_datetime(data["run"].get("started_at"))
    date = (
        started.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if started is not None
        else "unknown-date"
    )
    return f"crypto-scrape-{date}-{run_id}.pdf"


def validate_report(path: Path, data: dict, run_id: str) -> None:
    """Reject incomplete/corrupt reports before they can be delivered."""
    reader = PdfReader(str(path))
    if not reader.pages:
        raise ValueError("Generated report has no pages")

    page_text = [(page.extract_text() or "").strip() for page in reader.pages]
    if any(not text for text in page_text):
        raise ValueError("Generated report contains an empty page")
    joined = "\n".join(page_text)
    if run_id not in joined:
        raise ValueError("Generated report is missing its scrape ID")

    posts = data["posts"]
    if posts and f"#{len(posts)}" not in joined:
        raise ValueError("Generated report is missing raw posts")

    expected_urls = {
        str(post["url"]).strip()
        for post in posts
        if post.get("url") and str(post["url"]).strip()
    }
    linked_urls: set[str] = set()
    for page in reader.pages:
        annotations = page.get("/Annots") or []
        for annotation_ref in annotations:
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            if action and action.get("/URI"):
                linked_urls.add(str(action.get("/URI")))
    missing = expected_urls - linked_urls
    if missing:
        raise ValueError(
            f"Generated report is missing {len(missing)} source link(s)"
        )


def ensure_scrape_report(
    db_path: str | Path,
    report_dir: str | Path,
    run_id: str,
    user_id: int,
) -> Path:
    """Return a validated cached report, generating it atomically if needed."""
    safe_run_id = _safe_run_id(run_id)
    db_path = Path(db_path)
    data = _load_report_data(db_path, safe_run_id, int(user_id))
    destination_dir = Path(report_dir) / str(int(user_id)) / safe_run_id
    destination = destination_dir / report_filename(data, safe_run_id)

    with _report_lock(int(user_id), safe_run_id):
        # Re-read after waiting for a concurrent generator so validation uses
        # the same durable scrape state as the report we are about to return.
        data = _load_report_data(db_path, safe_run_id, int(user_id))
        if destination.is_file():
            try:
                validate_report(destination, data, safe_run_id)
                return destination
            except Exception:
                # Keep the old file until a valid replacement is ready.
                pass

        destination_dir.mkdir(parents=True, exist_ok=True)
        temporary = destination_dir / (
            f".{destination.stem}.{uuid.uuid4().hex}.tmp.pdf"
        )
        try:
            generate_report(db_path, safe_run_id, int(user_id), temporary)
            validate_report(temporary, data, safe_run_id)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


class ReportDocTemplate(BaseDocTemplate):
    def __init__(self, output_path: Path, run_id: str):
        super().__init__(
            str(output_path),
            pagesize=letter,
            leftMargin=0.62 * inch,
            rightMargin=0.62 * inch,
            topMargin=0.66 * inch,
            bottomMargin=0.58 * inch,
            title=f"Crypto News Scrape Report - {run_id}",
            author="Crypto News Research Agent",
            subject="Saved crypto news scrape with source links and raw posts",
        )
        self.run_id = run_id
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="report-frame",
        )
        self.addPageTemplates(
            PageTemplate(id="report", frames=[frame], onPage=self._draw_page)
        )

    def _draw_page(self, canvas, doc) -> None:
        canvas.saveState()
        width, height = letter
        canvas.setFillColor(NAVY)
        canvas.rect(0, height - 0.29 * inch, width, 0.29 * inch, fill=1, stroke=0)
        canvas.setFont(FONT_BOLD, 7.5)
        canvas.setFillColor(WHITE)
        canvas.drawString(
            0.62 * inch,
            height - 0.19 * inch,
            "CRYPTO NEWS RESEARCH AGENT",
        )
        canvas.setStrokeColor(LINE)
        canvas.line(
            0.62 * inch,
            0.4 * inch,
            width - 0.62 * inch,
            0.4 * inch,
        )
        canvas.setFont(FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(0.62 * inch, 0.23 * inch, f"Run {self.run_id}")
        canvas.drawRightString(
            width - 0.62 * inch,
            0.23 * inch,
            f"Page {doc.page}",
        )
        canvas.restoreState()


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "Title",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=27,
            leading=31,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=10,
        ),
        "subtitle": ParagraphStyle(
            "Subtitle",
            parent=base["Normal"],
            fontName=FONT,
            fontSize=11,
            leading=16,
            textColor=MUTED,
            spaceAfter=18,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading1"],
            fontName=FONT_BOLD,
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceBefore=8,
            spaceAfter=10,
        ),
        "story": ParagraphStyle(
            "Story",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=13.2,
            leading=16,
            textColor=NAVY,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=9.4,
            leading=13.5,
            textColor=INK,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=7.8,
            leading=11,
            textColor=MUTED,
        ),
        "meta": ParagraphStyle(
            "Meta",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=7.6,
            leading=10,
            textColor=BLUE,
        ),
        "link": ParagraphStyle(
            "Link",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=7.6,
            leading=10.5,
            textColor=BLUE,
            wordWrap="CJK",
        ),
        "index": ParagraphStyle(
            "Index",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=9.2,
            leading=13,
            textColor=INK,
        ),
        "stat": ParagraphStyle(
            "Stat",
            parent=base["BodyText"],
            fontName=FONT_BOLD,
            fontSize=14,
            leading=17,
            textColor=NAVY,
            alignment=TA_CENTER,
        ),
        "stat_label": ParagraphStyle(
            "StatLabel",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=7.3,
            leading=9,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
    }


def _stat_cell(value: object, label: str, styles: dict) -> list:
    return [
        Paragraph(_paragraph_text(value), styles["stat"]),
        Paragraph(_paragraph_text(label.upper()), styles["stat_label"]),
    ]


def _source_card(source: dict, index: int, styles: dict) -> Table:
    username = _clean(source.get("username") or "unknown")
    url = _clean(source.get("url") or "")
    header = Paragraph(
        f"<b>Source {index}</b> &nbsp; @{html.escape(username)}"
        f" &nbsp; | &nbsp; {_paragraph_text(_format_time(source.get('created_at')))}",
        styles["meta"],
    )
    body = Paragraph(_paragraph_text(source.get("text") or ""), styles["body"])
    content = [header, Spacer(1, 4), body]
    if url:
        safe_url = html.escape(url, quote=True)
        content.extend(
            [
                Spacer(1, 2),
                Paragraph(
                    f'<link href="{safe_url}" color="#176B87"><u>{safe_url}</u></link>',
                    styles["link"],
                ),
            ]
        )
    card = Table([[content]], colWidths=[6.95 * inch])
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("BOX", (0, 0), (-1, -1), 0.55, LINE),
                ("LINEBEFORE", (0, 0), (0, -1), 3, CYAN),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return card


def _raw_post_card(post: dict, index: int, styles: dict) -> Table:
    username = _clean(post.get("username") or "unknown")
    tier = _clean(post.get("tier") or "")
    tags = _clean(post.get("tags") or "").replace("|", ", ")
    url = _clean(post.get("url") or "")
    meta_bits = [
        f"#{index}",
        f"@{username}",
        _format_time(post.get("created_at")),
    ]
    if tier:
        meta_bits.append(f"Tier: {tier}")
    if tags:
        meta_bits.append(f"Tags: {tags}")
    content = [
        Paragraph(_paragraph_text(" | ".join(meta_bits)), styles["meta"]),
        Spacer(1, 3),
        Paragraph(_paragraph_text(post.get("text") or ""), styles["body"]),
    ]
    if url:
        safe_url = html.escape(url, quote=True)
        content.extend(
            [
                Spacer(1, 1),
                Paragraph(
                    f'<link href="{safe_url}" color="#176B87"><u>{safe_url}</u></link>',
                    styles["link"],
                ),
            ]
        )
    card = Table([[content]], colWidths=[6.95 * inch])
    card.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.45, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return card


def generate_report(db_path: Path, run_id: str, user_id: int, output_path: Path) -> None:
    data = _load_report_data(db_path, run_id, user_id)
    run = data["run"]
    stories = data["stories"]
    posts = data["posts"]
    styles = _styles()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = ReportDocTemplate(output_path, run_id)
    flow = []

    flow.append(Spacer(1, 0.25 * inch))
    flow.append(Paragraph("Crypto News<br/>Scrape Report", styles["title"]))
    flow.append(
        Paragraph(
            "A complete, source-linked archive of one production scrape. "
            "Curated stories appear first, followed by every collected post.",
            styles["subtitle"],
        )
    )
    stats = Table(
        [
            [
                _stat_cell(run.get("tweets_fetched", 0), "Posts", styles),
                _stat_cell(run.get("accounts_hit", 0), "Accounts", styles),
                _stat_cell(len(stories), "Stories", styles),
                _stat_cell(len(run.get("errors") or []), "Errors", styles),
            ]
        ],
        colWidths=[1.74 * inch] * 4,
    )
    stats.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PALE),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.45, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    flow.extend([stats, Spacer(1, 16)])
    details = [
        ["Run ID", run_id],
        ["Scrape started", _format_time(run.get("started_at"))],
        ["Scrape finished", _format_time(run.get("finished_at"))],
        ["Report generated", _format_time(datetime.now(timezone.utc))],
    ]
    details_table = Table(details, colWidths=[1.35 * inch, 5.55 * inch])
    details_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), FONT_BOLD),
                ("FONTNAME", (1, 0), (1, -1), FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (0, -1), BLUE),
                ("TEXTCOLOR", (1, 0), (1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.35, LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    flow.extend([details_table, Spacer(1, 20)])
    flow.append(Paragraph("Curated story index", styles["section"]))
    for story in stories:
        source_count = len(story["sources"])
        flow.append(
            Paragraph(
                f"<b>{int(story['rank'])}.</b> {_paragraph_text(story['headline'])} "
                f'<font color="#667585">({source_count} source'
                f'{"s" if source_count != 1 else ""})</font>',
                styles["index"],
            )
        )
        flow.append(Spacer(1, 4))
    flow.append(Spacer(1, 10))
    flow.append(
        Paragraph(
            "How to use this report",
            styles["section"],
        )
    )
    flow.append(
        Paragraph(
            "Use the curated section to understand the leading stories. Open the "
            "underlined X URLs to inspect original posts. Use the raw archive to "
            "audit the complete scrape or locate material that was not selected as "
            "a top story. Curated summaries are editorial aids; source posts remain "
            "the evidence of record.",
            styles["body"],
        )
    )

    flow.append(PageBreak())
    flow.append(Paragraph("Curated stories and evidence", styles["section"]))
    flow.append(
        Paragraph(
            "Every source linked by the curation model is shown with its original "
            "account, post time, text, and clickable URL.",
            styles["subtitle"],
        )
    )
    for story in stories:
        rank = int(story["rank"])
        story_header = Table(
            [
                [
                    Paragraph(str(rank), styles["stat"]),
                    [
                        Paragraph(_paragraph_text(story["headline"]), styles["story"]),
                        Paragraph(_paragraph_text(story["summary"]), styles["body"]),
                        Paragraph(
                            f"Newsworthiness score: {float(story['score']):.2f} "
                            f"| Linked sources: {len(story['sources'])}",
                            styles["small"],
                        ),
                    ],
                ]
            ],
            colWidths=[0.54 * inch, 6.38 * inch],
        )
        story_header.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, 0), ORANGE),
                    ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FFF8F1")),
                    ("BOX", (0, 0), (-1, -1), 0.65, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (0, 0), 7),
                    ("RIGHTPADDING", (0, 0), (0, 0), 7),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (1, 0), (1, 0), 10),
                    ("RIGHTPADDING", (1, 0), (1, 0), 10),
                ]
            )
        )
        flow.extend([story_header, Spacer(1, 8)])
        if story["sources"]:
            for source_index, source in enumerate(story["sources"], start=1):
                flow.extend(
                    [
                        KeepTogether(_source_card(source, source_index, styles)),
                        Spacer(1, 6),
                    ]
                )
        else:
            flow.append(
                Paragraph(
                    "No matching source posts were preserved for this story.",
                    styles["body"],
                )
            )
        flow.append(Spacer(1, 12))

    flow.append(PageBreak())
    flow.append(Paragraph("Complete raw post archive", styles["section"]))
    flow.append(
        Paragraph(
            f"All {len(posts)} collected posts, ordered newest first. "
            "This section includes posts that were not selected for a curated story.",
            styles["subtitle"],
        )
    )
    for post_index, post in enumerate(posts, start=1):
        flow.extend(
            [
                KeepTogether(_raw_post_card(post, post_index, styles)),
                Spacer(1, 6),
            ]
        )

    if run.get("errors"):
        flow.append(PageBreak())
        flow.append(Paragraph("Scrape errors", styles["section"]))
        for error in run["errors"]:
            flow.append(
                Paragraph(f"- {_paragraph_text(error)}", styles["body"])
            )

    doc.build(flow)
