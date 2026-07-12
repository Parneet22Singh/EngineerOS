"""Autonomous interaction explorer.

Loads the entry page and drives it like a QA tester would: discovers interactive
elements, opens menus, clicks buttons, fills & submits forms, and reacts to dialogs —
recording runtime errors, broken interactions, un-dismissable modals, and validation
gaps along the way. Every interaction is wrapped so a single misbehaving control can
never sink the run.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from playwright.async_api import Browser, Error as PlaywrightError

from app.modules.website_intelligence.crawler import VIEWPORTS
from app.modules.website_intelligence.page_audit import AUDIT_JS, findings_from_audit
from app.modules.website_intelligence.results import PageResult, RawFinding, Screenshot

logger = logging.getLogger("engineeros.qa.explorer")

ProgressCb = Callable[[str, float, str], Awaitable[None]]

# Synthetic-but-plausible values used to fill form fields by input type.
FIELD_VALUES = {
    "email": "qa.tester@example.com",
    "tel": "+15551234567",
    "phone": "+15551234567",
    "number": "42",
    "url": "https://example.com",
    "password": "Passw0rd!23",
    "date": "2025-01-01",
    "datetime-local": "2025-01-01T09:00",
    "month": "2025-01",
    "week": "2025-W01",
    "time": "09:00",
    "search": "EngineerOS QA",
    "color": "#6366f1",
}
DEFAULT_VALUE = "EngineerOS QA test"


@dataclass(slots=True)
class Flow:
    action: str
    label: str
    result: str
    issue: bool = False


# Discovers interactive elements and tags each with a stable data-eos-id handle.
INTERACT_JS = r"""
() => {
  let n = 0;
  const tag = (el) => { const id = 'eos' + (n++); el.setAttribute('data-eos-id', id); return id; };
  const clip = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 60);
  const label = (el) =>
    clip(el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') ||
         el.getAttribute('name') || el.value || el.tagName);
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 1 && r.height > 1 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };

  const buttons = [], menus = [], forms = [];
  const seen = new Set();

  document.querySelectorAll('button, [role="button"], input[type="button"], input[type="submit"], a[href^="#"], a[role="button"]').forEach((el) => {
    if (!visible(el) || seen.has(el)) return;
    seen.add(el);
    const expandable = el.hasAttribute('aria-expanded') || el.hasAttribute('aria-haspopup') ||
                       /menu|dropdown|toggle|hamburger|nav/i.test(el.className + ' ' + (el.getAttribute('aria-label') || ''));
    const rec = { id: tag(el), label: label(el), tag: el.tagName.toLowerCase(), expandable,
                  expanded: el.getAttribute('aria-expanded') };
    (expandable ? menus : buttons).push(rec);
  });

  document.querySelectorAll('form').forEach((f) => {
    if (!visible(f)) return;
    const fields = [];
    f.querySelectorAll('input, select, textarea').forEach((inp) => {
      const type = (inp.getAttribute('type') || inp.tagName).toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image', 'file'].includes(type)) return;
      fields.push({ id: tag(inp), type, required: !!inp.required, name: inp.name || inp.id || '' });
    });
    if (fields.length === 0) return;
    const submit = f.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
    forms.push({
      id: tag(f),
      label: clip(f.getAttribute('name') || f.getAttribute('id') || f.getAttribute('action') || 'form'),
      fields,
      submitId: submit ? tag(submit) : null,
      anyRequired: fields.some((x) => x.required),
      noValidate: f.hasAttribute('novalidate'),
    });
  });

  return { buttons, menus, forms };
}
"""

# Detects any currently-visible modal/dialog.
MODAL_JS = r"""
() => {
  const sels = ['[role="dialog"]', '[role="alertdialog"]', '[aria-modal="true"]', 'dialog[open]', '.modal.show', '.modal.open', '.modal[style*="display: block"]'];
  for (const sel of sels) {
    for (const el of document.querySelectorAll(sel)) {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      if (r.width > 1 && r.height > 1 && s.display !== 'none' && s.visibility !== 'hidden') {
        const closer = el.querySelector('[aria-label*="close" i], .close, [data-dismiss], button[class*="close" i]');
        return { open: true, hasCloser: !!closer, label: (el.getAttribute('aria-label') || el.textContent || '').replace(/\s+/g,' ').trim().slice(0, 50) };
      }
    }
  }
  return { open: false };
}
"""


class Explorer:
    def __init__(
        self,
        browser: Browser,
        *,
        artifacts_dir,
        scan_id: str,
        entry_url: str,
        timeout_ms: int,
        max_actions: int,
        progress_cb: ProgressCb | None = None,
    ) -> None:
        self._browser = browser
        self._artifacts_dir = artifacts_dir
        self._scan_id = scan_id
        self._entry_url = entry_url
        self._timeout_ms = timeout_ms
        self._max_actions = max_actions
        self._progress_cb = progress_cb
        self._shot_seq = 0

    async def _progress(self, stage: str, progress: float, detail: str = "") -> None:
        if self._progress_cb:
            await self._progress_cb(stage, progress, detail)

    async def _shot(self, page, viewport: str, label: str, result: PageResult) -> None:
        self._shot_seq += 1
        rel = f"{self._scan_id}/screenshots/{viewport}_{self._shot_seq}.png"
        dest = self._artifacts_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(dest), full_page=(viewport == "desktop"), timeout=15000)
            w, h = VIEWPORTS[viewport]
            result.screenshots.append(Screenshot(viewport=viewport, path=rel, width=w, height=h, label=label))
        except PlaywrightError as exc:
            logger.info("screenshot failed (%s): %r", label, exc)

    async def explore(self) -> tuple[PageResult, list[Flow], dict]:
        result = PageResult(url=self._entry_url, depth=0)
        flows: list[Flow] = []
        stats: dict = {"counts": {"buttons": 0, "menus": 0, "forms": 0}, "actions_performed": 0}
        context = await self._browser.new_context(
            viewport={"width": VIEWPORTS["desktop"][0], "height": VIEWPORTS["desktop"][1]},
            ignore_https_errors=True,
            user_agent="EngineerOS-AutonomousQA/0.1 (+https://engineeros.dev)",
        )
        console_errors: list[dict] = []
        dialogs: list[dict] = []

        page = await context.new_page()
        page.on("console", lambda m: console_errors.append({"type": m.type, "text": m.text[:400]})
                if m.type == "error" else None)

        async def on_dialog(dialog) -> None:
            dialogs.append({"type": dialog.type, "message": dialog.message[:200]})
            try:
                await dialog.dismiss()
            except PlaywrightError:
                pass

        page.on("dialog", lambda d: asyncio.create_task(on_dialog(d)))

        try:
            await self._progress("qa:load", 0.1, self._entry_url)
            start = time.monotonic()
            resp = await page.goto(self._entry_url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except PlaywrightError:
                pass
            result.load_ms = int((time.monotonic() - start) * 1000)
            result.status_code = resp.status if resp else None
            result.final_url = page.url

            # Baseline audit (reuse Module 1's DOM/a11y/SEO checks).
            await self._progress("qa:audit", 0.2, "Auditing entry page")
            audit = await page.evaluate(AUDIT_JS)
            result.title = (audit.get("meta") or {}).get("title", "")
            result.meta = audit.get("meta") or {}
            result.findings.extend(findings_from_audit(self._entry_url, audit))

            # Entry screenshots at three viewports.
            for name in ("desktop", "tablet", "mobile"):
                w, h = VIEWPORTS[name]
                await page.set_viewport_size({"width": w, "height": h})
                await asyncio.sleep(0.2)
                await self._shot(page, name, f"entry ({name})", result)
            await page.set_viewport_size({"width": VIEWPORTS["desktop"][0], "height": VIEWPORTS["desktop"][1]})

            # Discover interactive elements.
            await self._progress("qa:discover", 0.3, "Discovering interactive elements")
            catalog = await page.evaluate(INTERACT_JS)
            n_buttons = len(catalog["buttons"])
            n_menus = len(catalog["menus"])
            n_forms = len(catalog["forms"])
            logger.info("QA discovered %d buttons, %d menus, %d forms", n_buttons, n_menus, n_forms)

            actions = 0

            # --- Menus / expanders ---
            for menu in catalog["menus"][:6]:
                if actions >= self._max_actions:
                    break
                actions += 1
                await self._progress("qa:interact", 0.35 + 0.5 * actions / max(self._max_actions, 1),
                                     f"Menu: {menu['label']}")
                flows.append(await self._test_menu(page, menu, result, console_errors))

            # --- Buttons ---
            for btn in catalog["buttons"][:10]:
                if actions >= self._max_actions:
                    break
                actions += 1
                await self._progress("qa:interact", 0.35 + 0.5 * actions / max(self._max_actions, 1),
                                     f"Button: {btn['label']}")
                flows.append(await self._test_button(page, btn, result, console_errors))

            # --- Forms ---
            for form in catalog["forms"][:4]:
                if actions >= self._max_actions:
                    break
                actions += 1
                await self._progress("qa:interact", 0.35 + 0.5 * actions / max(self._max_actions, 1),
                                     f"Form: {form['label']}")
                sub = await self._test_form(page, form, result, console_errors)
                flows.extend(sub)

            # --- Dialogs seen during exploration ---
            if dialogs:
                result.findings.append(
                    RawFinding(
                        category="interaction",
                        severity="info",
                        title=f"{len(dialogs)} native dialog(s) triggered",
                        page_url=self._entry_url,
                        description="Native alert/confirm/prompt dialogs interrupt the user and block automation.",
                        recommendation="Prefer non-blocking in-page notifications over native dialogs.",
                        evidence={"dialogs": dialogs[:10]},
                        priority=4,
                    )
                )

            # --- Global console errors observed across the session ---
            if console_errors:
                result.findings.append(
                    RawFinding(
                        category="console",
                        severity="medium",
                        title=f"{len(console_errors)} JavaScript error(s) during exploration",
                        page_url=self._entry_url,
                        description="Console errors surfaced while loading or interacting with the page.",
                        recommendation="Resolve the logged runtime errors; they often indicate broken features.",
                        evidence={"errors": console_errors[:20]},
                        priority=3,
                    )
                )

            stats = {
                "counts": {"buttons": n_buttons, "menus": n_menus, "forms": n_forms},
                "actions_performed": actions,
            }
            result.console_errors = console_errors

        except PlaywrightError as exc:
            result.error = str(exc)[:400]
            result.findings.append(
                RawFinding(
                    category="crawl",
                    severity="high",
                    title="Entry page failed to load",
                    page_url=self._entry_url,
                    description=f"Navigation failed: {result.error}",
                    recommendation="Verify the URL is reachable within the timeout.",
                    priority=2,
                )
            )
        finally:
            await context.close()

        return result, flows, stats

    async def _test_menu(self, page, menu, result: PageResult, console_before: list) -> Flow:
        sel = f'[data-eos-id="{menu["id"]}"]'
        try:
            before = len(console_before)
            expanded_before = await page.get_attribute(sel, "aria-expanded")
            await page.click(sel, timeout=4000)
            await asyncio.sleep(0.4)
            expanded_after = await page.get_attribute(sel, "aria-expanded")
            modal = await page.evaluate(MODAL_JS)
            errs = len(console_before) - before

            if modal.get("open"):
                await self._shot(page, "desktop", f"opened: {menu['label']}", result)
                closed = await self._try_close_modal(page)
                if not closed:
                    result.findings.append(
                        RawFinding(
                            category="interaction",
                            severity="medium",
                            title=f"Modal cannot be dismissed: “{menu['label']}”",
                            page_url=self._entry_url,
                            description="A modal/dialog opened but did not close via Escape or a close control.",
                            recommendation="Ensure every modal is dismissible with Escape and a labelled close button.",
                            element=sel,
                            priority=3,
                        )
                    )
                    await page.goto(self._entry_url, wait_until="domcontentloaded")
                    return Flow("open menu", menu["label"], "modal not dismissable", issue=True)
                return Flow("open menu", menu["label"], "modal opened & closed")

            if menu.get("expanded") is not None and expanded_before == expanded_after:
                result.findings.append(
                    RawFinding(
                        category="interaction",
                        severity="low",
                        title=f"Expander does not toggle aria-expanded: “{menu['label']}”",
                        page_url=self._entry_url,
                        description="An element exposing aria-expanded did not update it when activated.",
                        recommendation="Toggle aria-expanded on activation so assistive tech reflects the state.",
                        element=sel,
                        priority=4,
                    )
                )
                return Flow("open menu", menu["label"], "aria-expanded did not change", issue=True)

            if errs:
                return Flow("open menu", menu["label"], f"{errs} console error(s)", issue=True)
            return Flow("open menu", menu["label"], "ok")
        except PlaywrightError as exc:
            return Flow("open menu", menu["label"], f"error: {str(exc)[:40]}", issue=True)

    async def _test_button(self, page, btn, result: PageResult, console_before: list) -> Flow:
        sel = f'[data-eos-id="{btn["id"]}"]'
        try:
            before = len(console_before)
            url_before = page.url
            await page.click(sel, timeout=4000)
            await asyncio.sleep(0.35)
            errs = len(console_before) - before

            if page.url != url_before:
                # Button navigated; note it and return to entry to keep exploring.
                await page.goto(self._entry_url, wait_until="domcontentloaded")
                return Flow("click button", btn["label"], "navigated")

            modal = await page.evaluate(MODAL_JS)
            if modal.get("open"):
                await self._shot(page, "desktop", f"opened: {btn['label']}", result)
                closed = await self._try_close_modal(page)
                if not closed:
                    result.findings.append(
                        RawFinding(
                            category="interaction",
                            severity="medium",
                            title=f"Modal cannot be dismissed: “{btn['label']}”",
                            page_url=self._entry_url,
                            description="A modal opened on click but did not close via Escape or a close control.",
                            recommendation="Ensure every modal is dismissible with Escape and a labelled close button.",
                            element=sel,
                            priority=3,
                        )
                    )
                    await page.goto(self._entry_url, wait_until="domcontentloaded")
                    return Flow("click button", btn["label"], "modal not dismissable", issue=True)

            if errs:
                result.findings.append(
                    RawFinding(
                        category="interaction",
                        severity="high",
                        title=f"Button triggers JavaScript error: “{btn['label']}”",
                        page_url=self._entry_url,
                        description="Clicking this control produced one or more console errors.",
                        recommendation="Fix the handler so the interaction completes without runtime errors.",
                        element=sel,
                        evidence={"errors": console_before[-errs:]},
                        priority=2,
                    )
                )
                return Flow("click button", btn["label"], f"{errs} console error(s)", issue=True)
            return Flow("click button", btn["label"], "ok")
        except PlaywrightError as exc:
            return Flow("click button", btn["label"], f"error: {str(exc)[:40]}", issue=True)

    async def _test_form(self, page, form, result: PageResult, console_before: list) -> list[Flow]:
        flows: list[Flow] = []
        try:
            # Fill each field with a plausible value.
            filled = 0
            for fld in form["fields"]:
                fsel = f'[data-eos-id="{fld["id"]}"]'
                value = FIELD_VALUES.get(fld["type"], DEFAULT_VALUE)
                try:
                    if fld["type"] in ("checkbox", "radio"):
                        await page.check(fsel, timeout=2000)
                    elif fld["type"] == "select" or fld["type"] == "select-one":
                        continue
                    else:
                        await page.fill(fsel, value, timeout=2000)
                    filled += 1
                except PlaywrightError:
                    pass
            flows.append(Flow("fill form", form["label"], f"filled {filled}/{len(form['fields'])} fields"))

            # Native-validation posture.
            if form["anyRequired"] and form["noValidate"]:
                result.findings.append(
                    RawFinding(
                        category="forms",
                        severity="low",
                        title=f"Form disables native validation: “{form['label']}”",
                        page_url=self._entry_url,
                        description="The form has required fields but uses novalidate, bypassing built-in checks.",
                        recommendation="Keep native validation, or ensure equivalent accessible client-side validation.",
                        element=f'[data-eos-id="{form["id"]}"]',
                        priority=4,
                    )
                )
            if not form["anyRequired"] and len(form["fields"]) >= 2:
                result.findings.append(
                    RawFinding(
                        category="forms",
                        severity="info",
                        title=f"Form has no required fields: “{form['label']}”",
                        page_url=self._entry_url,
                        description="No field is marked required; the form can be submitted empty.",
                        recommendation="Mark genuinely required fields with the required attribute.",
                        element=f'[data-eos-id="{form["id"]}"]',
                        priority=5,
                    )
                )

            # Attempt submission and watch for runtime errors (return to entry after).
            if form["submitId"]:
                ssel = f'[data-eos-id="{form["submitId"]}"]'
                before = len(console_before)
                url_before = page.url
                try:
                    await page.click(ssel, timeout=4000)
                    await asyncio.sleep(0.5)
                except PlaywrightError:
                    pass
                errs = len(console_before) - before
                navigated = page.url != url_before
                if errs:
                    result.findings.append(
                        RawFinding(
                            category="forms",
                            severity="medium",
                            title=f"Form submission triggers JS error: “{form['label']}”",
                            page_url=self._entry_url,
                            description="Submitting the form produced console errors.",
                            recommendation="Handle submission failures gracefully and fix the underlying error.",
                            element=ssel,
                            evidence={"errors": console_before[-errs:]},
                            priority=3,
                        )
                    )
                    flows.append(Flow("submit form", form["label"], f"{errs} console error(s)", issue=True))
                else:
                    flows.append(Flow("submit form", form["label"], "navigated" if navigated else "submitted"))
                if navigated:
                    await page.goto(self._entry_url, wait_until="domcontentloaded")
            return flows
        except PlaywrightError as exc:
            flows.append(Flow("test form", form["label"], f"error: {str(exc)[:40]}", issue=True))
            return flows

    async def _try_close_modal(self, page) -> bool:
        """Attempt to dismiss a visible modal via Escape, then a close control."""
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
            if not (await page.evaluate(MODAL_JS)).get("open"):
                return True
            closer = page.locator(
                '[role="dialog"] [aria-label*="close" i], [aria-modal="true"] [aria-label*="close" i], '
                '.modal .close, [data-dismiss], button[class*="close" i]'
            ).first
            if await closer.count():
                await closer.click(timeout=2000)
                await asyncio.sleep(0.3)
                return not (await page.evaluate(MODAL_JS)).get("open")
        except PlaywrightError:
            return False
        return False
