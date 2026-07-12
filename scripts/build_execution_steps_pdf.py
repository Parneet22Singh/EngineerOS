"""Generates Execution_Steps.pdf — how to run EngineerOS, step by step."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, ListFlowable, ListItem,
)

OUT = "Execution_Steps.pdf"

styles = getSampleStyleSheet()
title_style = ParagraphStyle("TitleX", parent=styles["Title"], fontSize=22, spaceAfter=4)
subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=18)
h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=15, spaceBefore=18, spaceAfter=8, textColor=colors.HexColor("#1a1a2e"))
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#333366"))
body = ParagraphStyle("BodyX", parent=styles["Normal"], fontSize=10.3, leading=15, spaceAfter=6, alignment=TA_LEFT)
note = ParagraphStyle("Note", parent=styles["Normal"], fontSize=9.5, leading=13, textColor=colors.HexColor("#555555"), spaceAfter=6)
code_style = ParagraphStyle(
    "Code", parent=styles["Code"], fontName="Courier", fontSize=9.3, leading=13,
    backColor=colors.HexColor("#f4f4f8"), borderPadding=8, spaceAfter=10, spaceBefore=4,
    leftIndent=2,
)


def code(text: str) -> Table:
    """Render a code block as a shaded, full-width table cell (wraps long lines cleanly)."""
    p = Paragraph(text.replace("\n", "<br/>").replace(" ", "&nbsp;"), code_style)
    t = Table([[p]], colWidths=[6.3 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f4f4f8")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0dc")),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


doc = SimpleDocTemplate(
    OUT, pagesize=letter,
    topMargin=0.7 * inch, bottomMargin=0.7 * inch, leftMargin=0.85 * inch, rightMargin=0.85 * inch,
    title="EngineerOS — Execution Steps",
)

story = []

story.append(Paragraph("EngineerOS — Execution Steps", title_style))
story.append(Paragraph("How to install and run every module from the command line", subtitle_style))
story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
story.append(Spacer(1, 12))

# --- 0. Setup ---------------------------------------------------------------
story.append(Paragraph("0. One-time setup", h1))
story.append(Paragraph(
    "Clone the repository, then set up the backend. Requires Python 3.11+ and Node.js 18+.", body))

story.append(Paragraph("<b>Linux / macOS:</b>", body))
story.append(code(
    "cd EngineerOS/backend\n"
    "python3 -m venv .venv\n"
    "source .venv/bin/activate\n"
    "pip install -r requirements.txt\n"
    "python -m playwright install chromium\n"
    "npm install lighthouse    (optional, for performance audits)"
))

story.append(Paragraph("<b>Windows — cmd.exe or PowerShell:</b>", body))
story.append(code(
    "cd EngineerOS\\backend\n"
    "python -m venv .venv\n"
    ".venv\\Scripts\\activate\n"
    "pip install -r requirements.txt\n"
    "python -m playwright install chromium\n"
    "npm install lighthouse    (optional, for performance audits)"
))

story.append(Paragraph(
    "Copy <b>.env.example</b> to <b>.env</b> in the backend folder and adjust values "
    "(browser mode, AI provider, Lighthouse path) as needed.", note))

# --- 1. Basic pattern ---------------------------------------------------------------
story.append(Paragraph("1. The basic pattern (every session)", h1))
story.append(Paragraph(
    "Activate the virtual environment, then change into the backend folder. All commands "
    "below assume you are in this directory with the venv active.", body))
story.append(code(
    "cd EngineerOS/backend        (Linux/macOS: source .venv/bin/activate)\n"
    "cd EngineerOS\\backend        (Windows: .venv\\Scripts\\activate)"
))
story.append(Paragraph("<b>Running the eos command — this differs by shell:</b>", body))
table_shell = [
    ["Shell", "How to invoke"],
    ["Linux / macOS (bash, zsh)", "./eos scan ...   (or add execute permission: chmod +x eos)"],
    ["Windows cmd.exe", "eos scan ...   (no prefix needed)"],
    ["Windows PowerShell", ".\\eos scan ...   (the .\\ prefix is required — PowerShell "
        "will not run a script from the current folder without it, unless the folder is on PATH)"],
]
shell_table = Table(table_shell, colWidths=[2.1 * inch, 4.2 * inch])
shell_table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, 1), (0, -1), "Helvetica"),
    ("FONTNAME", (1, 1), (1, -1), "Courier"),
    ("FONTSIZE", (0, 0), (-1, -1), 9.3),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f8")]),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0dc")),
    ("TOPPADDING", (0, 0), (-1, -1), 6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
]))
story.append(shell_table)
story.append(Spacer(1, 8))
story.append(Paragraph(
    "The examples below use the plain <b>eos</b> form for brevity — substitute <b>./eos</b> "
    "or <b>.\\eos</b> per the table above depending on your shell.", note))

# --- 2. Module 1 ---------------------------------------------------------------
story.append(Paragraph("2. Module 1 — Website Intelligence (crawl + audit)", h1))
story.append(Paragraph("Crawls a live site and audits accessibility, SEO, responsiveness, broken links, and (optionally) Lighthouse.", body))
story.append(code(
    "eos scan https://example.com\n"
    "eos scan https://example.com --lighthouse\n"
    "eos scan https://example.com -v                    (verbose: full recommendations)"
))
story.append(Paragraph("<b>Scan the whole site (within a limit):</b>", body))
story.append(code(
    "eos scan https://example.com --max-pages 100 --max-depth 4 --lighthouse -f html"
))
story.append(Paragraph(
    "--max-pages caps total pages; --max-depth is how many link-hops deep from the home "
    "page. The crawler breadth-first crawls same-origin pages, seeds from sitemap.xml "
    "(recursing sitemap indexes), treats www and apex as the same site, and skips non-HTML "
    "files. Every crawled page is audited; Lighthouse (if enabled) runs once on the entry "
    "page (~20-40s).", note))

# --- 3. Module 2 ---------------------------------------------------------------
story.append(Paragraph("3. Module 2 — Repository Intelligence", h1))
story.append(Paragraph(
    "Analyzes a local folder or a GitHub URL: language/stack inventory, import graph, "
    "circular dependencies, dead code, hardcoded secrets, TODO debt.", body))
story.append(code(
    "eos scan /path/to/your/repo -m repo\n"
    "eos scan https://github.com/user/repo -m repo      (clones, analyzes, auto-cleans up)"
))

# --- 4. Module 3 ---------------------------------------------------------------
story.append(Paragraph("4. Module 3 — API Intelligence", h1))
story.append(Paragraph(
    "Discovers APIs two ways: live network capture (web) or static route extraction "
    "(repo). Generates an OpenAPI 3.0 spec and a Postman collection.", body))
story.append(code(
    "eos scan https://example.com -m api                 (captures live XHR/fetch calls)\n"
    "eos scan /path/to/repo -m api                       (extracts routes from source)\n"
    "eos scan https://github.com/user/repo -m api\n"
    "eos scan https://example.com -m api --api-mode web  (force a mode)"
))

# --- 4b. Module 4 ---------------------------------------------------------------
story.append(Paragraph("5. Module 4 — Knowledge Graph", h1))
story.append(Paragraph(
    "Builds a semantic map of a repo: a component graph from import relationships, ranked "
    "by connectivity, with cycle detection and AI one-line role summaries of the most "
    "important components. Works on a local folder or a git URL; exports graph.json.", body))
story.append(code(
    "eos scan /path/to/repo -m kg\n"
    "eos scan /path/to/repo -m kg --max-nodes 20         (summarize more components; slower)\n"
    "eos scan https://github.com/user/repo -m kg"
))
story.append(Paragraph(
    "AI summaries need the model server running (see Module 5 below). Without it, you still "
    "get the full structural graph — just no summaries. --max-nodes controls how many of the "
    "top components get summarized.", note))

# --- 5. Module 5 ---------------------------------------------------------------
story.append(Paragraph("6. Module 5 — AI Copilot (ask / chat)", h1))
story.append(Paragraph(
    "Grounded coding Q&A: retrieves the most relevant source files for your question and "
    "answers using a local (or cloud) model, citing the files it used.", body))
story.append(Paragraph("<b>Step A — start a model server first, in its own terminal (leave it running):</b>", body))
story.append(code(
    "Linux/macOS:      ./serve-ai.sh\n"
    "Windows cmd:       serve-ai.cmd\n"
    "Windows PowerShell: .\\serve-ai.ps1"
))
story.append(Paragraph(
    "Wait for a line confirming the server is listening (e.g. "
    "<font face=\"Courier\">http://127.0.0.1:8080</font>). Keep this window open. "
    "Alternatively, configure a cloud AI provider in .env instead of running a local server.", note))
story.append(Paragraph("<b>Step B — back in your original terminal, ask questions:</b>", body))
story.append(code(
    "eos ask \"how does the plugin manager work?\" --repo /path/to/repo\n"
    "eos chat --repo /path/to/any/repo                   (interactive session, type 'exit' to quit)\n"
    "eos ask \"write a debounce function in TypeScript\"  (no --repo = general question)"
))
story.append(Paragraph(
    "Config lives in backend/.env (AI_PROVIDER, OPENAI_BASE_URL, AI_MODEL). Set AI_PROVIDER "
    "to anthropic, openai, or a local OpenAI-compatible server, with the matching API key/URL. "
    "Local models are free but slower than a cloud API, depending on your hardware.", note))

# --- 6. Module 6 ---------------------------------------------------------------
story.append(Paragraph("7. Module 6 — Autonomous QA Agent", h1))
story.append(Paragraph(
    "Given only a URL, autonomously explores a single page: clicks buttons, opens menus, "
    "fills and submits forms, handles dialogs, and reports runtime errors and interaction bugs.", body))
story.append(code(
    "eos scan https://example.com -m qa\n"
    "eos scan https://example.com -m qa -v               (verbose: shows every flow it tried)\n"
    "eos scan https://example.com -m qa --max-actions 25"
))

# --- 7. Reports & history ---------------------------------------------------------------
story.append(Paragraph("8. Viewing results and scan history", h1))
story.append(Paragraph(
    "Every scan is saved centrally, regardless of which folder you ran it from.", body))
story.append(code(
    "eos list                          (all past scans: id, module, status, health)\n"
    "eos report <scan-id>               (re-export a past scan)\n"
    "eos report <scan-id> -f pdf        (re-export as PDF)\n"
    "eos modules                        (list everything available)\n"
    "eos scan ... -f html,pdf,json,csv -o ./reports     (choose formats + folder)"
))
story.append(Paragraph(
    "HTML reports open directly in a browser. Reports default to ./reports/ under the "
    "current directory; scan history lives in backend/engineeros.db.", note))

# --- 7b. Browser / WAF + detailed reports ---------------------------------------------
story.append(Paragraph("9. WAF-protected sites (Akamai / Cloudflare) & report detail", h1))
story.append(Paragraph(
    "Headless browsers send a 'HeadlessChrome' User-Agent that some WAFs block with 'Access "
    "Denied'. If a live scan gets blocked, drive a real visible browser instead:", body))
story.append(code(
    "eos scan protected-site.com --headed --browser chrome        (visible Chrome, bypasses common WAF blocks)\n"
    "eos scan internal-site.com --headless --browser chromium     (fast headless, unprotected sites)"
))
story.append(Paragraph(
    "This is not a bot-evasion tool — it just uses a real browser like a QA engineer would. "
    "Repeatedly scanning a protected production site from one IP can still trip rate/reputation "
    "blocking; for your own sites, allowlist the scanner in the WAF or scan a staging environment.", note))
story.append(Paragraph("<b>Reports are specific, not vague.</b>", body))
story.append(Paragraph(
    "Each finding lists the exact offending items: every broken link with its HTTP status "
    "AND the page(s) it was found on, every failed asset with its URL/status, and the actual "
    "console error texts — rendered as detail tables in the HTML/PDF report.", note))

# --- 9. Common flags table ---------------------------------------------------------------
story.append(Paragraph("10. Common flags (quick reference)", h1))
flag_rows = [
    ["Flag", "Meaning"],
    ["-m, --module", "web (default) | qa | repo | api | kg"],
    ["--max-nodes", "Components to AI-summarize in the knowledge graph (kg)"],
    ["-v, --verbose", "Show full recommendations, elements, explored flows"],
    ["-f, --format", "Report formats: html,pdf,json,csv (comma-separated)"],
    ["-o, --out", "Output folder for reports"],
    ["--max-pages", "Crawl page budget (web module)"],
    ["--max-depth", "Crawl depth (web module)"],
    ["--max-actions", "Interaction budget (qa module)"],
    ["--max-files", "File budget (repo / api modules)"],
    ["--api-mode", "Force API discovery mode: web | repo"],
    ["--lighthouse", "Also run Lighthouse on the entry page"],
    ["--headed", "Drive a visible browser (bypasses some WAF blocks)"],
    ["--headless", "Force headless (faster; for sites that don't block bots)"],
    ["--browser", "chrome | msedge | chromium (installed vs bundled browser)"],
    ["--repo", "Ground Copilot answers in this local repo path (ask / chat)"],
]
flag_table = Table(flag_rows, colWidths=[1.7 * inch, 4.6 * inch])
flag_table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTNAME", (0, 1), (0, -1), "Courier"),
    ("FONTSIZE", (0, 0), (-1, -1), 9.3),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f8")]),
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d0d0dc")),
    ("TOPPADDING", (0, 0), (-1, -1), 6),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
]))
story.append(flag_table)

# --- 9. Things to know ---------------------------------------------------------------
story.append(Paragraph("11. Things worth knowing", h1))
items = [
    "Modules 1, 2, 3, and 6 need no AI and no extra setup — they work the instant you run <b>eos scan</b>.",
    "Module 5 (ask/chat) needs a model server (local or cloud-configured) reachable before use.",
    "If a live-site scan reports a suspiciously perfect health score alongside a \"page failed to load\" "
    "finding, the page never actually loaded — the result isn't meaningful; re-run once network "
    "conditions are clear.",
    "PowerShell requires the .\\ prefix to run local scripts (eos, serve-ai) that cmd.exe and "
    "Linux/macOS shells don't need — see the shell table in section 1.",
]
story.append(ListFlowable(
    [ListItem(Paragraph(t, body), leftIndent=6) for t in items],
    bulletType="bullet", start="•", leftIndent=14,
))

doc.build(story)
print(f"WROTE {OUT}")
