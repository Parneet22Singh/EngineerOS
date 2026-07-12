"""In-page DOM / accessibility / SEO auditing.

``AUDIT_JS`` runs inside the page (via Playwright ``page.evaluate``) and returns a
single JSON blob with everything we can observe from the DOM in one pass. Keeping it
to one evaluation call keeps the crawl fast. ``findings_from_audit`` turns that blob
into :class:`RawFinding` objects.

The checks mirror the accessibility / SEO items in the EngineerOS spec (alt text,
ARIA, heading hierarchy, duplicate IDs, labels, forms, buttons, horizontal overflow,
canonical, OpenGraph, metadata). An external engine like axe-core can be layered on
top later; these native heuristics are the always-available baseline.
"""
from __future__ import annotations

from typing import Any

from app.modules.website_intelligence.results import RawFinding

AUDIT_JS = r"""
() => {
  const MAX = 25; // cap sample lists so payloads stay small
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + el.id;
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 5) {
      let sel = node.nodeName.toLowerCase();
      if (node.className && typeof node.className === 'string') {
        const cls = node.className.trim().split(/\s+/).slice(0, 2).join('.');
        if (cls) sel += '.' + cls;
      }
      const parent = node.parentNode;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.nodeName === node.nodeName);
        if (sibs.length > 1) sel += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      node = node.parentElement;
      depth++;
    }
    return parts.join(' > ');
  };
  const outer = (el) => (el.outerHTML || '').slice(0, 300);

  // --- Images without alt ---
  const imagesNoAlt = [];
  const allImages = [];
  document.querySelectorAll('img').forEach(img => {
    const src = img.currentSrc || img.getAttribute('src') || '';
    if (src) allImages.push(src);
    const alt = img.getAttribute('alt');
    const decorative = img.getAttribute('role') === 'presentation' || alt === '';
    if (alt === null && !decorative && imagesNoAlt.length < MAX) {
      imagesNoAlt.push({ src, el: cssPath(img), html: outer(img) });
    }
  });

  // --- Heading hierarchy ---
  const headings = [];
  document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(h => {
    headings.push({ level: parseInt(h.tagName[1], 10), text: (h.innerText || '').trim().slice(0, 120) });
  });
  const h1Count = headings.filter(h => h.level === 1).length;
  const headingSkips = [];
  let prev = 0;
  for (const h of headings) {
    if (prev && h.level > prev + 1) {
      headingSkips.push({ from: prev, to: h.level, text: h.text });
    }
    prev = h.level;
  }

  // --- Duplicate IDs ---
  const idCounts = {};
  document.querySelectorAll('[id]').forEach(el => {
    const id = el.id;
    idCounts[id] = (idCounts[id] || 0) + 1;
  });
  const duplicateIds = Object.entries(idCounts)
    .filter(([, n]) => n > 1)
    .slice(0, MAX)
    .map(([id, n]) => ({ id, count: n }));

  // --- Form fields without accessible labels ---
  const unlabeled = [];
  document.querySelectorAll('input, select, textarea').forEach(el => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) return;
    const id = el.id;
    const hasLabelFor = id && document.querySelector('label[for="' + CSS.escape(id) + '"]');
    const wrapped = el.closest('label');
    const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
    const title = el.getAttribute('title');
    if (!hasLabelFor && !wrapped && !aria && !title && unlabeled.length < MAX) {
      unlabeled.push({ el: cssPath(el), html: outer(el) });
    }
  });

  // --- Buttons / links without accessible name ---
  const namelessControls = [];
  document.querySelectorAll('button, a[href], [role="button"]').forEach(el => {
    const text = (el.innerText || el.textContent || '').trim();
    const aria = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby');
    const title = el.getAttribute('title');
    const img = el.querySelector('img[alt]');
    const imgAlt = img && img.getAttribute('alt');
    if (!text && !aria && !title && !imgAlt && namelessControls.length < MAX) {
      namelessControls.push({ el: cssPath(el), tag: el.tagName.toLowerCase(), html: outer(el) });
    }
  });

  // --- Invalid ARIA roles (basic list of valid roles) ---
  const VALID_ROLES = new Set(['alert','alertdialog','application','article','banner','button','cell','checkbox','columnheader','combobox','complementary','contentinfo','definition','dialog','directory','document','feed','figure','form','grid','gridcell','group','heading','img','link','list','listbox','listitem','log','main','marquee','math','menu','menubar','menuitem','menuitemcheckbox','menuitemradio','navigation','none','note','option','presentation','progressbar','radio','radiogroup','region','row','rowgroup','rowheader','scrollbar','search','searchbox','separator','slider','spinbutton','status','switch','tab','table','tablist','tabpanel','term','textbox','timer','toolbar','tooltip','tree','treegrid','treeitem']);
  const badRoles = [];
  document.querySelectorAll('[role]').forEach(el => {
    const role = (el.getAttribute('role') || '').trim().toLowerCase();
    if (role && !VALID_ROLES.has(role) && badRoles.length < MAX) {
      badRoles.push({ role, el: cssPath(el) });
    }
  });

  // --- Horizontal overflow ---
  const de = document.documentElement;
  const overflow = de.scrollWidth > (window.innerWidth + 2);

  // --- Metadata / SEO / social ---
  const metaGet = (sel, attr) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute(attr) || '').trim() : null;
  };
  const canonical = metaGet('link[rel="canonical"]', 'href');
  const og = {};
  document.querySelectorAll('meta[property^="og:"]').forEach(m => {
    og[m.getAttribute('property')] = m.getAttribute('content');
  });
  const twitter = {};
  document.querySelectorAll('meta[name^="twitter:"]').forEach(m => {
    twitter[m.getAttribute('name')] = m.getAttribute('content');
  });

  const meta = {
    title: (document.title || '').trim(),
    description: metaGet('meta[name="description"]', 'content'),
    canonical,
    lang: de.getAttribute('lang'),
    viewport: metaGet('meta[name="viewport"]', 'content'),
    robots: metaGet('meta[name="robots"]', 'content'),
    charset: document.characterSet,
    ogCount: Object.keys(og).length,
    og,
    twitter,
    hasDoctype: !!document.doctype,
  };

  // --- Links + images for the link checker ---
  const links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.href)
    .filter(h => /^https?:/i.test(h));

  return {
    imagesNoAlt,
    allImages: allImages.slice(0, 200),
    headings,
    h1Count,
    headingSkips,
    duplicateIds,
    unlabeled,
    namelessControls,
    badRoles,
    overflow,
    scrollWidth: de.scrollWidth,
    innerWidth: window.innerWidth,
    meta,
    links: Array.from(new Set(links)).slice(0, 500),
  };
}
"""


