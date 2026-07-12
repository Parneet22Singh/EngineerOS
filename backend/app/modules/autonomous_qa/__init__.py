"""Module 6 — Autonomous QA Agent.

Given only a URL, autonomously explores the page: opens menus, clicks buttons, fills
and submits forms, detects dialogs/modals, and watches for runtime errors — then
produces an enterprise QA report. Builds on Module 1's shared audit primitives.
"""
from app.modules.autonomous_qa.module import AutonomousQAModule

MODULE = AutonomousQAModule