def findings_from_audit(url: str, audit: dict[str, Any]) -> list[RawFinding]:
    """Translate a raw audit blob into structured findings."""
    out: list[RawFinding] = []

    def add(**kw: Any) -> None:
        out.append(RawFinding(page_url=url, **kw))

    # --- Accessibility ---
    imgs = audit.get("imagesNoAlt") or []
    if imgs:
        add(
            category="accessibility",
            severity="high",
            title=f"{len(imgs)} image(s) missing alt text",
            description="Images without an alt attribute are invisible to screen readers.",
            recommendation="Add descriptive alt text, or alt=\"\" (with role=presentation) for decorative images.",
            element=imgs[0].get("el"),
            evidence={"images": imgs},
            priority=2,
        )

    if audit.get("h1Count", 0) == 0:
        add(
            category="accessibility",
            severity="medium",
            title="Page has no H1 heading",
            description="Every page should have exactly one top-level H1 describing its main content.",
            recommendation="Add a single, descriptive <h1> to the page.",
            evidence={"headings": audit.get("headings", [])[:10]},
            priority=3,
        )
    elif audit.get("h1Count", 0) > 1:
        add(
            category="accessibility",
            severity="low",
            title=f"Page has {audit['h1Count']} H1 headings",
            description="Multiple H1s dilute the document outline for assistive technology.",
            recommendation="Use a single H1 and demote the rest to H2/H3.",
            evidence={"h1Count": audit["h1Count"]},
            priority=4,
        )

    skips = audit.get("headingSkips") or []
    if skips:
        add(
            category="accessibility",
            severity="low",
            title=f"Heading levels skipped {len(skips)} time(s)",
            description="Jumping heading levels (e.g. H2 → H4) breaks the document outline.",
            recommendation="Increase heading levels one step at a time.",
            evidence={"skips": skips},
            priority=4,
        )

    dupes = audit.get("duplicateIds") or []
    if dupes:
        add(
            category="accessibility",
            severity="medium",
            title=f"{len(dupes)} duplicate element ID(s)",
            description="Duplicate IDs break label associations, anchors, and scripting.",
            recommendation="Make every id unique within the document.",
            evidence={"ids": dupes},
            priority=3,
        )

    unlabeled = audit.get("unlabeled") or []
    if unlabeled:
        add(
            category="accessibility",
            severity="high",
            title=f"{len(unlabeled)} form field(s) without a label",
            description="Inputs without an associated label are unusable with screen readers.",
            recommendation="Associate a <label for>, wrap the control, or add aria-label.",
            element=unlabeled[0].get("el"),
            evidence={"fields": unlabeled},
            priority=2,
        )

    nameless = audit.get("namelessControls") or []
    if nameless:
        add(
            category="accessibility",
            severity="high",
            title=f"{len(nameless)} button/link(s) without an accessible name",
            description="Controls with no text or aria-label are announced as 'button'/'link' with no context.",
            recommendation="Add visible text, aria-label, or alt text on the child image.",
            element=nameless[0].get("el"),
            evidence={"controls": nameless},
            priority=2,
        )

    bad_roles = audit.get("badRoles") or []
    if bad_roles:
        add(
            category="accessibility",
            severity="medium",
            title=f"{len(bad_roles)} invalid ARIA role(s)",
            description="Unrecognized ARIA roles are ignored, removing intended semantics.",
            recommendation="Use a valid WAI-ARIA role or a native element.",
            evidence={"roles": bad_roles},
            priority=3,
        )

    meta = audit.get("meta") or {}
    if not meta.get("lang"):
        add(
            category="accessibility",
            severity="medium",
            title="Missing <html lang> attribute",
            description="Screen readers use the lang attribute to select the correct pronunciation.",
            recommendation='Add a lang attribute, e.g. <html lang="en">.',
            priority=3,
        )

    # --- Responsive ---
    if audit.get("overflow"):
        add(
            category="responsive",
            severity="medium",
            title="Horizontal overflow detected",
            description=(
                f"Content is wider than the viewport "
                f"({audit.get('scrollWidth')}px vs {audit.get('innerWidth')}px), causing sideways scroll."
            ),
            recommendation="Constrain wide elements (images, tables, pre) with max-width:100% and overflow handling.",
            evidence={"scrollWidth": audit.get("scrollWidth"), "innerWidth": audit.get("innerWidth")},
            priority=3,
        )

    if not meta.get("viewport"):
        add(
            category="responsive",
            severity="high",
            title="Missing viewport meta tag",
            description="Without a viewport meta tag, mobile browsers render at desktop width and zoom out.",
            recommendation='Add <meta name="viewport" content="width=device-width, initial-scale=1">.',
            priority=2,
        )

    # --- SEO / metadata ---
    if not meta.get("title"):
        add(
            category="seo",
            severity="high",
            title="Missing <title>",
            description="The page has no title, hurting search ranking and browser tabs/bookmarks.",
            recommendation="Add a concise, unique <title> (50–60 characters).",
            priority=2,
        )
    elif len(meta["title"]) > 65:
        add(
            category="seo",
            severity="low",
            title="Title tag is long",
            description=f"The title is {len(meta['title'])} characters and may be truncated in search results.",
            recommendation="Keep titles under ~60 characters.",
            evidence={"title": meta["title"]},
            priority=4,
        )

    if not meta.get("description"):
        add(
            category="seo",
            severity="medium",
            title="Missing meta description",
            description="No meta description; search engines will synthesize a snippet.",
            recommendation="Add a 120–160 character meta description summarizing the page.",
            priority=3,
        )

    if not meta.get("canonical"):
        add(
            category="seo",
            severity="low",
            title="Missing canonical link",
            description="Without a canonical URL, duplicate-content variants may compete in search.",
            recommendation='Add <link rel="canonical" href="..."> pointing to the preferred URL.',
            priority=4,
        )

    if meta.get("ogCount", 0) == 0:
        add(
            category="seo",
            severity="low",
            title="No OpenGraph tags",
            description="Missing og: tags produce poor link previews when shared on social platforms.",
            recommendation="Add og:title, og:description, og:image, and og:url.",
            priority=4,
        )

    return out
